"""
象棋游戏对话框

功能：
- 完整的象棋对局界面
- 玩家 vs AI 对战
- 实时显示走棋记录
- 显示AI难度等级和渐进式提升
- 对局结束后 AI 分析教学
"""

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QTextEdit, QFrame, QMessageBox, QSizePolicy
)

from modules.game.xiangqi_engine import XiangqiEngine, Side, PieceType, DifficultyLevel
from modules.game.game_analyzer import GameAnalyzer
from ui.xiangqi_board import XiangqiBoard


# AI 走子线程
class AIPlayerThread(QThread):
    move_ready = pyqtSignal(tuple)  # (from_col, from_row, to_col, to_row)
    error_occurred = pyqtSignal(str)

    def __init__(self, engine: XiangqiEngine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            move = self.engine.get_ai_move()
            if move:
                self.move_ready.emit(move)
            else:
                self.error_occurred.emit("AI 无法走子")
        except Exception as e:
            self.error_occurred.emit(str(e))


# 分析线程
class AnalysisWorker(QThread):
    analysis_ready = pyqtSignal(dict)

    def __init__(self, analyzer: GameAnalyzer, engine: XiangqiEngine,
                 player_side: Side):
        super().__init__()
        self.analyzer = analyzer
        self.engine = engine
        self.player_side = player_side

    def run(self):
        try:
            result = self.analyzer.analyze_game(self.engine, self.player_side)
            self.analysis_ready.emit(result)
        except Exception as e:
            self.analysis_ready.emit({"error": str(e)})


class XiangqiGameDialog(QDialog):
    """象棋游戏对话框"""

    def __init__(self, llm, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.engine = XiangqiEngine(difficulty=1)  # 初始难度1
        self.analyzer = GameAnalyzer(llm)
        self.ai_thread = None
        self.analysis_thread = None
        
        # 累计胜利次数（跨局保持）
        self.total_wins = 0

        self._init_ui()
        self._setup_connections()

        # 游戏状态
        self._game_active = True
        self._player_turn = True

    def _init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("中国象棋 - AI 教练对战")
        self.setMinimumSize(900, 700)
        self.resize(1000, 750)

        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2d1b4e, stop:0.5 #1a1a3e, stop:1 #0f0f2f);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(20, 20, 20, 20)

        # 左侧：棋盘区域
        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)

        # 标题
        title_label = QLabel("🎮 中国象棋")
        title_label.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffd700; padding: 5px;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_panel.addWidget(title_label)

        # 回合指示
        self.turn_label = QLabel("红方（你）走棋")
        self.turn_label.setStyleSheet(
            "color: #ff6b6b; font-size: 14px; padding: 5px;"
            "background: rgba(255,107,107,0.15); border-radius: 8px;"
        )
        self.turn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_panel.addWidget(self.turn_label)

        # 棋盘
        self.board = XiangqiBoard()
        self.board.set_engine(self.engine)
        board_container = QFrame()
        board_container.setStyleSheet(
            "QFrame { background: #d4a85a; border-radius: 8px; }"
        )
        board_layout = QVBoxLayout(board_container)
        board_layout.setContentsMargins(4, 4, 4, 4)
        board_layout.addWidget(self.board, 1)
        left_panel.addWidget(board_container, 1)

        # 操作按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_restart = QPushButton("🔄 重新开始")
        self.btn_restart.setStyleSheet(self._btn_style("#4f8cff", "#3a6fd6"))

        self.btn_give_up = QPushButton("🏳 认输")
        self.btn_give_up.setStyleSheet(self._btn_style("#d65c7c", "#b2445e"))

        self.btn_analyze = QPushButton("📊 AI 分析")
        self.btn_analyze.setStyleSheet(self._btn_style("#7ee7a8", "#5bc98c"))
        self.btn_analyze.setEnabled(False)

        btn_layout.addWidget(self.btn_restart)
        btn_layout.addWidget(self.btn_give_up)
        btn_layout.addWidget(self.btn_analyze)

        left_panel.addLayout(btn_layout)
        layout.addLayout(left_panel, 4)

        # 右侧：信息面板
        right_panel = QVBoxLayout()
        right_panel.setSpacing(10)

        # 难度等级显示
        difficulty_frame = QFrame()
        difficulty_frame.setStyleSheet(
            "QFrame { background: rgba(255,215,0,0.1); border-radius: 10px;"
            " border: 1px solid rgba(255,215,0,0.3); }"
        )
        difficulty_layout = QVBoxLayout(difficulty_frame)
        difficulty_layout.setContentsMargins(12, 12, 12, 12)

        self.difficulty_title = QLabel("⚡ AI 难度")
        self.difficulty_title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self.difficulty_title.setStyleSheet("color: #ffd700;")
        difficulty_layout.addWidget(self.difficulty_title)

        self.difficulty_label = QLabel("难度：入门 (Level 1)")
        self.difficulty_label.setStyleSheet("color: #ffd700; font-size: 14px; font-weight: bold;")
        difficulty_layout.addWidget(self.difficulty_label)

        self.wins_label = QLabel("累计胜利：0 局")
        self.wins_label.setStyleSheet("color: #b7c4ff; font-size: 12px;")
        difficulty_layout.addWidget(self.wins_label)

        self.next_level_label = QLabel("再赢 1 局升级到：初级")
        self.next_level_label.setStyleSheet("color: #7ee7a8; font-size: 11px;")
        difficulty_layout.addWidget(self.next_level_label)

        right_panel.addWidget(difficulty_frame)

        # 游戏信息
        info_frame = QFrame()
        info_frame.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.08); border-radius: 10px;"
            " border: 1px solid rgba(255,255,255,0.15); }"
        )
        info_layout = QVBoxLayout(info_frame)
        info_layout.setContentsMargins(12, 12, 12, 12)

        info_title = QLabel("📋 对局信息")
        info_title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        info_title.setStyleSheet("color: #ecf0ff;")
        info_layout.addWidget(info_title)

        self.info_moves = QLabel("总步数：0")
        self.info_moves.setStyleSheet("color: #b7c4ff; font-size: 13px;")
        self.info_captured = QLabel("吃子数：0")
        self.info_captured.setStyleSheet("color: #b7c4ff; font-size: 13px;")
        self.info_status = QLabel("状态：进行中")
        self.info_status.setStyleSheet("color: #7ee7a8; font-size: 13px;")

        info_layout.addWidget(self.info_moves)
        info_layout.addWidget(self.info_captured)
        info_layout.addWidget(self.info_status)

        right_panel.addWidget(info_frame)

        # 走棋记录
        history_frame = QFrame()
        history_frame.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.08); border-radius: 10px;"
            " border: 1px solid rgba(255,255,255,0.15); }"
        )
        history_layout = QVBoxLayout(history_frame)
        history_layout.setContentsMargins(12, 12, 12, 12)

        history_title = QLabel("📝 走棋记录")
        history_title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        history_title.setStyleSheet("color: #ecf0ff;")
        history_layout.addWidget(history_title)

        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.history_text.setStyleSheet(
            "QTextEdit { background: rgba(0,0,0,0.2); color: #e6ecff;"
            " border: none; border-radius: 6px; padding: 8px;"
            " font-size: 12px; }"
        )
        self.history_text.setPlaceholderText("走棋记录将在这里显示...")
        history_layout.addWidget(self.history_text)

        right_panel.addWidget(history_frame, 1)

        # 提示信息
        self.tip_label = QLabel("💡 提示：红方先行，点击棋子选择走法")
        self.tip_label.setStyleSheet(
            "color: #ffd700; font-size: 12px; padding: 5px;"
            "background: rgba(255,215,0,0.1); border-radius: 6px;"
        )
        self.tip_label.setWordWrap(True)
        right_panel.addWidget(self.tip_label)

        layout.addLayout(right_panel, 1)
        
        # 初始化难度显示
        self._update_difficulty_display()

    def _btn_style(self, color, hover_color) -> str:
        """按钮样式"""
        return (
            f"QPushButton {{ padding: 10px 16px; border-radius: 10px; font-size: 13px;"
            f" color: white; background: {color}; border: none; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {hover_color}; }}"
            f"QPushButton:disabled {{ background: rgba(255,255,255,0.15); color: rgba(255,255,255,0.4); }}"
        )

    def _setup_connections(self):
        """设置信号连接"""
        self.board.piece_moved.connect(self._on_player_move)
        self.btn_restart.clicked.connect(self._restart_game)
        self.btn_give_up.clicked.connect(self._give_up)
        self.btn_analyze.clicked.connect(self._start_analysis)

    def _update_difficulty_display(self):
        """更新难度显示"""
        config = DifficultyLevel.get_config(self.engine.difficulty)
        level = self.engine.difficulty
        name = config["name"]
        
        self.difficulty_label.setText(f"难度：{name} (Level {level})")
        self.wins_label.setText(f"累计胜利：{self.total_wins} 局")
        
        # 计算到下一级还需要赢几局
        if level < DifficultyLevel.MAX_LEVEL:
            # 当前级 = 1 + wins_by_player，所以到下一级需要再赢 (level + 1 - (1 + wins_by_player)) 局
            wins_needed = (level + 1) - (1 + self.engine.wins_by_player)
            if wins_needed <= 0:
                wins_needed = 1
            next_name = DifficultyLevel.get_name(level + 1)
            self.next_level_label.setText(f"再赢 {wins_needed} 局升级到：{next_name}")
            self.next_level_label.setVisible(True)
        else:
            self.next_level_label.setText("🏆 已达到最高难度！")
            self.next_level_label.setVisible(True)

    def _on_player_move(self, from_pos: list, to_pos: list):
        """处理玩家走子"""
        if not self._game_active or not self._player_turn:
            return

        # 执行走子
        ok, msg = self.engine.make_move(
            from_pos[0], from_pos[1], to_pos[0], to_pos[1]
        )

        if ok:
            self._player_turn = False
            self._update_ui()
            self._add_history_entry(from_pos, to_pos, is_player=True)

            # 检查游戏是否结束
            if self.engine.game_over:
                self._end_game()
                return

            # AI 走子
            QTimer.singleShot(500, self._ai_move)
        else:
            self._show_tip(msg, is_error=True)

    def _ai_move(self):
        """AI 走子"""
        if not self._game_active:
            return

        self._show_tip("🤔 AI 正在思考...")

        self.board.set_player_turn(False)
        self.ai_thread = AIPlayerThread(self.engine)
        self.ai_thread.move_ready.connect(self._on_ai_move_ready)
        self.ai_thread.error_occurred.connect(self._on_ai_error)
        self.ai_thread.start()

    def _on_ai_move_ready(self, move: tuple):
        """AI 走子完成"""
        if not self._game_active:
            return

        from_col, from_row, to_col, to_row = move

        piece = self.engine.get_piece(from_col, from_row)
        if not piece or piece.side != Side.BLACK:
            self._show_tip("AI 走子异常，跳过本回合", is_error=True)
            self._player_turn = True
            self.board.set_player_turn(True)
            self._update_ui()
            return

        ok, msg = self.engine.make_move(from_col, from_row, to_col, to_row)

        if ok:
            self._player_turn = True
            self._update_ui()
            self._add_history_entry(
                (from_col, from_row), (to_col, to_row), is_player=False
            )
            self.board.set_last_move((from_col, from_row), (to_col, to_row))

            if self.engine.game_over:
                self._end_game()
                return

            self.board.set_player_turn(True)
            self._show_tip("💡 轮到你走棋了")
        else:
            self._show_tip(f"AI 走子失败: {msg}", is_error=True)
            self._player_turn = True
            self.board.set_player_turn(True)
            self._update_ui()

    def _on_ai_error(self, error: str):
        """AI 出错"""
        self._show_tip(f"AI 出错: {error}", is_error=True)
        self._player_turn = True
        self.board.set_player_turn(True)
        self._update_ui()

    def _update_ui(self):
        """更新 UI"""
        self.board.update()

        # 更新回合指示
        if self.engine.current_turn == Side.RED:
            self.turn_label.setText("红方（你）走棋")
            self.turn_label.setStyleSheet(
                "color: #ff6b6b; font-size: 14px; padding: 5px;"
                "background: rgba(255,107,107,0.15); border-radius: 8px;"
            )
        else:
            difficulty_info = self.engine.get_difficulty_info()
            self.turn_label.setText(f"黑方（AI · {difficulty_info['name']}）走棋")
            self.turn_label.setStyleSheet(
                "color: #6b6bff; font-size: 14px; padding: 5px;"
                "background: rgba(107,107,255,0.15); border-radius: 8px;"
            )

        # 更新信息
        move_count = len(self.engine.move_history)
        self.info_moves.setText(f"总步数：{move_count}")

        captured_count = sum(
            1 for m in self.engine.move_history
            if m.captured and m.piece.side == Side.RED
        )
        self.info_captured.setText(f"吃子数：{captured_count}")

        if self.engine.game_over:
            if self.engine.winner == Side.RED:
                self.info_status.setText("状态：🎉 你赢了！")
                self.info_status.setStyleSheet(
                    "color: #7ee7a8; font-size: 13px; font-weight: bold;"
                )
            else:
                self.info_status.setText("状态：😔 你输了")
                self.info_status.setStyleSheet(
                    "color: #ff8a9a; font-size: 13px; font-weight: bold;"
                )
        
        # 更新难度显示
        self._update_difficulty_display()

    def _add_history_entry(self, from_pos: tuple, to_pos: tuple,
                           is_player: bool):
        """添加走棋记录"""
        history = self.engine.move_history[-1]
        move_num = len(self.engine.move_history)
        player_label = "你" if is_player else "AI"
        color = "#ff6b6b" if is_player else "#6b6bff"

        entry = f'<div style="color: {color}; margin-bottom: 4px;">'
        entry += f'<b>第{move_num}步 [{player_label}]</b>: '
        entry += f'{history.piece.name} ({from_pos[0]+1},{from_pos[1]+1}) → ({to_pos[0]+1},{to_pos[1]+1})'

        if history.captured:
            entry += f' <span style="color: #ffd700;">吃{history.captured.name}</span>'

        entry += '</div>'

        self.history_text.append(entry)

    def _restart_game(self):
        """重新开始游戏"""
        reply = QMessageBox.question(
            self, "确认", "确定要重新开始吗？\n（AI 难度将保持当前等级）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            # 保持当前难度等级
            current_difficulty = self.engine.difficulty
            wins_by_player = self.engine.wins_by_player
            
            self.engine = XiangqiEngine(difficulty=current_difficulty)
            self.engine.wins_by_player = wins_by_player  # 保留胜利次数记录
            
            self.board.set_engine(self.engine)
            self.board.set_player_turn(True)
            self._game_active = True
            self._player_turn = True
            self.history_text.clear()
            self.btn_analyze.setEnabled(False)
            self._update_ui()
            
            difficulty_info = self.engine.get_difficulty_info()
            self._show_tip(f"🎮 新棋局开始！当前难度：{difficulty_info['name']}")

    def _give_up(self):
        """认输"""
        if self.engine.game_over:
            return

        reply = QMessageBox.question(
            self, "确认认输", "确定要认输吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.engine.winner = Side.BLACK
            self.engine.game_over = True
            self._game_active = False
            self._update_ui()
            self.btn_analyze.setEnabled(True)
            self._show_tip("😔 你认输了。点击「AI 分析」查看改进建议")

    def _end_game(self):
        """结束游戏"""
        self._game_active = False
        self._player_turn = False
        self.board.set_player_turn(False)
        
        # 如果玩家获胜，增加累计胜利次数并提升难度
        if self.engine.winner == Side.RED:
            self.total_wins += 1
            self.engine.increment_difficulty()
            
            difficulty_info = self.engine.get_difficulty_info()
            if difficulty_info["level"] > 1:
                self._show_tip(
                    f"🎉 恭喜你赢了！\n"
                    f"💪 AI 难度已提升到：{difficulty_info['name']} (Level {difficulty_info['level']})\n"
                    f"点击「AI 分析」查看你的精彩表现"
                )
            else:
                self._show_tip(
                    "🎉 恭喜你赢了！\n"
                    "💪 AI 难度已提升到：初级 (Level 2)\n"
                    "点击「AI 分析」查看你的精彩表现"
                )
        else:
            self._show_tip("😔 你输了。点击「AI 分析」看看哪里可以改进")
        
        self._update_ui()
        self.btn_analyze.setEnabled(True)

    def _start_analysis(self):
        """开始 AI 分析"""
        if not self.engine.move_history:
            self._show_tip("还没有走棋记录，无法分析", is_error=True)
            return

        self.btn_analyze.setEnabled(False)
        self.btn_analyze.setText("⏳ 分析中...")
        self._show_tip("🔍 AI 正在分析你的棋局...")

        self.analysis_thread = AnalysisWorker(
            self.analyzer, self.engine, Side.RED
        )
        self.analysis_thread.analysis_ready.connect(self._on_analysis_ready)
        self.analysis_thread.start()

    def _on_analysis_ready(self, result: dict):
        """分析完成"""
        self.btn_analyze.setEnabled(True)
        self.btn_analyze.setText("📊 AI 分析")

        if "error" in result:
            self._show_tip(f"分析失败: {result['error']}", is_error=True)
            return

        # 显示分析结果对话框
        self._show_analysis_result(result)

    def _show_analysis_result(self, result: dict):
        """显示分析结果"""
        dialog = QDialog(self)
        dialog.setWindowTitle("🎓 AI 教练分析报告")
        dialog.resize(600, 500)
        dialog.setStyleSheet("""
            QDialog { background: #1a1a3e; }
            QLabel { color: #ecf0ff; }
            QTextEdit {
                background: rgba(255,255,255,0.08);
                color: #e6ecff;
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 8px;
                padding: 12px;
                font-size: 13px;
            }
            QPushButton {
                padding: 10px 20px;
                border-radius: 8px;
                color: white;
                background: #4f8cff;
                border: none;
                font-weight: 600;
            }
            QPushButton:hover { background: #3a6fd6; }
        """)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        # 标题
        title = QLabel("📊 你的棋局分析报告")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffd700;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # 结果文本
        result_text = QTextEdit()
        result_text.setReadOnly(True)
        result_html = self._format_analysis_html(result)
        result_text.setHtml(result_html)
        layout.addWidget(result_text, 1)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_layout.addWidget(close_btn)
        close_layout.addStretch()
        layout.addLayout(close_layout)

        dialog.exec()

    def _format_analysis_html(self, result: dict) -> str:
        """格式化分析结果为 HTML"""
        html = """
        <div style="font-family: 'Microsoft YaHei', sans-serif; line-height: 1.8;">
        """

        # 对局结果
        winner = result.get("winner")
        if winner:
            if winner == "red":
                html += '<div style="color: #7ee7a8; font-size: 18px; margin-bottom: 12px;">🎉 恭喜获胜！</div>'
            else:
                html += '<div style="color: #ff8a9a; font-size: 18px; margin-bottom: 12px;">😔 再接再厉！</div>'

        # 总步数
        total = result.get("total_moves", 0)
        html += f'<div style="color: #b7c4ff; margin-bottom: 16px;">📝 总步数：{total}</div>'

        # AI 分析文本
        analysis = result.get("analysis", "")
        if analysis:
            html += f"""
            <div style="background: rgba(255,215,0,0.1); border-left: 3px solid #ffd700;
                        padding: 12px; margin-bottom: 16px; border-radius: 4px;">
                <div style="color: #ffd700; font-weight: bold; margin-bottom: 8px;">🎓 AI 教练点评</div>
                <div style="color: #e6ecff; white-space: pre-wrap;">{analysis}</div>
            </div>
            """

        # 失误列表
        mistakes = result.get("player_mistakes", [])
        if mistakes:
            html += '<div style="color: #ff6b6b; font-weight: bold; margin-bottom: 8px;">⚠️ 发现的失误</div>'
            for mistake in mistakes:
                html += f"""
                <div style="background: rgba(255,107,107,0.1); padding: 10px;
                            margin-bottom: 8px; border-radius: 6px;">
                    <div style="color: #ecf0ff;">
                        <b>第{mistake['move_number']}步</b>: {mistake['move']}
                    </div>
                    <div style="color: #ffb3be; font-size: 12px; margin-top: 4px;">
                        {mistake['description']}
                    </div>
                </div>
                """
        elif not analysis:
            html += '<div style="color: #7ee7a8;">✨ 没有发现明显失误，表现不错！</div>'

        html += '</div>'
        return html

    def _show_tip(self, message: str, is_error: bool = False):
        """显示提示信息"""
        if is_error:
            self.tip_label.setStyleSheet(
                "color: #ff8a9a; font-size: 12px; padding: 5px;"
                "background: rgba(255,100,120,0.15); border-radius: 6px;"
            )
        else:
            self.tip_label.setStyleSheet(
                "color: #ffd700; font-size: 12px; padding: 5px;"
                "background: rgba(255,215,0,0.1); border-radius: 6px;"
            )
        self.tip_label.setText(message)
