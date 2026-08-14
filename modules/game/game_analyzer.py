"""
棋局分析器模块

功能：
- 分析棋局记录，找出玩家失误
- 调用 LLM 生成教学建议
- 提供改进方案
"""

from typing import List, Dict, Optional
from modules.game.xiangqi_engine import XiangqiEngine, Side, PieceType
from modules.llm_module import LLMClient


class GameAnalyzer:
    """棋局分析器"""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def analyze_game(self, engine: XiangqiEngine,
                     player_side: Side = Side.RED) -> Dict:
        """
        分析整局游戏

        返回:
        {
            "winner": "red" | "black",
            "player_mistakes": [...],
            "key_moments": [...],
            "strategic_advice": "...",
            "tactical_tips": [...]
        }
        """
        history = engine.move_history
        if not history:
            return {"error": "没有走棋记录"}

        # 1. 找出关键失误
        mistakes = self._find_mistakes(engine, player_side)

        # 2. 识别关键转折点
        key_moments = self._find_key_moments(engine, player_side)

        # 3. 生成 LLM 分析
        analysis_result = self._generate_llm_analysis(
            history, mistakes, key_moments, engine, player_side
        )

        return {
            "winner": engine.winner.value if engine.winner else None,
            "total_moves": len(history),
            "player_mistakes": mistakes,
            "key_moments": key_moments,
            "analysis": analysis_result
        }

    def _find_mistakes(self, engine: XiangqiEngine,
                       player_side: Side) -> List[Dict]:
        """找出玩家的失误走法"""
        mistakes = []
        history = engine.move_history

        # 重新回放棋局来分析
        test_engine = XiangqiEngine()

        for idx, move in enumerate(history):
            # 跳过非玩家走法
            if move.piece.side != player_side:
                # AI 走法，直接执行
                test_engine.make_move(
                    move.from_pos[0], move.from_pos[1],
                    move.to_pos[0], move.to_pos[1]
                )
                continue

            # 评估玩家走法
            error = self._evaluate_move(test_engine, move, player_side)
            if error:
                mistakes.append({
                    "move_number": idx + 1,
                    "move": f"{move.piece.name} {self._pos_str(move.from_pos)} → {self._pos_str(move.to_pos)}",
                    "mistake_type": error["type"],
                    "description": error["description"],
                    "suggestion": error.get("suggestion", "")
                })

            # 执行走法继续回放
            test_engine.make_move(
                move.from_pos[0], move.from_pos[1],
                move.to_pos[0], move.to_pos[1]
            )

        return mistakes

    def _evaluate_move(self, engine: XiangqiEngine, move,
                       player_side: Side) -> Optional[Dict]:
        """评估单步走法的质量"""
        score = self._get_move_score(engine, move, player_side)

        if score <= 50:  # 严重失误
            return {
                "type": "blunder",
                "description": self._describe_blunder(move, engine),
                "suggestion": self._suggest_better_move(engine, move, player_side)
            }
        elif score <= 100:  # 一般失误
            return {
                "type": "mistake",
                "description": self._describe_mistake(move, engine),
                "suggestion": self._suggest_better_move(engine, move, player_side)
            }
        elif score <= 150:  # 小问题
            return {
                "type": "inaccuracy",
                "description": self._describe_inaccuracy(move, engine),
                "suggestion": ""
            }

        return None

    def _get_move_score(self, engine: XiangqiEngine, move,
                        player_side: Side) -> float:
        """评估走法得分（分数越高越好）"""
        score = 200.0  # 默认中等走法

        # 检查是否送子
        if move.captured:
            piece_values = {
                PieceType.KING: 10000,
                PieceType.CHARIOT: 900,
                PieceType.HORSE: 400,
                PieceType.CANNON: 450,
                PieceType.ADVISOR: 200,
                PieceType.ELEPHANT: 200,
                PieceType.PAWN: 100,
            }
            # 吃子加分
            score += piece_values.get(move.captured.piece_type, 0) * 0.3
        else:
            # 检查是否会被吃
            test_engine = copy.deepcopy(engine)
            # 这里简化处理
            pass

        return score

    def _describe_blunder(self, move, engine: XiangqiEngine) -> str:
        """描述严重失误"""
        descriptions = [
            f"⚠️ 严重失误：{move.piece.name}走法不佳",
            f"❌ 大失误：{move.piece.name}的走法过于急躁",
            f"🚫 致命失误：{move.piece.name}暴露了防守漏洞",
        ]
        return descriptions[len(move.from_pos) % len(descriptions)]

    def _describe_mistake(self, move, engine: XiangqiEngine) -> str:
        """描述一般失误"""
        descriptions = [
            f"⚠️ 失误：{move.piece.name}走法不够精准",
            f"💡 可改进：{move.piece.name}的走法可以更优",
            f"📌 小问题：{move.piece.name}的位置选择不理想",
        ]
        return descriptions[len(move.from_pos) % len(descriptions)]

    def _describe_inaccuracy(self, move, engine: XiangqiEngine) -> str:
        """描述小问题"""
        descriptions = [
            f"💡 可以更好：{move.piece.name}有更优的走法选择",
            f"📝 小瑕疵：{move.piece.name}的走法较为保守",
            f"🔍 值得思考：{move.piece.name}的走法可以更具攻击性",
        ]
        return descriptions[len(move.from_pos) % len(descriptions)]

    def _suggest_better_move(self, engine: XiangqiEngine, move,
                              player_side: Side) -> str:
        """建议更好的走法"""
        piece = move.piece
        valid_moves = engine.get_valid_moves(move.from_pos[0], move.from_pos[1])

        if not valid_moves:
            return "当前位置无其他合法走法"

        # 选择一个更好的走法（简化版）
        better_moves = [m for m in valid_moves if m != move.to_pos]
        if better_moves:
            suggestions = {
                PieceType.CHARIOT: "车应该放在更活跃的位置，控制更多区域",
                PieceType.HORSE: "马可以跳到更有威胁的位置",
                PieceType.CANNON: "炮应该寻找更好的炮架位置",
                PieceType.KING: "将/帅应该留在安全的位置",
                PieceType.ADVISOR: "士/仕应该紧密保护将/帅",
                PieceType.ELEPHANT: "象/相应该保持中路防守",
                PieceType.PAWN: "兵/卒应该考虑过河进攻",
            }
            tip = suggestions.get(piece.piece_type, "")
            return f"建议尝试 {self._pos_str(better_moves[0])}，{tip}"

        return ""

    def _find_key_moments(self, engine: XiangqiEngine,
                           player_side: Side) -> List[Dict]:
        """找出关键转折点"""
        key_moments = []
        history = engine.move_history

        for idx, move in enumerate(history):
            # 吃对方大子是关键时刻
            if move.captured and move.captured.piece_type in \
               (PieceType.KING, PieceType.CHARIOT):
                key_moments.append({
                    "move_number": idx + 1,
                    "description": f"{'红方' if move.piece.side == Side.RED else '黑方'}"
                                   f"{move.piece.name}吃了对方{move.captured.name}",
                    "importance": "high"
                })

        return key_moments

    def _generate_llm_analysis(self, history, mistakes, key_moments,
                                engine: XiangqiEngine,
                                player_side: Side) -> str:
        """调用 LLM 生成分析报告"""
        if not self.llm:
            return self._fallback_analysis(mistakes, key_moments, engine,
                                            player_side)

        # 准备棋局摘要
        game_summary = self._build_game_summary(history, mistakes, engine,
                                                player_side)

        prompt = f"""你是一位专业的象棋教练。请根据以下棋局记录，为玩家提供详细的分析和教学建议。

{game_summary}

请用以下格式回复：
1. 整体评价：对玩家这局棋的整体表现进行评分（1-10分）
2. 关键失误分析：详细说明玩家的主要失误及改进方法
3. 战术建议：针对玩家的走法给出具体的战术改进建议
4. 战略建议：从战略层面分析玩家的布局和进攻策略
5. 下一步学习方向：建议玩家应该重点学习的象棋知识

要求：
- 语言亲切易懂，像一位耐心的教练
- 重点突出，不要太学术化
- 给出具体的走法示例
- 鼓励玩家继续练习"""

        try:
            messages = [
                {"role": "system", "content": "你是一位专业的中国象棋教练，擅长分析棋局并给予鼓励式教学。"},
                {"role": "user", "content": prompt}
            ]
            response = self.llm.chat(messages, timeout=60)
            return response
        except Exception as e:
            logger = __import__('logging').getLogger(__name__)
            logger.error(f"LLM 分析失败: {e}")
            return self._fallback_analysis(mistakes, key_moments, engine,
                                            player_side)

    def _build_game_summary(self, history, mistakes, engine: XiangqiEngine,
                            player_side: Side) -> str:
        """构建棋局摘要"""
        total_moves = len(history)
        player_moves = [m for m in history if m.piece.side == player_side]
        captured_count = len([m for m in history
                              if m.captured and m.piece.side == player_side])

        lines = [
            f"## 棋局概况",
            f"- 总步数：{total_moves}",
            f"- 玩家走法数：{len(player_moves)}",
            f"- 玩家吃子数：{captured_count}",
            f"- 对局结果：{'红方胜' if engine.winner == Side.RED else '黑方胜' if engine.winner else '平局'}",
            "",
            f"## 玩家失误（{len(mistakes)}处）",
        ]

        if mistakes:
            for m in mistakes[:5]:  # 最多显示5个
                lines.append(f"- 第{m['move_number']}步：{m['description']}")
                if m.get('suggestion'):
                    lines.append(f"  建议：{m['suggestion']}")
        else:
            lines.append("暂无明显失误，表现不错！")

        lines.extend([
            "",
            f"## 关键时刻",
        ])

        # 玩家走法摘要
        lines.append("\n## 玩家关键走法：")
        for i, move in enumerate(player_moves[-5:], len(player_moves) - 4):
            lines.append(f"  第{i+1}步：{move.piece.name} "
                         f"{self._pos_str(move.from_pos)} → {self._pos_str(move.to_pos)}"
                         f"{'（吃' + move.captured.name + '）' if move.captured else ''}")

        return "\n".join(lines)

    def _fallback_analysis(self, mistakes, key_moments, engine: XiangqiEngine,
                            player_side: Side) -> str:
        """LLM 不可用时的兜底分析"""
        lines = [
            "📊 棋局分析报告",
            "",
            f"总步数：{len(engine.move_history)}",
            f"结果：{'红方胜' if engine.winner == Side.RED else '黑方胜' if engine.winner else '未结束'}",
            "",
        ]

        if mistakes:
            lines.append("⚠️ 主要失误：")
            for m in mistakes:
                lines.append(f"  第{m['move_number']}步：{m['description']}")
                if m.get('suggestion'):
                    lines.append(f"    💡 {m['suggestion']}")
            lines.append("")

        lines.extend([
            "💡 改进建议：",
            "1. 多练习基本走法，熟悉各棋子的移动规则",
            "2. 培养全局观，走子前考虑对手的应对",
            "3. 注意保护重要棋子（车、马、炮）",
            "4. 学会利用炮的隔子吃子特性",
            "5. 保持将/帅的安全位置",
            "",
            "🎯 下一步：继续练习！多下多练才能进步。",
        ])

        return "\n".join(lines)

    def _pos_str(self, pos: tuple) -> str:
        """坐标转字符串"""
        col_map = "一二三四五六七八九"
        col, row = pos
        return f"({col_map[col] if col < 9 else '十'},{row + 1})"


# 导入 copy
import copy
