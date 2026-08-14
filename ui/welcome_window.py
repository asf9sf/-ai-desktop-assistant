from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QPainter, QColor, QPixmap, QLinearGradient, QBrush
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect, QApplication


class WelcomeWindow(QWidget):
    """欢迎动画界面：大字显示「欢迎回来 {name}」，渐变消散，同步语音播报。"""

    finished = pyqtSignal()

    def __init__(self, user_name: str = "用户", llm=None):
        super().__init__()
        self.user_name = user_name
        self.llm = llm
        self._tts_mgr = None
        self._setup_ui()
        self._setup_animations()

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        screen = QApplication.primaryScreen().geometry()
        w, h = 900, 400
        self.setGeometry(
            (screen.width() - w) // 2,
            (screen.height() - h) // 2,
            w, h,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(20)
        root.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 欢迎大字（渐变颜色）
        self.label = QLabel(f"欢迎回来，{self.user_name}")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(56)
        font.setBold(True)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.label.setFont(font)
        self.label.setStyleSheet(self._gradient_style(QColor(80, 150, 255), QColor(200, 80, 220)))
        self.label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 副标题
        self.sub = QLabel(f"我是你的智能助手")
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_font = QFont()
        sub_font.setPointSize(18)
        self.sub.setFont(sub_font)
        self.sub.setStyleSheet("color: rgba(255,255,255,220);")

        root.addStretch(1)
        root.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.sub, alignment=Qt.AlignmentFlag.AlignCenter)
        root.addStretch(1)

        # 整体透明度
        self.effect = QGraphicsOpacityEffect(self)
        self.effect.setOpacity(0.0)
        self.setGraphicsEffect(self.effect)

    def _gradient_style(self, c1: QColor, c2: QColor) -> str:
        return (
            "QLabel { background: transparent;"
            f"color: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {c1.name()}, stop:1 {c2.name()});"
            " padding: 10px; }"
        )

    def _setup_animations(self):
        # 淡入
        self.fade_in = QPropertyAnimation(self.effect, b"opacity")
        self.fade_in.setDuration(700)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_in.finished.connect(self._on_faded_in)

        # 淡出
        self.fade_out = QPropertyAnimation(self.effect, b"opacity")
        self.fade_out.setDuration(1200)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self.fade_out.finished.connect(self._on_finished)

        # 文字轻微放大
        self.scale_anim = QPropertyAnimation(self.label, b"size")
        start_size = QSize(self.label.width(), self.label.height())
        end_size = QSize(self.label.width(), self.label.height())
        self.scale_anim.setDuration(2500)
        self.scale_anim.setStartValue(start_size)
        self.scale_anim.setEndValue(end_size)

    def start(self):
        self.show()
        # 人脸识别完成的瞬间立即开始语音播报（不等待淡入动画）
        self._play_welcome_voice()
        self.fade_in.start()

    def _on_faded_in(self):
        """淡入完成，开始停留阶段。等待语音播报结束后再淡出。"""
        self._wait_voice_then_fadeout()

    def _wait_voice_then_fadeout(self):
        """等语音播报完毕再淡出，避免被打断。"""
        if self._tts_mgr is not None and self._tts_mgr.is_playing():
            # 每 200ms 轮询一次
            QTimer.singleShot(200, self._wait_voice_then_fadeout)
            return
        # 语音已结束（或未启用 TTS），短暂停留后淡出
        QTimer.singleShot(300, self.fade_out.start)

    def _play_welcome_voice(self):
        """人脸识别登录成功后：欢迎语显示的同时语音播报。"""
        try:
            from modules.tts_module import TTSManager
            if self.llm is not None:
                s = self.llm.settings
                engine = s.get("tts_engine", "off")
                if engine == "off":
                    return
                sid = s.get("tts_speaker_id", 0)
                vol = s.get("tts_volume", 2.5)
                # 根据引擎选择正确的模型目录
                if engine == "cosyvoice":
                    model_dir = s.get("cosyvoice_model_dir", "") or s.get("tts_model_dir", "")
                else:
                    model_dir = s.get("tts_model_dir", "")
                cosyvoice_speaker = s.get("cosyvoice_speaker", "中文女")
                cosyvoice_emotion = s.get("cosyvoice_emotion", "")
                sherpa_emotion = s.get("sherpa_emotion", "")
            else:
                engine = "system"
                model_dir = ""
                sid = 0
                vol = 2.5
                cosyvoice_speaker = "中文女"
                cosyvoice_emotion = ""
                sherpa_emotion = ""
            self._tts_mgr = TTSManager(
                engine=engine,
                model_dir=model_dir,
                speaker_id=sid,
                volume=vol,
                cosyvoice_speaker=cosyvoice_speaker,
                cosyvoice_emotion=cosyvoice_emotion,
                sherpa_emotion=sherpa_emotion,
            )
            # 立即开始合成 + 播放（懒加载 Sherpa 引擎约 0.6s）
            self._tts_mgr.speak(f"欢迎回来，{self.user_name}。")
        except Exception:
            self._tts_mgr = None

    def _on_finished(self):
        # 欢迎窗结束，清理 TTS 资源避免与主窗口冲突
        if self._tts_mgr is not None:
            try:
                self._tts_mgr.shutdown()
            except Exception:
                pass
            self._tts_mgr = None
        self.hide()
        self.finished.emit()

    # 绘制半透明背景圆角卡片
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(15, 20, 40, 210))
        grad.setColorAt(1.0, QColor(30, 20, 60, 210))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 30, 30)
        p.end()
