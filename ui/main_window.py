import sys
import os
import re
import json
import logging
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QDateTime
from PyQt6.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPalette, QIcon, QTextCursor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QComboBox, QFrame, QMessageBox, QSizePolicy,
    QScrollArea, QApplication, QGraphicsOpacityEffect, QProgressBar,
    QRadioButton, QButtonGroup,
)

from modules.llm_module import LLMClient
from modules.app_controller import AppController
from modules.browser_search import BrowserSearcher
from modules.tts_module import TTSManager
from modules.speech_module import SpeechManager, MODE_OFF, MODE_PUSH_TO_TALK, MODE_WAKE_WORD
from modules.voiceprint_module import VoiceprintManager
from modules.agent_core import Agent
from modules.memory_system import MemSkillManager
from modules.file_operation_module import FileOperator
from modules.scheduler_module import Scheduler
from ui.settings_dialog import SettingsDialog
from ui.memory_dialog import MemoryDialog

logger = logging.getLogger(__name__)


# ---------------- 后台处理线程 ----------------
class AgentWorker(QThread):
    partial = pyqtSignal(str)     # 流式增量
    done = pyqtSignal(str, dict)  # 完整回复 + 动作详情
    error = pyqtSignal(str)

    def __init__(self, agent: Agent, user_text: str, stream: bool):
        super().__init__()
        self.agent = agent
        self.user_text = user_text
        self.stream = stream

    def _cb(self, delta: str):
        self.partial.emit(delta)

    def run(self):
        try:
            reply, info = self.agent.process(
                self.user_text,
                stream_callback=self._cb if self.stream else None,
            )
            self.done.emit(reply, info)
        except Exception as e:
            self.error.emit(str(e))


# ---------------- 记忆维护线程 ----------------
class MemoryMaintenanceWorker(QThread):
    """后台定时执行记忆合并与衰减删除。"""
    done = pyqtSignal(dict)

    def __init__(self, memory: MemSkillManager):
        super().__init__()
        self.memory = memory

    def run(self):
        try:
            stats = self.memory.schedule_maintenance()
            self.done.emit(stats)
        except Exception as e:
            self.done.emit({"merged": 0, "forgotten": 0, "error": str(e)})


# ---------------- 聊天气泡 ----------------
class ChatBubble(QFrame):
    def __init__(self, text: str, is_user: bool = False, max_width: int = 0):
        super().__init__()
        self.is_user = is_user
        self._max_text_w = max_width  # 由主窗口传入的可靠宽度

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFrameShape(QFrame.Shape.NoFrame)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.text_edit.setStyleSheet(
            "QTextEdit { background: transparent; color: inherit; border: none;"
            " padding: 0; margin: 0; }"
        )
        self.text_edit.setFont(QFont("Microsoft YaHei", 11))
        self.text_edit.document().setDocumentMargin(8)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # text_edit 占满整个 QFrame
        root.addWidget(self.text_edit, 1)

        # 通过文档默认样式表设置文本对齐方式
        if is_user:
            self.text_edit.document().setDefaultStyleSheet("body { text-align: right; }")
        else:
            self.text_edit.document().setDefaultStyleSheet("body { text-align: left; }")

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self._apply_style()
        self.set_text(text)

    def _apply_style(self):
        if self.is_user:
            self.setStyleSheet(
                "ChatBubble { background: rgba(79,140,255,0.85); border-radius: 16px;"
                " border-bottom-right-radius: 4px; padding: 16px 18px; }"
                "QTextEdit { color: white; }"
            )
        else:
            self.setStyleSheet(
                "ChatBubble { background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15);"
                " border-radius: 16px; border-bottom-left-radius: 4px; padding: 16px 18px; }"
                "QTextEdit { color: #ecf0ff; }"
            )

    def _get_text_width(self) -> int:
        """返回文本渲染宽度。优先用传入的max_width，回退到父容器宽度。"""
        # 强制获取最新的 scroll viewport 宽度
        try:
            main_win = self.window()
            if main_win and hasattr(main_win, 'scroll'):
                vp_w = main_win.scroll.viewport().width()
                if vp_w > 400:
                    return max(360, vp_w - 30)
        except Exception:
            pass

        if self._max_text_w > 0:
            return self._max_text_w

        parent = self.parent()
        while parent is not None:
            gp = parent.parent()
            if isinstance(parent, QScrollArea):
                return max(360, parent.viewport().width() - 40)
            if gp is not None and isinstance(gp, QScrollArea):
                return max(360, gp.viewport().width() - 40)
            parent = gp

        w = self.width()
        if w > 200:
            return max(360, w - 40)
        return 720

    def set_text(self, text: str):
        # 不转义，直接使用 <div style> 包裹对齐
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if self.is_user:
            html = f'<div style="text-align: right;">{safe.replace(chr(10), "<br>")}</div>'
        else:
            html = f'<div style="text-align: left;">{safe.replace(chr(10), "<br>")}</div>'
        self.text_edit.setHtml(html)
        self._autosize()
        QTimer.singleShot(0, self._autosize)
        QTimer.singleShot(50, self._autosize)
        QTimer.singleShot(150, self._autosize)
        QTimer.singleShot(300, self._autosize)

    def append_text(self, delta: str):
        doc = self.text_edit.document()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(delta)
        self._autosize()

    def _autosize(self):
        doc = self.text_edit.document()
        tw = self._get_text_width()

        doc.setTextWidth(tw)
        self.text_edit.document().adjustSize()
        self.text_edit.update()

        ideal_h = int(doc.size().height())
        self.text_edit.setMinimumHeight(ideal_h)
        self.text_edit.setMaximumHeight(ideal_h)

        bubble_h = ideal_h + 48
        self.setMinimumHeight(bubble_h)
        self.setMaximumHeight(bubble_h)

        # 最关键：强制气泡宽度为容器宽度
        target_w = self._max_text_w if self._max_text_w > 0 else tw
        if target_w > 0:
            self.setFixedWidth(target_w)
            self.text_edit.setFixedWidth(target_w - 36)  # 减去 padding: 18*2

        self.updateGeometry()
        p = self.parentWidget()
        if p:
            p.updateGeometry()

    def showEvent(self, event):
        super().showEvent(event)
        self._autosize()
        # 窗口显示后，强制重新计算所有气泡的宽度
        # 因为此时 scroll.viewport().width() 才有正确的值
        main_win = self.window()
        if main_win and hasattr(main_win, 'scroll'):
            vp_w = main_win.scroll.viewport().width()
            if vp_w > 400:
                new_max = max(360, vp_w - 30)
                self._max_text_w = new_max
                # 重新获取正确的宽度并触发 autosize
                QTimer.singleShot(0, self._force_full_width)

    def _force_full_width(self):
        """强制设置气泡宽度为容器宽度。"""
        main_win = self.window()
        if main_win and hasattr(main_win, 'scroll'):
            vp_w = main_win.scroll.viewport().width()
            if vp_w > 400:
                new_max = max(360, vp_w - 30)
                self._max_text_w = new_max
                # 强制设置宽度
                self.setMinimumWidth(new_max)
                self.setMaximumWidth(new_max)
                # 重新设置文档宽度
                doc = self.text_edit.document()
                doc.setTextWidth(new_max)
                self.text_edit.document().adjustSize()
                self._autosize()
                # 通知父布局更新
                parent = self.parentWidget()
                if parent:
                    parent.updateGeometry()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._autosize()


# ---------------- 主窗口 ----------------
class MainWindow(QMainWindow):
    # 语音识别文本到达信号（子线程 → 主线程）
    voice_text_arrived = pyqtSignal(str)

    def __init__(self, user_name: str, llm: LLMClient):
        super().__init__()
        self.user_name = user_name
        self.llm = llm
        self.app_ctrl = AppController()
        self.searcher = BrowserSearcher(llm)
        # 初始化长期记忆系统
        self.memory = MemSkillManager(llm)
        # 初始化文件操作模块
        self.file_operator = FileOperator()
        # 初始化定时任务调度器
        self.scheduler = Scheduler()
        self.scheduler.set_on_task_execute(self._on_scheduler_task)
        self.scheduler.set_on_status_change(self._on_scheduler_status)
        self.agent = Agent(llm, self.app_ctrl, self.searcher,
                           memory=self.memory,
                           file_operator=self.file_operator,
                           scheduler=self.scheduler)

        self._worker = None
        self._current_assistant_bubble: ChatBubble | None = None
        self._maint_worker: MemoryMaintenanceWorker | None = None
        # 聊天历史持久化路径
        self._chat_history_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "data", "chat_history.json"
        )
        # TTS 管理器
        self._init_tts()
        self._build_ui()
        # 语音识别管理器（依赖 UI 组件，需在 _build_ui 之后初始化）
        self._init_speech()
        self._apply_settings_to_ui()
        # 加载情绪引擎配置
        self._init_emotion_settings()
        # 启动时恢复历史对话；若无历史则显示欢迎语
        restored = self._restore_chat_history()
        if not restored:
            self._add_welcome_message()
        # 启动定时记忆维护（每 24 小时一次，启动后 5 分钟首次执行）
        self._maint_timer = QTimer(self)
        self._maint_timer.timeout.connect(self._run_memory_maintenance)
        self._maint_timer.start(24 * 60 * 60 * 1000)
        QTimer.singleShot(5 * 60 * 1000, self._run_memory_maintenance)
        # 窗口显示后强制重新计算所有气泡宽度
        QTimer.singleShot(100, self._refresh_all_bubbles)
        QTimer.singleShot(500, self._refresh_all_bubbles)
        # 启动定时任务调度器
        self._init_scheduler_ui()
        self.scheduler.start()
        logger.info("定时任务调度器已启动")

    def _refresh_all_bubbles(self):
        """强制刷新所有气泡的宽度。"""
        vp_w = self.scroll.viewport().width()
        if vp_w > 400:
            new_max = max(360, vp_w - 30)
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, ChatBubble):
                    w._max_text_w = new_max
                    w._autosize()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 窗口大小变化时，重新计算所有气泡宽度
        vp_w = self.scroll.viewport().width()
        if vp_w > 400:
            new_max = max(360, vp_w - 30)
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                w = item.widget() if item else None
                if isinstance(w, ChatBubble):
                    w._max_text_w = new_max
                    w._autosize()
        # 调整调度器面板位置
        if hasattr(self, '_scheduler_panel') and self._scheduler_panel.isVisible():
            self._reposition_scheduler_panel()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        self.setWindowTitle("智能助手")
        self.resize(920, 680)
        self.setMinimumSize(720, 520)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(16, 18, 40))
        self.setPalette(pal)
        self.setStyleSheet("""
            QMainWindow, QWidget { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(10)

        # 顶部栏
        top = QFrame()
        top.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.06); border-radius: 14px; }"
        )
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setSpacing(12)

        self.lbl_title = QLabel("🤖 智能助手")
        tf = QFont(); tf.setPointSize(16); tf.setBold(True)
        self.lbl_title.setFont(tf)
        self.lbl_title.setStyleSheet("color: #ecf0ff;")

        self.lbl_user = QLabel()
        self.lbl_user.setStyleSheet("color: #b7c4ff; font-size: 13px;")

        tl.addWidget(self.lbl_title)
        tl.addWidget(self.lbl_user)
        tl.addStretch(1)

        # 模型选择
        self.lbl_model = QLabel("大模型：")
        self.lbl_model.setStyleSheet("color: #b7c4ff; font-size: 13px;")
        self.cmb_model = QComboBox()
        self.cmb_model.addItem("Ollama", "ollama")
        self.cmb_model.addItem("LM Studio", "lmstudio")
        self.cmb_model.addItem("自定义（OpenAI 兼容）", "custom")
        self.cmb_model.setStyleSheet(self._cmb_style())
        self.cmb_model.currentIndexChanged.connect(self._on_model_changed)
        tl.addWidget(self.lbl_model)
        tl.addWidget(self.cmb_model)

        # 设置按钮
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_settings.setStyleSheet(self._btn_tool_style())
        self.btn_settings.clicked.connect(self._open_settings)
        tl.addWidget(self.btn_settings)

        # 清空对话
        self.btn_clear = QPushButton("🧹 清空")
        self.btn_clear.setStyleSheet(self._btn_tool_style())
        self.btn_clear.clicked.connect(self._clear_chat)
        tl.addWidget(self.btn_clear)

        # 记忆管理
        self.btn_memory = QPushButton("🧠 记忆")
        self.btn_memory.setStyleSheet(self._btn_tool_style())
        self.btn_memory.setToolTip("查看和管理长期记忆")
        self.btn_memory.clicked.connect(self._open_memory)
        tl.addWidget(self.btn_memory)

        # TTS 朗读
        self.btn_tts = QPushButton("🔊 朗读")
        self.btn_tts.setStyleSheet(self._btn_tool_style())
        self.btn_tts.setToolTip("语音朗读 — 点击朗读最后一条回复")
        self.btn_tts.clicked.connect(self._toggle_tts)
        tl.addWidget(self.btn_tts)

        # 屏幕感知开关
        self.btn_screen = QPushButton("👁 屏幕感知")
        self.btn_screen.setCheckable(True)
        self.btn_screen.setChecked(True)
        self.btn_screen.setStyleSheet(self._btn_screen_style())
        self.btn_screen.setToolTip("开启后，执行桌面操作前自动感知屏幕状态，操作后自动验证结果")
        self.btn_screen.clicked.connect(self._toggle_screen_perception)
        tl.addWidget(self.btn_screen)

        # 小游戏中心
        self.btn_game = QPushButton("🎮 游戏")
        self.btn_game.setStyleSheet(self._btn_game_style())
        self.btn_game.setToolTip("小游戏中心 - AI 教练陪你下棋")
        self.btn_game.clicked.connect(self._open_game_center)
        tl.addWidget(self.btn_game)

        # 退出按钮
        self.btn_exit = QPushButton("✕ 退出")
        self.btn_exit.setStyleSheet(self._btn_exit_style())
        self.btn_exit.setToolTip("退出智能助手")
        self.btn_exit.clicked.connect(self._on_exit_clicked)
        tl.addWidget(self.btn_exit)

        root.addWidget(top)

        # 聊天区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollBar:vertical { width: 8px; background: transparent; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.18); border-radius: 4px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.30); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.chat_container = QWidget()
        # 确保容器能扩展填充 QScrollArea 的宽度
        self.chat_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(10, 10, 10, 18)
        self.chat_layout.setSpacing(14)
        self.chat_layout.addStretch(1)
        scroll.setWidget(self.chat_container)
        self.scroll = scroll
        root.addWidget(scroll, 1)

        # 语音识别模式面板
        voice_panel = QFrame()
        voice_panel.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.04); border-radius: 12px;"
            " border: 1px solid rgba(255,255,255,0.08); }"
        )
        vp_layout = QHBoxLayout(voice_panel)
        vp_layout.setContentsMargins(12, 6, 12, 6)
        vp_layout.setSpacing(8)

        lbl_voice_mode = QLabel("🎙 语音识别")
        lbl_voice_mode.setStyleSheet("color: #b7c4ff; font-size: 13px; font-weight: 600;")
        vp_layout.addWidget(lbl_voice_mode)

        self._voice_btn_group = QButtonGroup(self)
        self._voice_btn_group.setExclusive(True)

        self.rb_off = QRadioButton("关闭")
        self.rb_off.setChecked(True)
        self.rb_ptt = QRadioButton("实时对话")
        self.rb_wake = QRadioButton("唤醒词对话")

        for rb in (self.rb_off, self.rb_ptt, self.rb_wake):
            rb.setStyleSheet(
                "QRadioButton { color: #e6ecff; font-size: 12px; spacing: 5px; }"
                "QRadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; }"
                "QRadioButton::indicator:unchecked { border: 2px solid rgba(255,255,255,0.3); background: transparent; }"
                "QRadioButton::indicator:checked { border: 2px solid #4f8cff; background: #4f8cff; }"
            )
            self._voice_btn_group.addButton(rb)
            vp_layout.addWidget(rb)

        self._voice_btn_group.buttonClicked.connect(self._on_voice_mode_changed)

        # 语音状态指示灯
        self._voice_status = QLabel("")
        self._voice_status.setStyleSheet("color: #7ee7a8; font-size: 12px; padding-left: 10px;")
        vp_layout.addWidget(self._voice_status)

        # 情绪状态指示器
        self._emotion_indicator = QLabel("😊 平稳")
        self._emotion_indicator.setStyleSheet(
            "color: #7ee7a8; font-size: 12px; padding: 2px 8px; "
            "background: rgba(126, 231, 168, 0.1); border-radius: 10px;"
        )
        self._emotion_indicator.setToolTip(
            "当前情绪状态：\n😊 喜悦 | 😢 悲伤 | 😠 愤怒 | 😨 恐惧 | ❤️ 好感\n"
            "🤝 信任 | 💭 思念 | 😔 愧疚"
        )
        vp_layout.addWidget(self._emotion_indicator)

        vp_layout.addStretch(1)

        root.addWidget(voice_panel)

        # TTS 语音合成进度条
        self.tts_progress = QProgressBar()
        self.tts_progress.setVisible(False)
        self.tts_progress.setTextVisible(True)
        self.tts_progress.setFixedHeight(6)
        self.tts_progress.setRange(0, 100)
        self.tts_progress.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.06); border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #4f8cff; border-radius: 3px; }"
        )
        root.addWidget(self.tts_progress)

        # 输入区
        bottom = QFrame()
        bottom.setStyleSheet(
            "QFrame { background: rgba(255,255,255,0.06); border-radius: 16px; }"
        )
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(12, 10, 12, 10)
        bl.setSpacing(8)

        self.ed_input = QTextEdit()
        self.ed_input.setPlaceholderText("输入要做的事情，例如：帮我打开微信 / 搜一下最新的AI新闻 ...")
        self.ed_input.setMinimumHeight(70)
        self.ed_input.setMaximumHeight(180)
        self.ed_input.setStyleSheet(
            "QTextEdit { background: rgba(255,255,255,0.08); color: #f0f3ff; border-radius: 10px; padding: 10px;"
            " border: 1px solid rgba(255,255,255,0.15); font-size: 14px; }"
            "QTextEdit:focus { border: 1px solid #4f8cff; }"
        )
        self.ed_input.installEventFilter(self)
        bl.addWidget(self.ed_input)

        row = QHBoxLayout()
        self.lbl_tip = QLabel("Enter 发送，Shift+Enter 换行")
        self.lbl_tip.setStyleSheet("color: #98a8d6; font-size: 12px;")
        row.addWidget(self.lbl_tip)
        row.addStretch(1)

        self.btn_voice = QPushButton("🎤 说话")
        self.btn_voice.setStyleSheet(self._btn_voice_idle_style())
        self.btn_voice.setToolTip("语音识别：选择实时对话或唤醒词模式后自动启用")
        self.btn_voice.clicked.connect(self._toggle_voice_input)
        self.btn_voice.setEnabled(False)

        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setStyleSheet(self._btn_style("#d65c7c", "#b2445e"))
        self.btn_stop.clicked.connect(self._stop_generation)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()

        self.btn_send = QPushButton("🚀 发送")
        self.btn_send.setStyleSheet(self._btn_style("#4f8cff", "#3a6fd6"))
        self.btn_send.clicked.connect(self._send_message)
        row.addWidget(self.btn_voice)
        row.addWidget(self.btn_stop)
        row.addWidget(self.btn_send)

        bl.addLayout(row)
        root.addWidget(bottom)

    def _btn_style(self, normal, hover) -> str:
        return (
            f"QPushButton {{ padding: 9px 22px; border-radius: 10px; font-size: 14px; color: white;"
            f" background: {normal}; border: none; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QPushButton:disabled {{ background: rgba(255,255,255,0.2); color: rgba(255,255,255,0.6); }}"
        )

    def _btn_tool_style(self) -> str:
        return (
            "QPushButton { padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #e6ecff;"
            " background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); }"
            "QPushButton:hover { background: rgba(255,255,255,0.16); }"
        )

    def _btn_screen_style(self) -> str:
        return (
            "QPushButton { padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #7ee7a8;"
            " background: rgba(79,140,255,0.15); border: 1px solid #4f8cff; font-weight: 600; }"
            "QPushButton:hover { background: rgba(79,140,255,0.30); }"
            "QPushButton:checked { color: #ff9966; background: rgba(255,153,102,0.15); border: 1px solid #ff9966; }"
            "QPushButton:checked:hover { background: rgba(255,153,102,0.30); }"
        )

    def _btn_game_style(self) -> str:
        return (
            "QPushButton { padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #ffd700;"
            " background: rgba(255,215,0,0.12); border: 1px solid rgba(255,215,0,0.4); font-weight: 600; }"
            "QPushButton:hover { background: rgba(255,215,0,0.25); color: #ffe44d; }"
            "QPushButton:pressed { background: rgba(255,215,0,0.35); }"
        )

    def _btn_exit_style(self) -> str:
        return (
            "QPushButton { padding: 8px 14px; border-radius: 10px; font-size: 13px; color: #ff8a9a;"
            " background: rgba(255,100,120,0.12); border: 1px solid rgba(255,100,120,0.4); font-weight: 600; }"
            "QPushButton:hover { background: rgba(255,100,120,0.25); color: #ffb3be; }"
            "QPushButton:pressed { background: rgba(255,100,120,0.35); }"
        )

    def _cmb_style(self) -> str:
        return (
            "QComboBox { padding: 7px 12px; border-radius: 9px; background: rgba(255,255,255,0.10);"
            " color: #eef2ff; border: 1px solid rgba(255,255,255,0.18); font-size: 13px; min-width: 180px; }"
            "QComboBox:hover { background: rgba(255,255,255,0.15); }"
            "QComboBox QAbstractItemView { background: #202542; color: white; selection-background-color: #4f8cff;"
            " border: 1px solid rgba(255,255,255,0.15); padding: 4px; }"
        )

    def _btn_voice_idle_style(self) -> str:
        return (
            "QPushButton { padding: 9px 18px; border-radius: 10px; font-size: 14px; color: #e6ecff;"
            " background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); font-weight: 600; }"
            "QPushButton:hover { background: rgba(79,140,255,0.25); border-color: #4f8cff; }"
            "QPushButton:disabled { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.3); }"
        )

    def _btn_voice_active_style(self) -> str:
        return (
            "QPushButton { padding: 9px 18px; border-radius: 10px; font-size: 14px; color: white;"
            " background: #d65c7c; border: 1px solid #d65c7c; font-weight: 600; }"
            "QPushButton:hover { background: #e06a8a; }"
        )

    # ---------- 消息与发送 ----------
    def eventFilter(self, obj, event):
        if obj is self.ed_input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._send_message()
                return True
        return super().eventFilter(obj, event)

    def _apply_settings_to_ui(self):
        s = self.llm.settings
        uname = s.get("user_name") or self.user_name
        aname = s.get("ai_name") or "小智"
        self.lbl_user.setText(f"👤 {uname}  ·  💬 {aname}")
        self.user_name = uname
        self.ai_name = aname
        provider = s.get("llm_provider", "ollama")
        idx = {"ollama": 0, "lmstudio": 1, "custom": 2}.get(provider, 0)
        self.cmb_model.blockSignals(True)
        self.cmb_model.setCurrentIndex(idx)
        self.cmb_model.blockSignals(False)
        # 应用 TTS 配置
        self._apply_tts_settings()
        # 同步唤醒词（跟随 AI 名称）
        if hasattr(self, 'speech') and self.speech:
            wake_words = [aname, f"你好{aname}"]
            self.speech.set_wake_words(wake_words)
            # 更新状态栏文案
            if self.speech.get_mode() == MODE_WAKE_WORD:
                self._voice_status.setText(f"监听中... 说「{aname}」唤醒")

    def _init_tts(self):
        """初始化 TTS 管理器。"""
        s = self.llm.settings
        engine = s.get("tts_engine", "off")
        auto_play = s.get("tts_auto_play", False)
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
        self.tts = TTSManager(
            engine=engine,
            model_dir=model_dir,
            speaker_id=sid,
            auto_play=auto_play,
            volume=vol,
            cosyvoice_speaker=cosyvoice_speaker,
            cosyvoice_emotion=cosyvoice_emotion,
            sherpa_emotion=sherpa_emotion,
        )
        # 状态回调（更新按钮 + 进度条可见性）
        self.tts.set_on_state_change(self._on_tts_state_change)
        # 进度回调（更新进度条）
        self.tts.set_on_progress(self._on_tts_progress)

    def _apply_tts_settings(self):
        """从 settings 应用 TTS 配置到 TTSManager。"""
        if not hasattr(self, "tts"):
            return
        s = self.llm.settings
        engine = s.get("tts_engine", "off")
        # 根据引擎选择正确的模型目录
        if engine == "cosyvoice":
            model_dir = s.get("cosyvoice_model_dir", "") or s.get("tts_model_dir", "")
        else:
            model_dir = s.get("tts_model_dir", "")
        self.tts.set_engine(engine)
        self.tts.set_model_dir(model_dir)
        self.tts.set_speaker_id(s.get("tts_speaker_id", 0))
        self.tts.set_auto_play(s.get("tts_auto_play", False))
        self.tts.set_volume(s.get("tts_volume", 2.5))
        self.tts.set_cosyvoice_speaker(s.get("cosyvoice_speaker", "中文女"))
        self.tts.set_cosyvoice_emotion(s.get("cosyvoice_emotion", ""))
        self.tts.set_sherpa_emotion(s.get("sherpa_emotion", ""))
        # 更新按钮显示
        if hasattr(self, "btn_tts"):
            if engine == "off":
                self.btn_tts.setText("🔇 朗读")
                self.btn_tts.setToolTip("语音朗读（已关闭）")
            else:
                self.btn_tts.setText("🔊 朗读")
                self.btn_tts.setToolTip(f"语音朗读（{engine}）— 点击朗读最后一条回复")

    def _on_tts_state_change(self, playing: bool):
        """TTS 播放状态变化回调（在工作线程中调用）。"""
        try:
            QTimer.singleShot(0, lambda: self._update_tts_button(playing))
        except Exception:
            pass

    def _on_tts_progress(self, sentence_idx: int, total_sentences: int,
                         sample_pos: int, total_samples: int):
        """TTS 朗读进度回调（在工作线程中调用）。"""
        try:
            QTimer.singleShot(0, lambda: self._update_tts_progress(
                sentence_idx, total_sentences, sample_pos, total_samples))
        except Exception:
            pass

    def _update_tts_progress(self, sentence_idx: int, total_sentences: int,
                             sample_pos: int, total_samples: int):
        """更新进度条（主线程）。"""
        if not hasattr(self, "tts_progress"):
            return
        # 计算总体进度：句子进度(70%) + 当前句采样进度(30%)
        if total_sentences > 0:
            sentence_part = (sentence_idx / total_sentences) * 70
        else:
            sentence_part = 0
        if total_samples > 0:
            sample_part = (sample_pos / total_samples) * 30
        else:
            sample_part = 0
        pct = int(min(100, sentence_part + sample_part))
        self.tts_progress.setValue(pct)

    def _update_tts_button(self, playing: bool):
        """更新 TTS 按钮状态和进度条可见性（主线程）。"""
        if not hasattr(self, "btn_tts"):
            return
        if playing:
            self.btn_tts.setText("⏸ 停止朗读")
            self.tts_progress.setVisible(True)
            self.tts_progress.setValue(0)
        else:
            engine = self.llm.settings.get("tts_engine", "off")
            if engine == "off":
                self.btn_tts.setText("🔇 朗读")
            else:
                self.btn_tts.setText("🔊 朗读")
            self.tts_progress.setVisible(False)
            self.tts_progress.setValue(0)

    def _toggle_tts(self):
        """点击 TTS 按钮：正在播放则停止，否则朗读最后一条 AI 回复。"""
        if self.tts.is_playing():
            self.tts.stop()
            return
        # 找到最后一条 AI 消息气泡
        last_ai_text = ""
        for i in range(self.chat_layout.count() - 1, -1, -1):
            item = self.chat_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, ChatBubble) and not w.is_user:
                last_ai_text = w.text_edit.toPlainText()
                break
        if last_ai_text:
            self.tts.speak_immediately(last_ai_text)
        else:
            QMessageBox.information(self, "提示", "暂无 AI 回复可朗读。")

    def _toggle_screen_perception(self):
        """切换屏幕感知开关状态。"""
        enabled = self.btn_screen.isChecked()
        self.agent.screen_perception_enabled = enabled
        if enabled:
            self.btn_screen.setText("👁 屏幕感知")
            self.btn_screen.setToolTip("开启后，执行桌面操作前自动感知屏幕状态，操作后自动验证结果")
            self._add_system_message("屏幕感知已开启：操作前自动感知屏幕，操作后自动验证结果")
        else:
            self.btn_screen.setText("👁 屏幕感知 (关)")
            self.btn_screen.setToolTip("已关闭自动屏幕感知，Agent 将直接执行操作不进行前置/后置检查")
            self._add_system_message("屏幕感知已关闭：Agent 将直接执行操作，不进行自动屏幕感知和验证")

    def _add_welcome_message(self):
        aname = self.llm.settings.get("ai_name", "小智")
        msg = f"你好，{self.user_name}！我是{aname}，很高兴见到你。\n你可以让我：\n• 帮你打开或关闭电脑上的软件（支持中文名、英文名、拼音）\n• 搜索网页信息并自动打开最匹配的结果\n• 直接提问，我会尽力回答"
        self._add_bubble(msg, is_user=False)

    # ---------- 聊天历史持久化 ----------
    def _load_chat_history(self) -> list:
        """从文件加载历史对话列表。"""
        try:
            if os.path.exists(self._chat_history_path):
                with open(self._chat_history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
        except Exception:
            pass
        return []

    def _save_chat_history(self, history: list):
        """保存历史对话列表到文件。"""
        try:
            os.makedirs(os.path.dirname(self._chat_history_path), exist_ok=True)
            with open(self._chat_history_path, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _append_chat_message(self, role: str, content: str):
        """追加一条消息到历史文件。role: 'user' 或 'assistant'"""
        if not content.strip():
            return
        history = self._load_chat_history()
        history.append({
            "role": role,
            "content": content,
            "ts": QDateTime.currentDateTime().toString(Qt.DateFormat.ISODate)
        })
        # 限制最多保留 200 条，避免无限增长
        if len(history) > 200:
            history = history[-200:]
        self._save_chat_history(history)

    def _restore_chat_history(self) -> bool:
        """启动时从文件恢复历史对话到气泡和 agent 上下文。返回是否恢复了内容。"""
        history = self._load_chat_history()
        if not history:
            return False
        for msg in history:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                self._add_bubble(content, is_user=(role == "user"))
                # 同步到 agent 内部历史（供 LLM 上下文使用）
                self.agent._history.append({"role": role, "content": content})
        # 恢复后滚动到底部
        QTimer.singleShot(30, self._scroll_bottom)
        return True

    def _clear_chat_history_file(self):
        """清空历史对话文件。"""
        try:
            if os.path.exists(self._chat_history_path):
                self._save_chat_history([])
        except Exception:
            pass

    def _add_bubble(self, text: str, is_user: bool) -> ChatBubble:
        # 计算可靠的气泡最大宽度
        # 优先使用 scroll viewport 宽度，如果不可用则使用窗口宽度
        vp_w = self.scroll.viewport().width()
        if vp_w < 400:
            # 窗口还没有完全显示，使用窗口宽度作为回退
            vp_w = self.width() - 40  # 减去一些边距
        max_w = max(360, vp_w - 30)

        b = ChatBubble(text, is_user, max_width=max_w)
        # 插在stretch前面
        count = self.chat_layout.count()
        self.chat_layout.insertWidget(count - 1, b)
        # 多次延迟触发autosize，确保气泡获得正确尺寸
        QTimer.singleShot(10, b._autosize)
        QTimer.singleShot(60, b._autosize)
        QTimer.singleShot(150, b._autosize)
        QTimer.singleShot(300, b._autosize)
        QTimer.singleShot(500, b._autosize)  # 额外的延迟触发
        QTimer.singleShot(120, self._scroll_bottom)
        return b

    def _add_system_message(self, text: str):
        """添加系统提示消息（灰色小字，非气泡样式）。"""
        lbl = QLabel(f"— {text} —")
        lbl.setStyleSheet(
            "color: #7a8ab5; font-size: 11px; padding: 4px 12px;"
        )
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count = self.chat_layout.count()
        self.chat_layout.insertWidget(count - 1, lbl)
        QTimer.singleShot(100, self._scroll_bottom)

    def _scroll_bottom(self):
        self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())

    def _clear_chat(self):
        # 停止 TTS 朗读
        if hasattr(self, "tts") and self.tts:
            try:
                self.tts.stop()
            except Exception:
                pass
        # 移除bubble
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.agent.clear_history()
        # 同步清空持久化的历史文件
        self._clear_chat_history_file()
        self._add_welcome_message()

    def _send_message(self):
        if self._worker and self._worker.isRunning():
            return
        text = self.ed_input.toPlainText().strip()
        if not text:
            return
        self.ed_input.clear()
        self._add_bubble(text, is_user=True)
        # 持久化用户消息
        self._append_chat_message("user", text)
        self._current_assistant_bubble = self._add_bubble("🧠 思考中...", is_user=False)
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.show()

        stream = True
        self._worker = AgentWorker(self.agent, text, stream)
        self._worker.partial.connect(self._on_llm_partial)
        self._worker.done.connect(self._on_llm_done)
        self._worker.error.connect(self._on_llm_error)
        self._worker.start()
        # 开始流式 TTS（与 LLM 输出并行）
        if (self.tts and self.tts.auto_play
                and self.tts.engine_name != "off"):
            try:
                self.tts.speak_streaming_start()
            except Exception:
                pass

    def _on_llm_partial(self, delta: str):
        if not self._current_assistant_bubble:
            return
        # 第一次收到增量时，清掉占位
        if getattr(self, "_first_partial", True):
            self._current_assistant_bubble.set_text(delta)
            self._first_partial = False
        else:
            self._current_assistant_bubble.append_text(delta)
        self._scroll_bottom()
        # 流式朗读：把 LLM 增量追加到 TTS 队列
        if (self.tts and self.tts.auto_play and delta
                and self.tts.engine_name != "off"):
            try:
                self.tts.speak_streaming_append(delta)
            except Exception:
                pass

    def _on_llm_done(self, full_text: str, info: dict):
        self._first_partial = True
        if self._current_assistant_bubble:
            # 以防流的最后和full不一致
            self._current_assistant_bubble.set_text(full_text)
            self._current_assistant_bubble = None
            # 持久化助手回复
            if full_text.strip():
                self._append_chat_message("assistant", full_text)
        self.btn_send.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()
        self._scroll_bottom()
        # flush 流式朗读剩余文本（流式模式已在 _send_message 中启动）
        if (self.tts and self.tts.auto_play and full_text
                and full_text.strip()
                and self.tts.engine_name != "off"):
            try:
                self.tts.speak_streaming_flush()
            except Exception:
                pass
        # 处理队列中等待的语音消息
        QTimer.singleShot(300, self._drain_voice_queue)
        # 更新情绪指示器
        self._update_emotion_indicator()

    def _on_llm_error(self, err: str):
        self._first_partial = True
        err_msg = f"❌ 出错了：{err}\n请检查大模型配置和网络连接。"
        if self._current_assistant_bubble:
            self._current_assistant_bubble.set_text(err_msg)
            self._current_assistant_bubble = None
            # 持久化错误回复（避免重启后看到"思考中"占位）
            self._append_chat_message("assistant", err_msg)
        self.btn_send.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()
        self._scroll_bottom()
        # 错误提示朗读失败时停止 TTS，避免残留
        if self.tts:
            try:
                self.tts.stop()
            except Exception:
                pass
        # 处理队列中等待的语音消息
        QTimer.singleShot(300, self._drain_voice_queue)

    def _play_welcome_voice(self):
        """启动完成后播报欢迎语。"""
        if not self.tts or self.tts.engine_name == "off":
            return
        try:
            user_name = self.llm.settings.get("user_name", "用户") or "用户"
            # 只朗读欢迎语，不依赖 auto_play 开关（登录欢迎是独立行为）
            text = f"欢迎回来，{user_name}。"
            self.tts.speak(text)
        except Exception:
            pass

    def _stop_generation(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(1500)
        self.btn_send.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.hide()
        if self._current_assistant_bubble:
            try:
                if self._current_assistant_bubble.text_edit:
                    t = self._current_assistant_bubble.text_edit.toPlainText()
                    self._current_assistant_bubble.set_text(t + "\n（已停止生成）")
            except RuntimeError:
                pass  # QTextEdit 可能已被删除
            except Exception:
                pass
        self._first_partial = True
        # 停止 TTS 朗读，避免遗留音频
        if hasattr(self, "tts") and self.tts:
            try:
                self.tts.stop()
            except Exception:
                pass

    def _on_exit_clicked(self):
        """点击退出按钮：确认后退出程序。"""
        reply = QMessageBox.question(
            self, "退出确认",
            "确定要退出智能助手吗？\n所有正在进行的任务将被终止。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.close()

    def closeEvent(self, event):
        # 清理定时任务调度器
        if hasattr(self, "scheduler") and self.scheduler:
            try:
                self.scheduler.cleanup()
            except Exception:
                pass
        # 窗口关闭时清理 TTS 资源
        if hasattr(self, "tts") and self.tts:
            try:
                self.tts.shutdown()
            except Exception:
                pass
        # 清理语音资源
        if hasattr(self, "speech") and self.speech:
            try:
                self.speech.cleanup()
            except Exception:
                pass
        super().closeEvent(event)

    # ---------- 语音识别 ----------

    def _init_speech(self):
        """初始化语音识别管理器。"""
        s = self.llm.settings
        self.speech = SpeechManager()

        # 初始化声纹管理器
        self.voiceprint_mgr = VoiceprintManager()
        self.speech.set_voiceprint_manager(self.voiceprint_mgr)

        # 恢复声纹验证设置
        vp_enabled = s.get("voiceprint_enabled", False)
        vp_threshold = s.get("voiceprint_threshold", 0.4)  # 3D-Speaker CAM++ 模型推荐阈值
        self.speech.set_voiceprint_enabled(vp_enabled)
        self.speech.set_voiceprint_threshold(vp_threshold)
        logger.info(f"🔐 声纹设置已加载: 启用={vp_enabled}, 阈值={vp_threshold:.2f}")

        self.speech.set_on_state_change(self._on_speech_state)
        self.speech.set_on_final(self._on_speech_final)
        self.speech.set_on_partial(self._on_speech_partial)
        self.speech.set_on_error(self._on_speech_error)
        self.speech.set_on_voiceprint_fail(self._on_voiceprint_fail)
        # 连接语音信号到主线程处理槽
        self.voice_text_arrived.connect(self._deliver_voice_text)
        # 语音消息队列：当 LLM 正在处理时，缓存语音识别结果
        self._pending_voice_messages: list[str] = []
        # 根据 AI 名称自动生成唤醒词
        ai_name = s.get("ai_name", "小智")
        wake_words = [ai_name, f"你好{ai_name}"]
        self.speech.set_wake_words(wake_words)
        # 恢复上次的语音模式
        saved_mode = s.get("voice_mode", "off")
        mode_map = {
            "off": MODE_OFF,
            "push_to_talk": MODE_PUSH_TO_TALK,
            "wake_word": MODE_WAKE_WORD,
        }
        if saved_mode in mode_map:
            self.speech.set_mode(mode_map[saved_mode])
            # 更新单选按钮状态
            btn_map = {
                MODE_OFF: self.rb_off,
                MODE_PUSH_TO_TALK: self.rb_ptt,
                MODE_WAKE_WORD: self.rb_wake,
            }
            btn = btn_map.get(mode_map[saved_mode])
            if btn:
                btn.blockSignals(True)
                btn.setChecked(True)
                btn.blockSignals(False)
        self._update_voice_ui_for_mode(self.speech.get_mode())

    def _init_emotion_settings(self):
        """初始化情绪引擎配置。"""
        s = self.llm.settings
        emotion_enabled = s.get("emotion_enabled", True)
        self.agent.set_emotion_enabled(emotion_enabled)

        # 加载人格配置
        try:
            from modules.emotion_module import Personality
            personality = Personality(
                openness=s.get("personality_openness", 0.5),
                conscientiousness=s.get("personality_conscientiousness", 0.5),
                extraversion=s.get("personality_extraversion", 0.5),
                agreeableness=s.get("personality_agreeableness", 0.5),
                neuroticism=s.get("personality_neuroticism", 0.5),
            )
            self.agent.emotion.personality = personality
            self.agent.emotion.reset()
            logger.info(
                f"💫 情绪引擎已加载: 启用={emotion_enabled}, "
                f"openness={personality.openness:.2f}, "
                f"conscientiousness={personality.conscientiousness:.2f}, "
                f"extraversion={personality.extraversion:.2f}, "
                f"agreeableness={personality.agreeableness:.2f}, "
                f"neuroticism={personality.neuroticism:.2f}"
            )
        except Exception as e:
            logger.warning(f"加载情绪引擎配置失败: {e}")

        # 更新情绪指示器
        self._update_emotion_indicator()

    def _on_voice_mode_changed(self, btn):
        """语音识别模式切换。"""
        if btn == self.rb_off:
            self.speech.set_mode(MODE_OFF)
            self._save_voice_mode("off")
        elif btn == self.rb_ptt:
            self.speech.set_mode(MODE_PUSH_TO_TALK)
            self._save_voice_mode("push_to_talk")
        elif btn == self.rb_wake:
            self.speech.set_mode(MODE_WAKE_WORD)
            self._save_voice_mode("wake_word")
        self._update_voice_ui_for_mode(self.speech.get_mode())

    def _save_voice_mode(self, mode: str):
        """保存语音模式到配置。"""
        try:
            self.llm.settings["voice_mode"] = mode
            self.llm.save_settings()
        except Exception:
            pass

    def _update_voice_ui_for_mode(self, mode: str):
        """根据模式更新 UI 状态。"""
        if mode == MODE_OFF:
            self.btn_voice.setEnabled(False)
            self.btn_voice.setText("🎤 说话")
            self.btn_voice.setStyleSheet(self._btn_voice_idle_style())
            self._voice_status.setText("")
        elif mode == MODE_PUSH_TO_TALK:
            self.btn_voice.setEnabled(False)
            self.btn_voice.setText("🎤 聆听中")
            self.btn_voice.setStyleSheet(self._btn_voice_active_style())
            self._voice_status.setText("正在聆听，请说话...")
            self._voice_status.setStyleSheet("color: #7ee7a8; font-size: 12px; padding-left: 10px;")
        elif mode == MODE_WAKE_WORD:
            self.btn_voice.setEnabled(False)
            self.btn_voice.setText("🎤 唤醒词")
            self.btn_voice.setStyleSheet(self._btn_voice_idle_style())
            aname = self.llm.settings.get("ai_name", "小智")
            self._voice_status.setText(f"监听中... 说「{aname}」唤醒")
            self._voice_status.setStyleSheet("color: #4f8cff; font-size: 12px; padding-left: 10px;")

    def _toggle_voice_input(self):
        """切换语音输入 — 实时对话模式下由持续监听自动处理，此方法保留兼容。"""
        pass

    def _on_speech_state(self, state: str):
        """语音状态变化回调。"""
        aname = self.llm.settings.get("ai_name", "小智")
        state_map = {
            "idle": ("", "#7ee7a8"),
            "recording": ("🔴 录音中...", "#d65c7c"),
            "processing": ("⚙ 识别中...", "#f0c36c"),
            "listening": ("正在聆听，请说话...", "#7ee7a8"),
            "wake_detected": ("✨ 检测到唤醒词！", "#ffc857"),
            "loading_model": ("📦 加载模型中...", "#f0c36c"),
            "mode:off": ("", "#7ee7a8"),
            "mode:push_to_talk": ("正在聆听，请说话...", "#7ee7a8"),
            "mode:wake_word": (f"监听中... 说「{aname}」唤醒", "#4f8cff"),
        }
        if state in state_map:
            text, color = state_map[state]
            QTimer.singleShot(0, lambda: self._voice_status.setText(text))
            QTimer.singleShot(0, lambda: self._voice_status.setStyleSheet(
                f"color: {color}; font-size: 12px; padding-left: 10px;"))

    def _on_speech_final(self, text: str):
        """语音识别最终结果 — 通过信号安全地跨线程送达主线程。"""
        if text and text.strip():
            self.voice_text_arrived.emit(text)

    def _deliver_voice_text(self, text: str):
        """主线程中：将语音文本送达，发送或排队等待。"""
        if not text or not text.strip():
            return
        # 如果 LLM 正在处理，缓存这条消息
        if self._worker and self._worker.isRunning():
            self._pending_voice_messages.append(text)
            self._voice_status.setText(f"⏳ 已缓存 {len(self._pending_voice_messages)} 条消息，等待处理...")
            self._voice_status.setStyleSheet("color: #f0c36c; font-size: 12px; padding-left: 10px;")
            logger.info(f"语音消息已缓存（队列: {len(self._pending_voice_messages)}）: {text}")
            return
        # 直接发送
        self._send_voice_message(text)

    def _send_voice_message(self, text: str):
        """发送一条语音识别的消息给 AI。"""
        self.ed_input.setPlainText(text)
        self._send_message()
        # 如果发送后仍有 worker 在运行，检查队列
        self._drain_voice_queue()

    def _drain_voice_queue(self):
        """尝试将队列中缓存的语音消息发送出去。"""
        if self._pending_voice_messages and not (self._worker and self._worker.isRunning()):
            next_text = self._pending_voice_messages.pop(0)
            logger.info(f"从队列发送下一条语音消息: {next_text}")
            self._send_voice_message(next_text)

    def _on_speech_partial(self, text: str):
        """语音识别部分结果 — 显示在状态栏。"""
        if text:
            QTimer.singleShot(0, lambda: self._voice_status.setText(
                f"识别中: {text[:50]}..."))

    def _on_speech_error(self, error: str):
        """语音错误回调。"""
        QTimer.singleShot(0, lambda: self._voice_status.setText(f"❌ {error}"))
        QTimer.singleShot(0, lambda: self._voice_status.setStyleSheet(
            "color: #d65c7c; font-size: 12px; padding-left: 10px;"))

    def _on_voiceprint_fail(self, reason: str):
        """声纹验证失败回调。"""
        QTimer.singleShot(0, lambda: self._voice_status.setText(f"🔒 {reason}"))
        QTimer.singleShot(0, lambda: self._voice_status.setStyleSheet(
            "color: #ffb86c; font-size: 12px; padding-left: 10px;"))

    def _update_emotion_indicator(self):
        """更新情绪状态指示器。"""
        try:
            state = self.agent.get_emotion_state()
            if state.get("disabled"):
                self._emotion_indicator.setText("😊 情绪: 关闭")
                self._emotion_indicator.setStyleSheet(
                    "color: #98a8d6; font-size: 12px; padding: 2px 8px; "
                    "background: rgba(152, 168, 214, 0.1); border-radius: 10px;"
                )
                return

            dominant = state.get("dominant", "joy")
            dominant_cn = state.get("dominant_cn", "喜悦")
            dominant_emoji = state.get("dominant_emoji", "😊")
            trust = state.get("trust", 0)
            love = state.get("love", 0)

            # 构建显示文本
            indicator_text = f"{dominant_emoji} {dominant_cn}"
            if trust > 0.5:
                indicator_text += f" 🤝{trust:.2f}"
            if love > 0.5:
                indicator_text += f" ❤️{love:.2f}"

            # 根据主导情绪设置颜色
            color_map = {
                "joy": "#7ee7a8",      # 绿色
                "sadness": "#9fb5ff",  # 蓝色
                "anger": "#ff99a8",    # 红色
                "fear": "#ffb86c",     # 橙色
                "love": "#ff7eb3",     # 粉色
                "disgust": "#b388ff",  # 紫色
                "surprise": "#f0c36c", # 黄色
                "trust": "#7ee7a8",    # 绿色
                "longing": "#c586c0",  # 紫色
                "guilt": "#ff99a8",    # 红色
            }
            bg_map = {
                "joy": "rgba(126, 231, 168, 0.1)",
                "sadness": "rgba(159, 181, 255, 0.1)",
                "anger": "rgba(255, 153, 168, 0.1)",
                "fear": "rgba(255, 184, 108, 0.1)",
                "love": "rgba(255, 126, 179, 0.1)",
                "disgust": "rgba(179, 136, 255, 0.1)",
                "surprise": "rgba(240, 195, 108, 0.1)",
                "trust": "rgba(126, 231, 168, 0.1)",
                "longing": "rgba(197, 134, 192, 0.1)",
                "guilt": "rgba(255, 153, 168, 0.1)",
            }

            color = color_map.get(dominant, "#7ee7a8")
            bg = bg_map.get(dominant, "rgba(126, 231, 168, 0.1)")

            self._emotion_indicator.setText(indicator_text)
            self._emotion_indicator.setStyleSheet(
                f"color: {color}; font-size: 12px; padding: 2px 8px; "
                f"background: {bg}; border-radius: 10px;"
            )

            # 更新 tooltip
            felt = state.get("felt", {})
            tooltip_parts = ["当前情绪状态："]
            channel_labels = {
                "joy": "😊 喜悦", "sadness": "😢 悲伤", "anger": "😠 愤怒",
                "fear": "😨 恐惧", "love": "❤️ 好感", "disgust": "😖 厌恶",
                "surprise": "😮 惊讶", "trust": "🤝 信任",
                "longing": "💭 思念", "guilt": "😔 愧疚"
            }
            for ch, label in channel_labels.items():
                val = felt.get(ch, 0)
                if val > 0.1:
                    tooltip_parts.append(f"  {label}: {val:.2f}")
            tooltip_parts.append(f"\n信任度: {trust:.2f}")
            tooltip_parts.append(f"好感度: {love:.2f}")
            self._emotion_indicator.setToolTip("\n".join(tooltip_parts))

        except Exception as e:
            logger.warning(f"更新情绪指示器异常: {e}")

    def _handle_voice_text(self, text: str):
        """将语音识别文本填入输入框并自动发送。"""
        self.ed_input.setPlainText(text)
        self._send_message()

    # ---------- 定时任务调度器 ----------

    def _init_scheduler_ui(self):
        """初始化调度器相关 UI（状态显示 + 任务管理按钮）。"""
        # 在语音模式面板旁添加调度器状态
        self._scheduler_indicator = QLabel("⏰ 调度器: 运行中")
        self._scheduler_indicator.setStyleSheet(
            "color: #4f8cff; font-size: 12px; padding-left: 10px;")
        self._voice_status.parentWidget().layout().addWidget(self._scheduler_indicator)

        # 添加管理按钮到工具栏
        self.btn_scheduler = QPushButton("⏰ 定时")
        self.btn_scheduler.setStyleSheet("""
            QPushButton {
                background: #2d3748; color: #e2e8f0; border: 1px solid #4a5568;
                border-radius: 6px; padding: 6px 14px; font-size: 13px;
            }
            QPushButton:hover { background: #3d4758; border-color: #4f8cff; }
            QPushButton:pressed { background: #1a202c; }
        """)
        self.btn_scheduler.clicked.connect(self._toggle_scheduler_panel)
        # 插入到设置按钮旁边
        self.btn_scheduler.setParent(self._voice_status.parentWidget())
        layout = self._voice_status.parentWidget().layout()
        layout.insertWidget(layout.indexOf(self._scheduler_indicator), self.btn_scheduler)

        # 调度器面板（默认隐藏）
        self._scheduler_panel = QFrame()
        self._scheduler_panel.setStyleSheet("""
            QFrame {
                background: #1e2533; border: 1px solid #3a4556;
                border-radius: 8px; padding: 12px; margin-top: 8px;
            }
        """)
        panel_layout = QVBoxLayout(self._scheduler_panel)
        panel_layout.setSpacing(8)

        # 标题行
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<b style='color:#e2e8f0; font-size:14px;'>⏰ 定时任务管理</b>"))
        header_row.addStretch()
        btn_refresh = QPushButton("🔄")
        btn_refresh.setToolTip("刷新任务列表")
        btn_refresh.setStyleSheet(
            "QPushButton {background: transparent; border:1px solid #4a5568; color:#e2e8f0; "
            "border-radius:4px; padding:4px 8px;} QPushButton:hover {border-color:#4f8cff;}")
        btn_refresh.clicked.connect(self._refresh_scheduler_list)
        header_row.addWidget(btn_refresh)
        panel_layout.addLayout(header_row)

        # 任务列表区
        self._scheduler_list_widget = QWidget()
        self._scheduler_list_layout = QVBoxLayout(self._scheduler_list_widget)
        self._scheduler_list_layout.setSpacing(4)
        panel_layout.addWidget(self._scheduler_list_widget)

        # 添加面板到主窗口
        self._scheduler_panel.setParent(self)
        self._scheduler_panel.setFixedHeight(200)
        self._scheduler_panel.hide()

        self._refresh_scheduler_list()

    def _toggle_scheduler_panel(self):
        """切换调度器面板显示/隐藏。"""
        if self._scheduler_panel.isVisible():
            self._scheduler_panel.hide()
        else:
            self._refresh_scheduler_list()
            self._scheduler_panel.show()
            self._scheduler_panel.raise_()
            self._reposition_scheduler_panel()

    def _reposition_scheduler_panel(self):
        """调整调度器面板位置。"""
        if self._scheduler_panel.isVisible():
            geo = self.geometry()
            panel_w = 500
            panel_x = geo.width() - panel_w - 20
            panel_y = 80
            self._scheduler_panel.setGeometry(panel_x, panel_y, panel_w, 200)

    def _refresh_scheduler_list(self):
        """刷新定时任务列表显示。"""
        # 清空现有
        while self._scheduler_list_layout.count():
            item = self._scheduler_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tasks = self.scheduler.list_tasks()
        if not tasks:
            empty = QLabel("  暂无定时任务。你可以让我创建定时任务，如：\n  「每天早上8点播报天气」\n  「每30分钟检查一次邮件」")
            empty.setStyleSheet("color: #718096; font-size: 12px; padding: 8px;")
            empty.setWordWrap(True)
            self._scheduler_list_layout.addWidget(empty)
            return

        for task in tasks:
            row = QHBoxLayout()
            row.setSpacing(8)

            status = "✅" if task.get("enabled") else "❌"
            trigger = task.get("cron") or f"每{task.get('interval_minutes',0)}分钟" or task.get("run_at", "")
            label_text = f"{status} <b>{task['name']}</b> | {trigger} | 下次: {task.get('next_run','?')}"
            label = QLabel(label_text)
            label.setStyleSheet("color: #e2e8f0; font-size: 12px;")
            label.setToolTip(f"指令: {task['prompt']}")
            row.addWidget(label, 1)

            # 操作按钮
            btn_toggle = QPushButton("🟢" if task.get("enabled") else "🔴")
            btn_toggle.setFixedSize(24, 24)
            btn_toggle.setToolTip("启用/禁用")
            btn_toggle.clicked.connect(lambda _=False, tid=task['task_id']: self._toggle_task(tid))
            row.addWidget(btn_toggle)

            btn_run = QPushButton("▶")
            btn_run.setFixedSize(24, 24)
            btn_run.setToolTip("立即执行")
            btn_run.clicked.connect(lambda _=False, tid=task['task_id']: self._run_task(tid))
            row.addWidget(btn_run)

            btn_del = QPushButton("🗑")
            btn_del.setFixedSize(24, 24)
            btn_del.setToolTip("删除")
            btn_del.setStyleSheet("QPushButton {background:transparent; border:none;}")
            btn_del.clicked.connect(lambda _=False, tid=task['task_id']: self._delete_task(tid))
            row.addWidget(btn_del)

            self._scheduler_list_layout.addLayout(row)

    def _toggle_task(self, task_id: str):
        """切换任务启用状态。"""
        task = self.scheduler.get_task(task_id)
        if task:
            self.scheduler.toggle_task(task_id, enabled=not task.get("enabled", True))
            self._refresh_scheduler_list()

    def _run_task(self, task_id: str):
        """立即执行任务。"""
        self.scheduler.run_task_now(task_id)

    def _delete_task(self, task_id: str):
        """删除任务。"""
        reply = QMessageBox.question(
            self, "删除任务", f"确定删除此定时任务吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.scheduler.remove_task(task_id)
            self._refresh_scheduler_list()

    def _on_scheduler_task(self, prompt: str):
        """定时任务触发回调 — AI 主动向用户发送消息。"""
        QTimer.singleShot(0, lambda: self._run_scheduler_prompt(prompt))

    def _run_scheduler_prompt(self, prompt: str):
        """在主线程中执行调度器任务 — AI 主动发话。"""
        if self._worker and self._worker.isRunning():
            self._pending_voice_messages.append(prompt)
            return

        # AI 主动发话：不显示用户气泡，直接显示 AI 气泡
        self._current_assistant_bubble = self._add_bubble("⏰ 定时任务触发...", is_user=False)
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.show()

        # 用 system 级提示让 LLM 把 prompt 作为自己要说的话
        scheduled_text = f"（定时任务触发）请以自然的语气向用户说：{prompt}。直接说这句话即可，不要加其他解释。"
        self._worker = AgentWorker(self.agent, scheduled_text, stream=True)
        self._worker.partial.connect(self._on_llm_partial)
        self._worker.done.connect(self._on_llm_done)
        self._worker.error.connect(self._on_llm_error)
        self._worker.start()

        # TTS 朗读
        if (self.tts and self.tts.auto_play
                and self.tts.engine_name != "off"):
            try:
                self.tts.speak_streaming_start()
            except Exception:
                pass

    def _on_scheduler_status(self, msg: str):
        """调度器状态变化回调。"""
        QTimer.singleShot(0, lambda: self._scheduler_indicator.setText(f"⏰ {msg}"))

    def _send_message_text(self, text: str):
        """直接发送文本到 Agent（不经过输入框）。"""
        if self._worker and self._worker.isRunning():
            self._pending_voice_messages.append(text)
            return
        self._add_bubble(text, is_user=True)
        self._append_chat_message("user", text)
        self._current_assistant_bubble = self._add_bubble("🧠 思考中...", is_user=False)
        self.btn_send.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.show()
        self._worker = AgentWorker(self.agent, text, stream=True)
        self._worker.partial.connect(self._on_llm_partial)
        self._worker.done.connect(self._on_llm_done)
        self._worker.error.connect(self._on_llm_error)
        self._worker.start()

    # ---------- 模型切换 / 设置 ----------
    def _on_model_changed(self, _idx):
        key = self.cmb_model.currentData()
        if key and key != self.llm.get_provider():
            self.llm.set_provider(key)

    def _open_settings(self):
        dlg = SettingsDialog(self.llm, voiceprint_mgr=self.voiceprint_mgr, parent=self)
        dlg.settings_changed.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self, new_settings: dict):
        self._apply_settings_to_ui()
        
        # 同步声纹验证设置
        vp_enabled = new_settings.get("voiceprint_enabled", False)
        vp_threshold = new_settings.get("voiceprint_threshold", 0.6)
        self.speech.set_voiceprint_enabled(vp_enabled)
        self.speech.set_voiceprint_threshold(vp_threshold)
        logger.info(f"🔐 声纹设置已同步: 启用={vp_enabled}, 阈值={vp_threshold:.2f}")
        
        # 同步情绪引擎设置
        emotion_enabled = new_settings.get("emotion_enabled", True)
        self.agent.set_emotion_enabled(emotion_enabled)
        # 更新人格配置
        try:
            from modules.emotion_module import EmotionEngine, Personality
            personality = Personality(
                openness=new_settings.get("personality_openness", 0.5),
                conscientiousness=new_settings.get("personality_conscientiousness", 0.5),
                extraversion=new_settings.get("personality_extraversion", 0.5),
                agreeableness=new_settings.get("personality_agreeableness", 0.5),
                neuroticism=new_settings.get("personality_neuroticism", 0.5),
            )
            self.agent.emotion.personality = personality
            self.agent.emotion.reset()
        except Exception as e:
            logger.warning(f"应用人格配置失败: {e}")
        # 更新情绪指示器
        self._update_emotion_indicator()

    # ---------- 记忆管理 ----------
    def _open_memory(self):
        """打开记忆管理对话框。"""
        dlg = MemoryDialog(self.memory, self)
        dlg.exec()

    # ---------- 小游戏中心 ----------
    def _open_game_center(self):
        """打开小游戏中心。"""
        from ui.game_center_dialog import GameCenterDialog
        dlg = GameCenterDialog(self.llm, self)
        dlg.exec()

    def _run_memory_maintenance(self):
        """后台执行记忆维护（合并 + 衰减）。"""
        if self._maint_worker and self._maint_worker.isRunning():
            return
        self._maint_worker = MemoryMaintenanceWorker(self.memory)
        self._maint_worker.done.connect(self._on_maintenance_done)
        self._maint_worker.start()

    def _on_maintenance_done(self, stats: dict):
        """维护完成回调。"""
        merged = stats.get("merged", 0)
        forgotten = stats.get("forgotten", 0)
        if merged > 0 or forgotten > 0:
            # 可选：在聊天区提示
            pass

    # 渐变背景
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0.0, QColor(12, 14, 34))
        grad.setColorAt(0.5, QColor(18, 18, 46))
        grad.setColorAt(1.0, QColor(14, 20, 42))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()
