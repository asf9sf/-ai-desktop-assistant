"""
Emotion Module — 多通道情绪交互引擎
基于 emotion-engine (https://github.com/fenghua2006/emotion-engine) 移植

特性:
- 10 个独立情绪通道（joy, sadness, anger, fear, disgust, surprise, love, trust, longing, guilt）
- 弹性衰减：偏离基线越远，回弹越快
- 情绪交互矩阵：情绪之间互相调制，不互相抵消
- 人格基线：基于大五人格（OCEAN）
- 感知压缩：log₁₀ 映射，边际递减
"""
import time
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# 通道定义
# ══════════════════════════════════════════════════════
class Channel(Enum):
    """情绪通道"""
    JOY = "joy"           # 喜悦
    SADNESS = "sadness"   # 悲伤
    ANGER = "anger"       # 愤怒
    FEAR = "fear"         # 恐惧
    LOVE = "love"         # 爱/依恋 (慢通道)
    DISGUST = "disgust"   # 厌恶
    SURPRISE = "surprise" # 惊讶
    TRUST = "trust"       # 信任 (慢通道)
    LONGING = "longing"   # 思念 (缺失驱动)
    GUILT = "guilt"       # 愧疚 (自责驱动)


ALL_CHANNELS = list(Channel)

# 半衰期（分钟）
HALF_LIFE: Dict[Channel, Optional[float]] = {
    Channel.JOY: 90,
    Channel.SADNESS: 180,
    Channel.ANGER: 120,
    Channel.FEAR: 60,
    Channel.LOVE: None,     # 慢通道不自动衰减
    Channel.DISGUST: 90,
    Channel.SURPRISE: 30,
    Channel.GUILT: 180,
    Channel.TRUST: None,    # 慢通道不自动衰减
    Channel.LONGING: 30,    # 思念快速消退
}

# 中文标签
CHANNEL_CN: Dict[Channel, str] = {
    Channel.JOY: "喜悦",
    Channel.SADNESS: "悲伤",
    Channel.ANGER: "愤怒",
    Channel.FEAR: "恐惧",
    Channel.LOVE: "好感",
    Channel.DISGUST: "厌恶",
    Channel.SURPRISE: "惊讶",
    Channel.TRUST: "信任",
    Channel.LONGING: "思念",
    Channel.GUILT: "愧疚",
}

# Emoji 映射
CHANNEL_EMOJI: Dict[Channel, str] = {
    Channel.JOY: "😊",
    Channel.SADNESS: "😢",
    Channel.ANGER: "😠",
    Channel.FEAR: "😨",
    Channel.LOVE: "❤️",
    Channel.DISGUST: "😖",
    Channel.SURPRISE: "😮",
    Channel.TRUST: "🤝",
    Channel.LONGING: "💭",
    Channel.GUILT: "😔",
}


# ══════════════════════════════════════════════════════
# 核心: EmotionalState
# ══════════════════════════════════════════════════════
@dataclass
class EmotionalState:
    """10 通道情绪状态"""
    joy: float = 0.3
    sadness: float = 0.1
    anger: float = 0.05
    fear: float = 0.05
    love: float = 0.2
    disgust: float = 0.0
    surprise: float = 0.0
    trust: float = 0.25
    longing: float = 0.0
    guilt: float = 0.0

    _previous: Optional[Dict[str, float]] = None
    _last_update: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, float]:
        return {ch.value: getattr(self, ch.value) for ch in Channel}

    def clone(self) -> "EmotionalState":
        s = EmotionalState(**self.to_dict())
        s._last_update = self._last_update
        if self._previous:
            s._previous = dict(self._previous)
        return s

    def snapshot_previous(self):
        self._previous = self.to_dict()

    def felt(self, ch: Channel) -> float:
        """感知压缩：log₁₀ 映射，边际递减"""
        raw = getattr(self, ch.value)
        if raw <= 0:
            return 0.0
        return math.log10(1 + raw * 2)

    def felt_all(self) -> Dict[str, float]:
        return {ch.value: self.felt(ch) for ch in Channel}


# ══════════════════════════════════════════════════════
# 情绪衰减
# ══════════════════════════════════════════════════════
def decay_value(value: float, half_life_minutes: Optional[float],
                elapsed_minutes: float, baseline: float = 0.0) -> float:
    """弹性衰减：偏离基线越远，回弹越快"""
    if half_life_minutes is None or elapsed_minutes <= 0:
        return value
    if baseline > 0.01:
        distance = abs(value - baseline) / baseline
    else:
        distance = abs(value - baseline)
    effective_hl = half_life_minutes / (1 + distance)
    return baseline + (value - baseline) * (2 ** (-elapsed_minutes / effective_hl))


def saturate(x: float, asymptote: float = 3.0) -> float:
    """自然饱和因子"""
    return max(0.0, 1.0 - x / asymptote)


# ══════════════════════════════════════════════════════
# 交互矩阵
# ══════════════════════════════════════════════════════
INTERACTION_RULES: Dict[Tuple[Channel, Channel], Tuple[str, float]] = {
    (Channel.SADNESS, Channel.ANGER): ("amplify_anger", 0.3),
    (Channel.ANGER, Channel.TRUST): ("suppress_trust", 0.1),
    (Channel.TRUST, Channel.FEAR): ("suppress_fear", 0.15),
    (Channel.FEAR, Channel.ANGER): ("amplify_anger", 0.2),
    (Channel.LOVE, Channel.FEAR): ("blend", 0.0),
    (Channel.JOY, Channel.SADNESS): ("blend", 0.0),
    (Channel.SURPRISE, Channel.FEAR): ("amplify_fear", 0.4),
    (Channel.SURPRISE, Channel.JOY): ("amplify_joy", 0.25),
    (Channel.DISGUST, Channel.ANGER): ("amplify_anger", 0.35),
    (Channel.LOVE, Channel.TRUST): ("amplify_trust", 0.02),
    (Channel.GUILT, Channel.TRUST): ("amplify_trust", 0.1),
    (Channel.GUILT, Channel.ANGER): ("suppress_anger", 0.3),
    (Channel.GUILT, Channel.SADNESS): ("amplify_sadness", 0.2),
    (Channel.GUILT, Channel.LOVE): ("amplify_love", 0.05),
}


def apply_interactions(state: EmotionalState) -> List[str]:
    """应用交互矩阵，返回混合标签"""
    blends = []
    for (a, b), (effect, strength) in INTERACTION_RULES.items():
        va = getattr(state, a.value)
        vb = getattr(state, b.value)
        if va < 0.15 or vb < 0.15:
            continue

        if effect == "amplify_joy":
            state.joy += vb * strength * saturate(state.joy)
        elif effect == "amplify_anger":
            state.anger += vb * strength * saturate(state.anger)
        elif effect == "amplify_fear":
            state.fear += vb * strength * saturate(state.fear)
        elif effect == "amplify_sadness":
            state.sadness += vb * strength * saturate(state.sadness)
        elif effect == "amplify_love":
            state.love += strength * saturate(state.love, asymptote=2.0)
        elif effect == "amplify_trust":
            state.trust += strength * saturate(state.trust)
        elif effect.startswith("suppress"):
            target = effect.split("_")[1]
            current = getattr(state, target)
            setattr(state, target, max(0.0, current - va * strength))
        elif effect == "blend":
            blends.append(f"{a.value}+{b.value}")
    return blends


# ══════════════════════════════════════════════════════
# 认知评估
# ══════════════════════════════════════════════════════
@dataclass
class Appraisal:
    """事件的认知评估参数"""
    goal_relevance: float = 0.0       # 0~1 目标相关性
    goal_conduciveness: float = 0.0   # -1 障碍 ~ +1 助力
    expectedness: float = 0.5         # 0 完全意外 ~ 1 完全预期
    coping_potential: float = 0.5     # 0~1 控制能力
    other_agency: float = 0.0         # 0~1 外部因素占比
    social_evaluation: float = 0.0    # -1 负面评价 ~ +1 正面评价


def appraise(app: Appraisal) -> Dict[Channel, float]:
    """认知评估 → 通道原始激活"""
    gc = app.goal_conduciveness
    gr = app.goal_relevance
    cp = app.coping_potential
    ue = 1.0 - app.expectedness
    self_agency = 1.0 - app.other_agency

    return {
        Channel.JOY: gc * gr if gc > 0 else 0.0,
        Channel.SADNESS: -gc * gr * (1.0 - cp) if gc < 0 else 0.0,
        Channel.ANGER: -gc * app.other_agency * cp if gc < 0 else 0.0,
        Channel.FEAR: -gc * gr * (1.0 - cp) * ue if gc < 0 else 0.0,
        Channel.DISGUST: -app.social_evaluation * app.other_agency if app.social_evaluation < 0 else 0.0,
        Channel.SURPRISE: ue * gr,
        Channel.GUILT: -gc * gr * self_agency if gc < 0 and self_agency > 0.3 else 0.0,
        Channel.LONGING: 0.0,  # 由在线/离线状态驱动
        Channel.LOVE: 0.0,     # 慢通道由累积驱动
        Channel.TRUST: 0.0,    # 慢通道由累积驱动
    }


# ══════════════════════════════════════════════════════
# 人格
# ══════════════════════════════════════════════════════
@dataclass
class Personality:
    """OCEAN 大五人格"""
    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.5

    def baseline(self) -> Dict[Channel, float]:
        return {
            Channel.JOY: 0.2 + self.extraversion * 0.3,
            Channel.SADNESS: 0.05 + self.neuroticism * 0.15,
            Channel.ANGER: 0.05 + (1 - self.agreeableness) * 0.15,
            Channel.FEAR: 0.05 + self.neuroticism * 0.1,
            Channel.LOVE: 0.15 + self.agreeableness * 0.2,
            Channel.DISGUST: 0.0 + (1 - self.agreeableness) * 0.1,
            Channel.SURPRISE: 0.05 + self.openness * 0.1,
            Channel.TRUST: 0.2 + self.agreeableness * 0.3,
            Channel.LONGING: 0.0,
            Channel.GUILT: 0.0 + (1 - self.agreeableness) * 0.05,
        }


# ══════════════════════════════════════════════════════
# 情绪引擎主类
# ══════════════════════════════════════════════════════
class EmotionEngine:
    """多通道情绪交互引擎"""

    def __init__(self, personality: Personality = None):
        self.personality = personality or Personality()
        self.state = EmotionalState()
        self._event_count_today = 0
        self._positive_count = 0
        self._last_grow_time = time.time()

    def tick(self, appraisal: Appraisal) -> Dict[str, any]:
        """处理一个事件，更新情绪状态"""
        now = time.time()
        elapsed_minutes = (now - self.state._last_update) / 60.0
        elapsed_hours = elapsed_minutes / 60.0

        # 保存快照用于对比度
        self.state.snapshot_previous()

        # 1. 衰减
        self._apply_decay(elapsed_minutes)

        # 2. 认知评估
        raw_activation = appraise(appraisal)

        # 3. 门控（信任/爱调节情绪反应）
        gated = self._gate_appraisal(raw_activation)

        # 4. 应用激活值
        for ch, val in gated.items():
            current = getattr(self.state, ch.value)
            new_val = current + val
            setattr(self.state, ch.value, max(0.0, new_val))

        # 5. 情绪交互
        blends = apply_interactions(self.state)

        # 6. 慢通道更新
        self._grow_slow_channels(elapsed_hours)

        # 7. 更新时间戳
        self.state._last_update = now
        self._event_count_today += 1

        # 统计正负面
        if appraisal.goal_conduciveness > 0.3:
            self._positive_count += 1
        elif appraisal.goal_conduciveness < -0.3:
            self._positive_count = max(0, self._positive_count - 1)

        # 触发思念衰减（在线时消退）
        self.state.longing *= 0.95

        return {
            "felt": self.state.felt_all(),
            "blends": blends,
            "dominant": self._get_dominant_emotion(),
        }

    def _apply_decay(self, elapsed_minutes: float):
        """应用弹性衰减"""
        baseline = self.personality.baseline()
        for ch in Channel:
            hl = HALF_LIFE.get(ch)
            current = getattr(self.state, ch.value)
            base = baseline.get(ch, 0.0)
            decayed = decay_value(current, hl, elapsed_minutes, base)
            setattr(self.state, ch.value, max(0.0, decayed))

    def _gate_appraisal(self, raw_activation: Dict[Channel, float]) -> Dict[Channel, float]:
        """信任/爱调节情绪反应强度"""
        trust = self.state.trust
        love = self.state.love

        fear_gate = max(0.02, 1.0 - trust * 0.9)
        anger_gate = max(0.05, 1.0 - trust * 0.7)
        sadness_gate = max(0.05, 1.0 - love * 0.6)
        disgust_gate = max(0.05, 1.0 - trust * 0.8)
        joy_boost = 1.0 + love * 0.5

        gated = {}
        for ch, val in raw_activation.items():
            if ch == Channel.FEAR:
                gated[ch] = val * fear_gate
            elif ch == Channel.ANGER:
                gated[ch] = val * anger_gate
            elif ch == Channel.SADNESS:
                gated[ch] = val * sadness_gate
            elif ch == Channel.DISGUST:
                gated[ch] = val * disgust_gate
            elif ch == Channel.JOY:
                gated[ch] = val * joy_boost
            else:
                gated[ch] = val
        return gated

    def _grow_slow_channels(self, elapsed_hours: float):
        """慢通道累积"""
        # trust 向 love 引力线靠近
        target_trust = 1.0 - 1.0 / (1.0 + self.state.love * 3.5)
        gap = target_trust - self.state.trust
        if gap > 0:
            self.state.trust += gap * 0.02 + elapsed_hours * 0.0003
        else:
            self.state.trust += gap * 0.005

        # love 受 trust 影响
        trust_deficit = max(0.0, target_trust - self.state.trust)
        if trust_deficit > 0.15:
            self.state.love -= trust_deficit * 0.005 * max(0.01, elapsed_hours)
        else:
            sat_love = saturate(self.state.love, asymptote=2.0)
            love_growth = elapsed_hours * 0.0005 * sat_love + self._positive_count * 0.02 * sat_love
            self.state.love += love_growth

    def _get_dominant_emotion(self) -> Channel:
        """获取主导情绪"""
        felt = self.state.felt_all()
        max_ch = max(Channel, key=lambda ch: felt.get(ch.value, 0))
        return max_ch

    def get_state_summary(self) -> Dict[str, any]:
        """获取当前情绪状态摘要"""
        felt = self.state.felt_all()
        dominant = self._get_dominant_emotion()
        return {
            "dominant": dominant.value,
            "dominant_cn": CHANNEL_CN[dominant],
            "dominant_emoji": CHANNEL_EMOJI[dominant],
            "raw": self.state.to_dict(),
            "felt": felt,
            "trust": self.state.trust,
            "love": self.state.love,
            "longing": self.state.longing,
        }

    def damage_trust(self, severity: float = 0.5):
        """背叛事件：打掉信任"""
        self.state.trust *= (1.0 - severity * 0.5)
        if severity > 0.5:
            self.state.love *= 0.85

    def reset(self):
        """重置情绪状态"""
        self.state = EmotionalState()
        self._event_count_today = 0
        self._positive_count = 0
        self._last_grow_time = time.time()


# ══════════════════════════════════════════════════════
# 便捷函数
# ══════════════════════════════════════════════════════

def analyze_user_input(text: str) -> Appraisal:
    """简单的文本情绪分析，将用户输入映射为 Appraisal 参数"""
    text_lower = text.lower()

    # 目标相关性
    relevance = 0.3  # 对话默认有一定相关性

    # 检测正面/负面
    positive_words = ["喜欢", "开心", "谢谢", "好的", "棒", "厉害", "爱", "赞", "哈哈", "😊", "❤️"]
    negative_words = ["讨厌", "生气", "烦", "讨厌", "不好", "差", "笨", "蠢", "恨", "😡", "😢"]

    positive_score = sum(1 for w in positive_words if w in text_lower)
    negative_score = sum(1 for w in negative_words if w in text_lower)

    # 目标助益性
    conduciveness = 0.0
    if positive_score > negative_score:
        conduciveness = min(1.0, positive_score * 0.3)
    elif negative_score > positive_score:
        conduciveness = -min(1.0, negative_score * 0.3)

    # 惊讶度（检测意外内容）
    surprise_words = ["哇", "哦", "真的", "竟然", "居然", "什么", "怎么", "难道"]
    expectedness = 0.5 - sum(0.1 for w in surprise_words if w in text_lower)

    # 社交评价
    social_eval = 0.0
    if positive_score > 0:
        social_eval = 0.3
    if negative_score > 0:
        social_eval = -0.3

    # 检测是否为提问（增加控制感）
    question_marks = "？?"
    is_question = any(m in text for m in question_marks)
    coping = 0.7 if is_question else 0.5

    # 检测是否为命令/指令（增加外部归因）
    command_words = ["帮我", "给我", "去", "来", "做", "执行", "打开", "关闭", "搜索"]
    is_command = any(w in text for w in command_words)
    other_agency = 0.6 if is_command else 0.3

    return Appraisal(
        goal_relevance=relevance,
        goal_conduciveness=conduciveness,
        expectedness=max(0.0, min(1.0, expectedness)),
        coping_potential=coping,
        other_agency=other_agency,
        social_evaluation=social_eval,
    )


def build_emotion_prompt(engine: EmotionEngine) -> str:
    """构建情绪状态提示词，注入到 LLM system prompt"""
    summary = engine.get_state_summary()
    felt = summary["felt"]

    # 只返回超过阈值的情绪
    active_emotions = []
    for ch in Channel:
        val = felt.get(ch.value, 0)
        if val > 0.15:
            active_emotions.append(f"{CHANNEL_CN[ch]}({val:.2f})")

    prompt_parts = [
        f"你当前的情绪状态：",
        f"- 主导情绪: {summary['dominant_cn']} {summary['dominant_emoji']}",
        f"- 信任度: {summary['trust']:.2f}",
        f"- 好感度: {summary['love']:.2f}",
    ]

    if active_emotions:
        prompt_parts.append(f"- 活跃情绪: {', '.join(active_emotions)}")

    prompt_parts.append(
        "请根据此情绪状态调整你的回复语气和用词。"
        "信任度高时可以更坦诚、更有温度；好感度高时可以更亲昵。"
        "保持情绪一致性，不要做出与当前情绪矛盾的反应。"
    )

    return "\n".join(prompt_parts)
