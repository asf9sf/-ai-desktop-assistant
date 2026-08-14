import os
import json
import time
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPalette, QIcon
from PyQt6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QTextEdit, QComboBox, QPushButton,
    QVBoxLayout, QHBoxLayout, QFormLayout, QFrame, QTabWidget,
    QWidget, QMessageBox, QSpinBox, QFileDialog, QSlider, QCheckBox,
    QProgressBar, QListWidget, QListWidgetItem,
)

from modules.llm_module import LLMClient
from modules.tts_module import check_dependencies, find_sherpa_models
from modules.voiceprint_module import VoiceprintManager, ENROLLMENT_TEMPLATES, RECORD_DURATION


class SettingsDialog(QDialog):
    """设置对话框：通用设置 + 模型配置。"""

    settings_changed = pyqtSignal(dict)  # 新的settings
    _deps_checked = pyqtSignal(dict)  # 依赖检查完成信号

    # 缓存依赖检查结果（类级别，避免重复导入）
    _cached_deps = None

    def __init__(self, llm: LLMClient, voiceprint_mgr: VoiceprintManager = None, parent=None):
        super().__init__(parent)
        self.llm = llm
        self.voiceprint_mgr = voiceprint_mgr
        self.setWindowTitle("设置")
        self.resize(620, 560)
        self._working = dict(json.loads(json.dumps(llm.settings)))  # 深拷贝
        self._enrolling = False
        self._enroll_start_time = 0
        self._deps_widgets = {}  # 存储依赖显示控件
        self._build_ui()
        # 延迟检查依赖，避免阻塞主线程
        self._deps_checked.connect(self._on_deps_checked)
        if SettingsDialog._cached_deps is not None:
            # 使用缓存结果
            QTimer.singleShot(100, lambda: self._on_deps_checked(SettingsDialog._cached_deps))
        else:
            # 后台线程检查
            QTimer.singleShot(100, self._check_deps_background)

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog, QWidget {
                font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
                color: #e8ecff;
            }
            QTabWidget::pane { border: 1px solid rgba(255,255,255,20); border-radius: 10px; top:-1px; }
            QTabBar::tab {
                background: rgba(255,255,255,8); padding: 8px 18px; border-radius: 8px;
                margin-right: 4px; color: #cdd6ff;
            }
            QTabBar::tab:selected { background: #4f8cff; color: white; }
            QLabel { background: transparent; font-size: 14px; }
            QLineEdit, QTextEdit, QComboBox {
                background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,25);
                border-radius: 8px; padding: 8px 10px; color: white; font-size: 14px;
                selection-background-color: #4f8cff;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #4f8cff; }
            QPushButton {
                padding: 9px 20px; border-radius: 8px; font-size: 14px;
                color: white; border: none; font-weight: 600;
            }
            QPushButton#primary { background: #4f8cff; }
            QPushButton#primary:hover { background: #3a6fd6; }
            QPushButton#ghost   { background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,25); }
            QPushButton#ghost:hover { background: rgba(255,255,255,18); }
            QPushButton#danger  { background: rgba(255,90,110,0.2); color: #ff99a8; border: 1px solid rgba(255,90,110,0.5); }
            QPushButton#danger:hover { background: rgba(255,90,110,0.35); }
        """)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(16, 20, 40))
        self.setPalette(pal)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        root.setSpacing(14)

        tabs = QTabWidget()
        tabs.addTab(self._tab_general(), "通用")
        tabs.addTab(self._tab_llm(), "大模型")
        tabs.addTab(self._tab_tts(), "语音朗读")
        tabs.addTab(self._tab_voiceprint(), "声纹识别")
        tabs.addTab(self._tab_emotion(), "情绪引擎")
        root.addWidget(tabs, 1)

        # 底部按钮
        btns = QHBoxLayout()
        btns.addStretch(1)
        reset = QPushButton("恢复默认")
        reset.setObjectName("ghost")
        reset.clicked.connect(self._reset_defaults)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        btns.addWidget(reset)
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    def _tab_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setSpacing(12)
        form.setContentsMargins(14, 14, 14, 14)

        self.ed_user = QLineEdit(self._working.get("user_name", ""))
        self.ed_ai = QLineEdit(self._working.get("ai_name", ""))
        self.ed_persona = QTextEdit(self._working.get("ai_persona", ""))
        self.ed_persona.setMinimumHeight(180)

        form.addRow(QLabel("用户名称："), self.ed_user)
        form.addRow(QLabel("助手名称："), self.ed_ai)
        form.addRow(QLabel("助手人设（System Prompt）："), self.ed_persona)
        return w

    def _tab_llm(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)

        # 当前供应商选择
        row0 = QHBoxLayout()
        row0.addWidget(QLabel("当前大模型："))
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["ollama", "lmstudio", "custom"])
        cur = self._working.get("llm_provider", "ollama")
        if cur not in ("ollama", "lmstudio", "custom"):
            cur = "ollama"
        self.cmb_provider.setCurrentText(cur)
        self.cmb_provider.currentTextChanged.connect(self._on_provider_changed)
        row0.addWidget(self.cmb_provider, 1)
        lay.addLayout(row0)

        tip = QLabel("⚠️ LM Studio / Ollama 需要先在本地启动服务。\n自定义支持所有 OpenAI 兼容格式的接口。")
        tip.setStyleSheet("color: #9fb5ff;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # 每个供应商的配置面板
        self.panels: dict[str, QWidget] = {}
        self.cfg_widgets: dict[str, dict] = {}
        stack = QWidget()
        self.stack_layout = QVBoxLayout(stack)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)

        for key in ("ollama", "lmstudio", "custom"):
            panel, wmap = self._build_llm_config_panel(key)
            self.panels[key] = panel
            self.cfg_widgets[key] = wmap
            self.stack_layout.addWidget(panel)

        lay.addWidget(stack, 1)
        self._on_provider_changed(self.cmb_provider.currentText())
        return w

    def _build_llm_config_panel(self, key: str):
        w = QFrame()
        w.setStyleSheet(
            "QFrame { background: rgba(255,255,255,5); border-radius: 10px; }"
        )
        form = QFormLayout(w)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(12)

        cfg = self._working.get("llm_configs", {}).get(key, {})

        ed_base = QLineEdit(cfg.get("base_url", ""))
        ed_key = QLineEdit(cfg.get("api_key", ""))
        ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        ed_model = QLineEdit(cfg.get("model", ""))

        titles = {
            "ollama": "Ollama 配置（默认端口11434）",
            "lmstudio": "LM Studio 配置（默认端口1234）",
            "custom": "自定义（OpenAI 兼容格式）",
        }
        title = QLabel(titles[key])
        tf = QFont(); tf.setPointSize(13); tf.setBold(True)
        title.setFont(tf)
        form.addRow(title)

        form.addRow(QLabel("Base URL："), ed_base)
        form.addRow(QLabel("API Key："), ed_key)
        form.addRow(QLabel("模型名称："), ed_model)

        hint_map = {
            "ollama": "使用前请确保执行：ollama serve，并且已拉取模型（如 ollama pull qwen2.5:7b）",
            "lmstudio": "在 LM Studio 中开启 Local Server，并加载模型后再使用。",
            "custom": "所有遵循 OpenAI /v1/chat/completions 格式的接口均可，例如 DeepSeek / 通义千问 / Kimi / 自建网关。",
        }
        hint = QLabel(hint_map[key])
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9fb5ff;")
        form.addRow(hint)

        wmap = {"base_url": ed_base, "api_key": ed_key, "model": ed_model}
        return w, wmap

    def _on_provider_changed(self, key: str):
        for k, panel in self.panels.items():
            panel.setVisible(k == key)

    def _tab_tts(self) -> QWidget:
        """TTS 语音朗读配置页。"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # 依赖状态（先显示加载中，后台线程检查后更新）
        dep_box = QFrame()
        dep_box.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        dep_lay = QVBoxLayout(dep_box)
        dep_lay.setContentsMargins(12, 10, 12, 10)
        dep_title = QLabel("📦 依赖状态")
        dep_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        dep_lay.addWidget(dep_title)

        # 加载中提示
        self.lbl_deps_loading = QLabel("⏳ 正在检查依赖...")
        self.lbl_deps_loading.setStyleSheet("color: #9fb5ff; font-size: 13px;")
        dep_lay.addWidget(self.lbl_deps_loading)

        # 依赖项显示（初始隐藏，检查完成后显示）
        self.deps_container = QWidget()
        deps_lay = QVBoxLayout(self.deps_container)
        deps_lay.setContentsMargins(0, 0, 0, 0)
        deps_lay.setSpacing(4)

        dep_items_config = [
            ("cosyvoice", "CosyVoice 情感TTS（支持情感控制）"),
            ("torchaudio", "音频处理（CosyVoice 必需）"),
            ("sherpa-onnx", "高质量离线 TTS（备选）"),
            ("sounddevice", "音频播放必需"),
            ("numpy", "数值计算必需"),
            ("pyttsx3", "系统 TTS 兜底"),
        ]
        self._deps_widgets = {}
        for key, desc in dep_items_config:
            row = QHBoxLayout()
            icon_lbl = QLabel("⏳")
            icon_lbl.setMinimumWidth(24)
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_lbl = QLabel(key)
            name_lbl.setStyleSheet("font-family: monospace;")
            name_lbl.setMinimumWidth(140)
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: #9fb5ff; font-size: 12px;")
            row.addWidget(icon_lbl)
            row.addWidget(name_lbl)
            row.addWidget(desc_lbl, 1)
            deps_lay.addLayout(row)
            self._deps_widgets[key] = (icon_lbl, name_lbl)

        self.deps_container.setVisible(False)
        dep_lay.addWidget(self.deps_container)

        # 未就绪提示（初始隐藏）
        self.deps_tip_widget = QWidget()
        tip_lay = QVBoxLayout(self.deps_tip_widget)
        tip_lay.setContentsMargins(0, 6, 0, 0)
        tip = QLabel("⚠️ 建议安装 CosyVoice 或 Sherpa：\n"
                     "pip install modelscope transformers torchaudio sounddevice numpy")
        tip.setStyleSheet("color: #ffb86c; font-size: 12px;")
        tip.setWordWrap(True)
        tip_lay.addWidget(tip)
        install_btn = QPushButton("一键安装 TTS 依赖")
        install_btn.setObjectName("primary")
        install_btn.clicked.connect(self._install_tts_deps)
        tip_lay.addWidget(install_btn)
        self.deps_tip_widget.setVisible(False)
        dep_lay.addWidget(self.deps_tip_widget)

        lay.addWidget(dep_box)

        # 引擎选择
        engine_row = QHBoxLayout()
        engine_row.addWidget(QLabel("TTS 引擎："))
        self.cmb_tts_engine = QComboBox()
        self.cmb_tts_engine.addItem("关闭朗读", "off")
        self.cmb_tts_engine.addItem("系统 TTS（pyttsx3）", "system")
        self.cmb_tts_engine.addItem("Sherpa-ONNX（高质量）", "sherpa")
        self.cmb_tts_engine.addItem("CosyVoice（情感TTS）", "cosyvoice")
        cur_engine = self._working.get("tts_engine", "system")
        idx_map = {"off": 0, "system": 1, "sherpa": 2, "cosyvoice": 3}
        self.cmb_tts_engine.setCurrentIndex(idx_map.get(cur_engine, 1))
        self.cmb_tts_engine.currentIndexChanged.connect(self._on_tts_engine_changed)
        engine_row.addWidget(self.cmb_tts_engine, 1)
        lay.addLayout(engine_row)

        # 自动朗读
        self.chk_tts_auto = QCheckBox("AI 回复后自动朗读")
        self.chk_tts_auto.setChecked(self._working.get("tts_auto_play", False))
        lay.addWidget(self.chk_tts_auto)

        # Sherpa 模型目录
        self.tts_sherpa_panel = QFrame()
        self.tts_sherpa_panel.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        sp_lay = QVBoxLayout(self.tts_sherpa_panel)
        sp_lay.setContentsMargins(12, 10, 12, 10)
        sp_title = QLabel("🎙️ Sherpa-ONNX 模型配置")
        sp_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        sp_lay.addWidget(sp_title)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("模型目录："))
        self.ed_tts_model = QLineEdit(self._working.get("tts_model_dir", ""))
        model_row.addWidget(self.ed_tts_model, 1)
        browse = QPushButton("浏览…")
        browse.setObjectName("ghost")
        browse.clicked.connect(self._browse_tts_model)
        model_row.addWidget(browse)
        scan = QPushButton("自动扫描")
        scan.setObjectName("ghost")
        scan.clicked.connect(self._scan_tts_models)
        model_row.addWidget(scan)
        sp_lay.addLayout(model_row)

        # 已发现模型列表
        self.lbl_tts_models = QLabel("")
        self.lbl_tts_models.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        self.lbl_tts_models.setWordWrap(True)
        sp_lay.addWidget(self.lbl_tts_models)

        # 说话人 ID
        sid_row = QHBoxLayout()
        sid_row.addWidget(QLabel("说话人 ID："))
        self.sp_tts_sid = QSpinBox()
        self.sp_tts_sid.setRange(0, 4)
        self.sp_tts_sid.setValue(self._working.get("tts_speaker_id", 0))
        sid_row.addWidget(self.sp_tts_sid)
        self.lbl_tts_sid_info = QLabel("")
        self.lbl_tts_sid_info.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        sid_row.addWidget(self.lbl_tts_sid_info)
        sid_row.addStretch(1)
        sp_lay.addLayout(sid_row)

        # 音量调节
        vol_row = QHBoxLayout()
        vol_row.addWidget(QLabel("音量增益："))
        self.sl_tts_volume = QSlider(Qt.Orientation.Horizontal)
        self.sl_tts_volume.setRange(5, 50)  # 5~50 → 0.5x ~ 5.0x
        default_vol = int(self._working.get("tts_volume", 2.5) * 10)
        self.sl_tts_volume.setValue(max(5, min(50, default_vol)))
        self.sl_tts_volume.setMinimumWidth(200)
        self.lbl_tts_volume = QLabel("")
        self.lbl_tts_volume.setStyleSheet("color: #9fb5ff; font-size: 12px; min-width: 60px;")
        self.sl_tts_volume.valueChanged.connect(
            lambda v: self.lbl_tts_volume.setText(f"{v / 10:.1f}x"))
        # 初始化显示
        self.lbl_tts_volume.setText(f"{self.sl_tts_volume.value() / 10:.1f}x")
        vol_row.addWidget(self.sl_tts_volume, 1)
        vol_row.addWidget(self.lbl_tts_volume)
        lay.addLayout(vol_row)

        # 情感标签选择（Sherpa VITS 支持情感标签）
        emo_row = QHBoxLayout()
        emo_row.addWidget(QLabel("情感标签："))
        self.cmb_sherpa_emotion = QComboBox()
        self.cmb_sherpa_emotion.addItem("默认（无情感）", "")
        self.cmb_sherpa_emotion.addItem("😊 开心", "happy")
        self.cmb_sherpa_emotion.addItem("😢 悲伤", "sad")
        self.cmb_sherpa_emotion.addItem("😡 愤怒", "angry")
        self.cmb_sherpa_emotion.addItem("😨 恐惧", "fearful")
        self.cmb_sherpa_emotion.addItem("😲 惊讶", "surprised")
        self.cmb_sherpa_emotion.addItem("🤢 厌恶", "disgusted")
        saved_emo = self._working.get("sherpa_emotion", "")
        idx = self.cmb_sherpa_emotion.findData(saved_emo)
        if idx >= 0:
            self.cmb_sherpa_emotion.setCurrentIndex(idx)
        emo_row.addWidget(self.cmb_sherpa_emotion, 1)
        sp_lay.addLayout(emo_row)

        tip = QLabel("💡 模型下载：https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models\n"
                     "推荐：vits-zh-aishell3（多说话人+情感）或 vits-zh-ll（轻量）\n"
                     "🎭 支持情感标签：开心、悲伤、愤怒、恐惧等")
        tip.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        tip.setWordWrap(True)
        sp_lay.addWidget(tip)

        lay.addWidget(self.tts_sherpa_panel)

        # CosyVoice 情感TTS 配置面板
        self.tts_cosyvoice_panel = QFrame()
        self.tts_cosyvoice_panel.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        cv_lay = QVBoxLayout(self.tts_cosyvoice_panel)
        cv_lay.setContentsMargins(12, 10, 12, 10)
        cv_lay.setSpacing(10)

        cv_title = QLabel("🎭 CosyVoice 情感TTS 配置")
        cv_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        cv_lay.addWidget(cv_title)

        # 模型目录
        cv_model_row = QHBoxLayout()
        cv_model_row.addWidget(QLabel("模型目录："))
        self.ed_cosyvoice_model = QLineEdit(self._working.get("cosyvoice_model_dir", ""))
        cv_model_row.addWidget(self.ed_cosyvoice_model, 1)
        cv_browse = QPushButton("浏览…")
        cv_browse.setObjectName("ghost")
        cv_browse.clicked.connect(self._browse_cosyvoice_model)
        cv_model_row.addWidget(cv_browse)
        cv_lay.addLayout(cv_model_row)

        # 说话人选择
        spk_row = QHBoxLayout()
        spk_row.addWidget(QLabel("说话人："))
        self.cmb_cosyvoice_speaker = QComboBox()
        self.cmb_cosyvoice_speaker.addItems([
            "中文女", "中文男", "英文女", "英文男",
            "粤语女", "粤语男", "四川话", "东北话",
        ])
        saved_speaker = self._working.get("cosyvoice_speaker", "中文女")
        idx = self.cmb_cosyvoice_speaker.findText(saved_speaker)
        if idx >= 0:
            self.cmb_cosyvoice_speaker.setCurrentIndex(idx)
        spk_row.addWidget(self.cmb_cosyvoice_speaker, 1)
        cv_lay.addLayout(spk_row)

        # 情感指令
        emo_row = QHBoxLayout()
        emo_row.addWidget(QLabel("情感指令："))
        self.ed_cosyvoice_emotion = QLineEdit(self._working.get("cosyvoice_emotion", ""))
        self.ed_cosyvoice_emotion.setPlaceholderText("如：开心地、温柔地、愤怒地、悲伤地…")
        emo_row.addWidget(self.ed_cosyvoice_emotion, 1)
        cv_lay.addLayout(emo_row)

        # 情感快捷按钮
        emo_presets = QHBoxLayout()
        emo_presets.addWidget(QLabel("快捷情感："))
        emotions = [
            ("😊 开心", "开心地"),
            ("😢 悲伤", "悲伤地，低沉的"),
            ("😡 愤怒", "愤怒地，激动地"),
            ("😍 温柔", "温柔地，轻声地"),
            ("😨 恐惧", "恐惧地，颤抖地"),
            ("😴 平静", "平静地，舒缓地"),
        ]
        for label, emo in emotions:
            btn = QPushButton(label)
            btn.setObjectName("ghost")
            btn.setFixedHeight(26)
            btn.setStyleSheet("font-size: 12px;")
            btn.clicked.connect(lambda checked, e=emo: self.ed_cosyvoice_emotion.setText(e))
            emo_presets.addWidget(btn)
        emo_presets.addStretch(1)
        cv_lay.addLayout(emo_presets)

        # 提示
        cv_tip = QLabel("💡 CosyVoice 支持自然语言情感控制。在「情感指令」中描述你想要的语气，如：\n"
                        "• 开心地、充满活力地\n"
                        "• 温柔地、轻声细语地\n"
                        "• 愤怒地、激动地\n"
                        "• 悲伤地、低沉地\n\n"
                        "留空则使用默认情感。首次使用需要下载 CosyVoice 模型（约 1.2GB）。")
        cv_tip.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        cv_tip.setWordWrap(True)
        cv_lay.addWidget(cv_tip)

        lay.addWidget(self.tts_cosyvoice_panel)

        # 试听按钮
        test_row = QHBoxLayout()
        test_row.addStretch(1)
        self.btn_tts_test = QPushButton("🔊 试听")
        self.btn_tts_test.setObjectName("primary")
        self.btn_tts_test.clicked.connect(self._test_tts)
        test_row.addWidget(self.btn_tts_test)
        lay.addLayout(test_row)

        lay.addStretch(1)

        # 初始化显示
        self._on_tts_engine_changed(self.cmb_tts_engine.currentIndex())
        # 扫描默认目录
        self._scan_tts_models_silent()
        return w

    def _tab_voiceprint(self) -> QWidget:
        """声纹识别配置页。"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # 声纹验证开关
        self.chk_vp_enable = QCheckBox("启声声纹验证（仅允许注册用户的语音通过）")
        self.chk_vp_enable.setChecked(self._working.get("voiceprint_enabled", False))
        self.chk_vp_enable.setToolTip("开启后，语音识别前会验证声纹，防止 TTS 或其他人的声音被识别")
        lay.addWidget(self.chk_vp_enable)

        # 声纹阈值
        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("验证阈值："))
        self.sl_vp_threshold = QSlider(Qt.Orientation.Horizontal)
        self.sl_vp_threshold.setRange(25, 60)  # 0.25 - 0.60
        self.sl_vp_threshold.setValue(int(self._working.get("voiceprint_threshold", 0.4) * 100))
        self.sl_vp_threshold.setToolTip(
            "3D-Speaker CAM++ 模型推荐范围 0.35-0.50\n"
            "越低越宽松，越高越严格\n"
            "阈值太低可能误识别，太高可能漏识别"
        )
        self.lbl_vp_threshold = QLabel(f"{self.sl_vp_threshold.value() / 100:.2f}")
        self.sl_vp_threshold.valueChanged.connect(lambda v: self.lbl_vp_threshold.setText(f"{v / 100:.2f}"))
        threshold_row.addWidget(self.sl_vp_threshold, 1)
        threshold_row.addWidget(self.lbl_vp_threshold)
        lay.addLayout(threshold_row)

        # 声纹录入区域
        enroll_box = QFrame()
        enroll_box.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        enroll_lay = QVBoxLayout(enroll_box)
        enroll_lay.setContentsMargins(12, 10, 12, 10)
        enroll_lay.setSpacing(8)

        title = QLabel("🎙️ 声纹录入")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        enroll_lay.addWidget(title)

        # 录入说明
        tip = QLabel("点击「开始录入」后，请对着麦克风朗读下方文字（约 6 秒）。建议录入 3-5 次以提高识别准确率。\n"
                     "录入的声纹将用于语音识别验证，只有匹配的声音才会被识别。")
        tip.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        tip.setWordWrap(True)
        enroll_lay.addWidget(tip)

        # 录音模板区域
        template_box = QFrame()
        template_box.setStyleSheet(
            "QFrame { background: rgba(79, 140, 255, 15); border: 1px solid rgba(79, 140, 255, 50); border-radius: 6px; }"
        )
        template_lay = QVBoxLayout(template_box)
        template_lay.setContentsMargins(12, 8, 12, 8)
        template_lay.setSpacing(6)

        # 模板标题和切换按钮
        template_header = QHBoxLayout()
        template_title = QLabel("📝 请朗读以下内容")
        template_title.setStyleSheet("color: #4f8cff; font-weight: bold; font-size: 12px;")
        template_header.addWidget(template_title)
        template_header.addStretch()

        self.btn_next_template = QPushButton("🔄 换一个")
        self.btn_next_template.setObjectName("ghost")
        self.btn_next_template.setFixedHeight(24)
        self.btn_next_template.setStyleSheet("font-size: 11px;")
        self.btn_next_template.clicked.connect(self._switch_template)
        template_header.addWidget(self.btn_next_template)
        template_lay.addLayout(template_header)

        # 模板文本显示
        self.lbl_template_text = QLabel()
        self.lbl_template_text.setStyleSheet(
            "color: #e0e8ff; font-size: 15px; padding: 8px; "
            "font-family: 'Microsoft YaHei', 'SimHei', sans-serif; line-height: 1.5;"
        )
        self.lbl_template_text.setWordWrap(True)
        self.lbl_template_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._current_template_index = 0
        self._update_template_text()
        template_lay.addWidget(self.lbl_template_text)

        enroll_lay.addWidget(template_box)

        # 录入按钮行
        btn_row = QHBoxLayout()
        self.btn_vp_enroll = QPushButton("🎤 开始录入")
        self.btn_vp_enroll.setObjectName("primary")
        self.btn_vp_enroll.clicked.connect(self._start_voiceprint_enroll)
        btn_row.addWidget(self.btn_vp_enroll)

        self.btn_vp_stop_enroll = QPushButton("⏹ 停止录入")
        self.btn_vp_stop_enroll.setObjectName("ghost")
        self.btn_vp_stop_enroll.setEnabled(False)
        self.btn_vp_stop_enroll.clicked.connect(self._stop_voiceprint_enroll)
        btn_row.addWidget(self.btn_vp_stop_enroll)
        enroll_lay.addLayout(btn_row)

        # 录入进度条
        self.pbar_vp_enroll = QProgressBar()
        self.pbar_vp_enroll.setVisible(False)
        self.pbar_vp_enroll.setRange(0, 100)
        self.pbar_vp_enroll.setFixedHeight(6)
        self.pbar_vp_enroll.setStyleSheet(
            "QProgressBar { background: rgba(255,255,255,0.06); border: none; border-radius: 3px; }"
            "QProgressBar::chunk { background: #4f8cff; border-radius: 3px; }"
        )
        enroll_lay.addWidget(self.pbar_vp_enroll)

        # 录入状态
        self.lbl_vp_status = QLabel("")
        self.lbl_vp_status.setStyleSheet("color: #7ee7a8; font-size: 12px;")
        enroll_lay.addWidget(self.lbl_vp_status)

        lay.addWidget(enroll_box)

        # 已注册声纹列表
        list_box = QFrame()
        list_box.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        list_lay = QVBoxLayout(list_box)
        list_lay.setContentsMargins(12, 10, 12, 10)
        list_lay.setSpacing(6)

        list_title = QLabel("👥 已注册声纹")
        list_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        list_lay.addWidget(list_title)

        self.list_voiceprints = QListWidget()
        self.list_voiceprints.setStyleSheet(
            "QListWidget { background: rgba(255,255,255,5); border: 1px solid rgba(255,255,255,10); border-radius: 6px; }"
            "QListWidget::item { padding: 6px; }"
        )
        list_lay.addWidget(self.list_voiceprints)

        # 删除按钮
        del_row = QHBoxLayout()
        btn_refresh_vp = QPushButton("🔄 刷新")
        btn_refresh_vp.setObjectName("ghost")
        btn_refresh_vp.clicked.connect(self._refresh_voiceprint_list)
        del_row.addWidget(btn_refresh_vp)

        btn_del_vp = QPushButton("🗑 删除选中")
        btn_del_vp.setObjectName("ghost")
        btn_del_vp.clicked.connect(self._delete_selected_voiceprint)
        del_row.addWidget(btn_del_vp)

        btn_del_all_vp = QPushButton("⚠️ 全部删除")
        btn_del_all_vp.setObjectName("danger")
        btn_del_all_vp.clicked.connect(self._delete_all_voiceprints)
        del_row.addWidget(btn_del_all_vp)
        list_lay.addLayout(del_row)

        lay.addWidget(list_box)

        # 初始化列表
        self._refresh_voiceprint_list()

        return w

    # ---------- 录音模板 ----------

    def _update_template_text(self):
        """更新模板文本显示。"""
        template = ENROLLMENT_TEMPLATES[self._current_template_index]
        self.lbl_template_text.setText(template)

    def _switch_template(self):
        """切换到下一个模板。"""
        self._current_template_index = (self._current_template_index + 1) % len(ENROLLMENT_TEMPLATES)
        self._update_template_text()

    def _get_current_template(self) -> str:
        """获取当前模板文本。"""
        return ENROLLMENT_TEMPLATES[self._current_template_index]

    # ---------- 声纹操作 ----------

    def _refresh_voiceprint_list(self):
        """刷新声纹列表。"""
        self.list_voiceprints.clear()
        if self.voiceprint_mgr:
            vps = self.voiceprint_mgr.list_voiceprints()
            for vp in vps:
                item = QListWidgetItem(f"👤 {vp['name']}  ({vp['template_count']} 个模板)")
                self.list_voiceprints.addItem(item)
        else:
            item = QListWidgetItem("声纹管理器未初始化")
            self.list_voiceprints.addItem(item)

    def _start_voiceprint_enroll(self):
        """开始声纹录入。"""
        if not self.voiceprint_mgr:
            QMessageBox.warning(self, "提示", "声纹管理器未初始化")
            return

        if self._enrolling:
            return

        self._enrolling = True
        self._enroll_start_time = time.time()
        self.btn_vp_enroll.setEnabled(False)
        self.btn_vp_stop_enroll.setEnabled(True)
        self.pbar_vp_enroll.setVisible(True)
        self.pbar_vp_enroll.setValue(0)
        template_text = self._get_current_template()
        self.lbl_vp_status.setText(f"🎙️ 请朗读: {template_text}")
        self.lbl_vp_status.setStyleSheet("color: #ffb86c; font-size: 12px;")

        # 定时更新进度
        self._enroll_timer = QTimer()
        self._enroll_timer.timeout.connect(self._update_enroll_progress)
        self._enroll_timer.start(100)

        user_name = self._working.get("user_name", "用户")

        def on_progress(percent, remaining):
            self.pbar_vp_enroll.setValue(percent)

        def on_complete(success, message):
            self._enrolling = False
            self.btn_vp_enroll.setEnabled(True)
            self.btn_vp_stop_enroll.setEnabled(False)
            self.pbar_vp_enroll.setVisible(False)
            self.lbl_vp_status.setText(message)
            if success:
                self.lbl_vp_status.setStyleSheet("color: #7ee7a8; font-size: 12px;")
                QTimer.singleShot(2000, self._refresh_voiceprint_list)
            else:
                self.lbl_vp_status.setStyleSheet("color: #ff99a8; font-size: 12px;")

        def on_error(error_msg):
            self._enrolling = False
            self.btn_vp_enroll.setEnabled(True)
            self.btn_vp_stop_enroll.setEnabled(False)
            self.pbar_vp_enroll.setVisible(False)
            self.lbl_vp_status.setText(f"错误: {error_msg}")
            self.lbl_vp_status.setStyleSheet("color: #ff99a8; font-size: 12px;")

        self.voiceprint_mgr.enroll_voiceprint(
            user_name, duration=RECORD_DURATION,
            on_progress=on_progress,
            on_complete=on_complete,
            on_error=on_error,
        )

    def _update_enroll_progress(self):
        """更新录入进度。"""
        if not self._enrolling:
            self._enroll_timer.stop()
            return
        elapsed = time.time() - self._enroll_start_time
        remaining = max(0, RECORD_DURATION - elapsed)
        percent = min(100, int(elapsed / RECORD_DURATION * 100))
        self.pbar_vp_enroll.setValue(percent)
        self.lbl_vp_status.setText(f"请对着麦克风说话... 剩余 {remaining:.1f}s")

        if elapsed >= RECORD_DURATION + 0.5:  # 超时保护（加0.5秒缓冲）
            self._stop_voiceprint_enroll()

    def _stop_voiceprint_enroll(self):
        """停止声纹录入。"""
        self._enroll_timer.stop()
        if self.voiceprint_mgr:
            self.voiceprint_mgr.stop_recording()
        self._enrolling = False
        self.btn_vp_enroll.setEnabled(True)
        self.btn_vp_stop_enroll.setEnabled(False)

    def _delete_selected_voiceprint(self):
        """删除选中的声纹。"""
        if not self.voiceprint_mgr:
            return
        current = self.list_voiceprints.currentItem()
        if not current:
            QMessageBox.information(self, "提示", "请先选择一个声纹")
            return
        text = current.text()
        # 提取名称
        name = text.split("  (")[0].replace("👤 ", "")
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除「{name}」的声纹吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.voiceprint_mgr.delete_voiceprint(name)
            self._refresh_voiceprint_list()

    def _delete_all_voiceprints(self):
        """删除所有声纹。"""
        if not self.voiceprint_mgr:
            return
        reply = QMessageBox.warning(
            self, "危险操作",
            "确定删除所有声纹吗？删除后需要重新录入！",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.voiceprint_mgr.delete_all_voiceprints()
            self._refresh_voiceprint_list()

    def _tab_emotion(self) -> QWidget:
        """情绪引擎配置页。"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # 情绪引擎开关
        self.chk_emotion_enable = QCheckBox("启用品格与情绪引擎")
        self.chk_emotion_enable.setChecked(self._working.get("emotion_enabled", True))
        self.chk_emotion_enable.setToolTip(
            "开启后，AI 将根据用户输入分析情绪状态，"
            "并以此调整回复语气和用词，实现更有温度的交互。"
        )
        lay.addWidget(self.chk_emotion_enable)

        # 情绪引擎说明
        info_box = QFrame()
        info_box.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        info_lay = QVBoxLayout(info_box)
        info_lay.setContentsMargins(12, 10, 12, 10)
        info_lay.setSpacing(6)

        title = QLabel("💫 关于情绪引擎")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        info_lay.addWidget(title)

        desc = QLabel(
            "情绪引擎基于心理学理论构建了 10 个独立情绪通道，包括喜悦、悲伤、愤怒、恐惧、"
            "好感、信任、思念、愧疚等。\n\n"
            "核心特性：\n"
            "• 情绪不互相抵消，而是互相调制（如委屈：悲伤放大愤怒）\n"
            "• 弹性衰减：情绪偏离基线越远，回弹越快\n"
            "• 信任/好感作为慢通道，由长期交互累积形成\n"
            "• 人格基线：基于大五人格模型设定情绪基调\n\n"
            "开启后，AI 会在每次对话中分析用户情绪，并据此调整回复风格。"
        )
        desc.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        desc.setWordWrap(True)
        info_lay.addWidget(desc)

        lay.addWidget(info_box)

        # 人格设定
        personality_box = QFrame()
        personality_box.setStyleSheet("QFrame { background: rgba(255,255,255,5); border-radius: 8px; }")
        pers_lay = QVBoxLayout(personality_box)
        pers_lay.setContentsMargins(12, 10, 12, 10)
        pers_lay.setSpacing(8)

        pers_title = QLabel("🎭 人格设定（大五人格 OCEAN）")
        pers_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        pers_lay.addWidget(pers_title)

        pers_desc = QLabel("调节以下参数可以改变 AI 的性格基调：")
        pers_desc.setStyleSheet("color: #9fb5ff; font-size: 12px;")
        pers_lay.addWidget(pers_desc)

        # 辅助函数：将配置值转换为滑块值（0.0-1.0 → 0-100）
        def to_slider_val(val, default=0.5):
            v = self._working.get(val, default)
            # 如果是 0-1 范围的浮点数，乘以 100；否则直接使用（兼容旧格式）
            return int(v * 100) if v <= 1.0 else int(v)

        # 开放性
        row_open = QHBoxLayout()
        row_open.addWidget(QLabel("开放性："))
        self.sl_openness = QSlider(Qt.Orientation.Horizontal)
        self.sl_openness.setRange(0, 100)
        self.sl_openness.setValue(to_slider_val("personality_openness", 0.5))
        self.lbl_openness = QLabel(f"{self.sl_openness.value() / 100:.2f}")
        self.sl_openness.valueChanged.connect(lambda v: self.lbl_openness.setText(f"{v / 100:.2f}"))
        row_open.addWidget(self.sl_openness, 1)
        row_open.addWidget(self.lbl_openness)
        pers_lay.addLayout(row_open)

        # 尽责性
        row_cons = QHBoxLayout()
        row_cons.addWidget(QLabel("尽责性："))
        self.sl_conscientiousness = QSlider(Qt.Orientation.Horizontal)
        self.sl_conscientiousness.setRange(0, 100)
        self.sl_conscientiousness.setValue(to_slider_val("personality_conscientiousness", 0.5))
        self.lbl_conscientiousness = QLabel(f"{self.sl_conscientiousness.value() / 100:.2f}")
        self.sl_conscientiousness.valueChanged.connect(lambda v: self.lbl_conscientiousness.setText(f"{v / 100:.2f}"))
        row_cons.addWidget(self.sl_conscientiousness, 1)
        row_cons.addWidget(self.lbl_conscientiousness)
        pers_lay.addLayout(row_cons)

        # 外向性
        row_ext = QHBoxLayout()
        row_ext.addWidget(QLabel("外向性："))
        self.sl_extraversion = QSlider(Qt.Orientation.Horizontal)
        self.sl_extraversion.setRange(0, 100)
        self.sl_extraversion.setValue(to_slider_val("personality_extraversion", 0.5))
        self.lbl_extraversion = QLabel(f"{self.sl_extraversion.value() / 100:.2f}")
        self.sl_extraversion.valueChanged.connect(lambda v: self.lbl_extraversion.setText(f"{v / 100:.2f}"))
        row_ext.addWidget(self.sl_extraversion, 1)
        row_ext.addWidget(self.lbl_extraversion)
        pers_lay.addLayout(row_ext)

        # 宜人性
        row_agr = QHBoxLayout()
        row_agr.addWidget(QLabel("宜人性："))
        self.sl_agreeableness = QSlider(Qt.Orientation.Horizontal)
        self.sl_agreeableness.setRange(0, 100)
        self.sl_agreeableness.setValue(to_slider_val("personality_agreeableness", 0.5))
        self.lbl_agreeableness = QLabel(f"{self.sl_agreeableness.value() / 100:.2f}")
        self.sl_agreeableness.valueChanged.connect(lambda v: self.lbl_agreeableness.setText(f"{v / 100:.2f}"))
        row_agr.addWidget(self.sl_agreeableness, 1)
        row_agr.addWidget(self.lbl_agreeableness)
        pers_lay.addLayout(row_agr)

        # 神经质
        row_neu = QHBoxLayout()
        row_neu.addWidget(QLabel("神经质："))
        self.sl_neuroticism = QSlider(Qt.Orientation.Horizontal)
        self.sl_neuroticism.setRange(0, 100)
        self.sl_neuroticism.setValue(to_slider_val("personality_neuroticism", 0.5))
        self.lbl_neuroticism = QLabel(f"{self.sl_neuroticism.value() / 100:.2f}")
        self.sl_neuroticism.valueChanged.connect(lambda v: self.lbl_neuroticism.setText(f"{v / 100:.2f}"))
        row_neu.addWidget(self.sl_neuroticism, 1)
        row_neu.addWidget(self.lbl_neuroticism)
        pers_lay.addLayout(row_neu)

        # 人格快捷按钮
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("预设："))
        btn_empathic = QPushButton("🤝 共情型")
        btn_empathic.setObjectName("ghost")
        btn_empathic.clicked.connect(lambda: self._set_personality_preset(0.6, 0.5, 0.4, 0.8, 0.3))
        preset_row.addWidget(btn_empathic)

        btn_analytical = QPushButton("🧠 理性型")
        btn_analytical.setObjectName("ghost")
        btn_analytical.clicked.connect(lambda: self._set_personality_preset(0.5, 0.8, 0.3, 0.5, 0.2))
        preset_row.addWidget(btn_analytical)

        btn_cheerful = QPushButton("😊 开朗型")
        btn_cheerful.setObjectName("ghost")
        btn_cheerful.clicked.connect(lambda: self._set_personality_preset(0.6, 0.5, 0.8, 0.5, 0.3))
        preset_row.addWidget(btn_cheerful)

        btn_calm = QPushButton("😌 沉稳型")
        btn_calm.setObjectName("ghost")
        btn_calm.clicked.connect(lambda: self._set_personality_preset(0.4, 0.7, 0.3, 0.6, 0.2))
        preset_row.addWidget(btn_calm)

        pers_lay.addLayout(preset_row)

        lay.addWidget(personality_box)

        lay.addStretch(1)
        return w

    def _set_personality_preset(self, openness, conscientiousness, extraversion, agreeableness, neuroticism):
        """设置人格预设。"""
        self.sl_openness.setValue(int(openness * 100))
        self.sl_conscientiousness.setValue(int(conscientiousness * 100))
        self.sl_extraversion.setValue(int(extraversion * 100))
        self.sl_agreeableness.setValue(int(agreeableness * 100))
        self.sl_neuroticism.setValue(int(neuroticism * 100))

    def _on_tts_engine_changed(self, idx: int):
        engine = self.cmb_tts_engine.currentData()
        self.tts_sherpa_panel.setVisible(engine == "sherpa")
        self.tts_cosyvoice_panel.setVisible(engine == "cosyvoice")

    def _browse_tts_model(self):
        d = QFileDialog.getExistingDirectory(self, "选择 Sherpa TTS 模型目录")
        if d:
            self.ed_tts_model.setText(d)

    def _browse_cosyvoice_model(self):
        d = QFileDialog.getExistingDirectory(self, "选择 CosyVoice 模型目录")
        if d:
            self.ed_cosyvoice_model.setText(d)

    def _scan_tts_models_silent(self):
        """静默扫描默认目录。"""
        paths = []
        # 常见位置
        home = os.path.expanduser("~")
        candidates = [
            os.path.join("data", "tts_models"),
            os.path.join(home, "sherpa_models"),
            os.path.join(home, "tts_models"),
            "D:\\sherpa_models",
            "D:\\tts_models",
        ]
        for c in candidates:
            if os.path.isdir(c):
                paths.extend(find_sherpa_models(c))
        if paths:
            self.lbl_tts_models.setText("🔍 已发现模型：\n" + "\n".join(paths))

    def _scan_tts_models(self):
        """扫描模型目录。"""
        d = QFileDialog.getExistingDirectory(self, "选择搜索根目录")
        if not d:
            return
        paths = find_sherpa_models(d)
        if paths:
            self.lbl_tts_models.setText("🔍 已发现模型：\n" + "\n".join(paths))
            if len(paths) == 1 and not self.ed_tts_model.text():
                self.ed_tts_model.setText(paths[0])
            QMessageBox.information(self, "扫描完成", f"发现 {len(paths)} 个模型目录。")
        else:
            self.lbl_tts_models.setText("未发现模型。")
            QMessageBox.information(self, "扫描完成", "未在所选目录发现 Sherpa 模型。")

    def _check_deps_background(self):
        """在后台线程中检查依赖，避免阻塞主线程。"""
        import threading
        def _worker():
            try:
                result = check_dependencies()
                SettingsDialog._cached_deps = result
                self._deps_checked.emit(result)
            except Exception:
                self._deps_checked.emit({
                    "sherpa_onnx": False, "pyttsx3": False, "sounddevice": False,
                    "numpy": False, "cosyvoice": False, "torchaudio": False,
                    "sherpa_ready": False, "cosyvoice_ready": False,
                })
        threading.Thread(target=_worker, daemon=True).start()

    def _on_deps_checked(self, deps: dict):
        """依赖检查完成后更新UI。"""
        if not hasattr(self, '_deps_widgets') or not self._deps_widgets:
            return

        # 隐藏加载中提示
        if hasattr(self, 'lbl_deps_loading'):
            self.lbl_deps_loading.setVisible(False)

        # 更新依赖项显示
        dep_map = {
            "cosyvoice": deps.get("cosyvoice", False),
            "torchaudio": deps.get("torchaudio", False),
            "sherpa-onnx": deps.get("sherpa_onnx", False),
            "sounddevice": deps.get("sounddevice", False),
            "numpy": deps.get("numpy", False),
            "pyttsx3": deps.get("pyttsx3", False),
        }
        for key, ok in dep_map.items():
            if key in self._deps_widgets:
                icon_lbl, name_lbl = self._deps_widgets[key]
                icon_lbl.setText("✅" if ok else "❌")

        # 显示依赖项
        if hasattr(self, 'deps_container'):
            self.deps_container.setVisible(True)

        # 如果没有 TTS 引擎就绪，显示安装提示
        if hasattr(self, 'deps_tip_widget'):
            if not deps.get("cosyvoice_ready", False) and not deps.get("sherpa_ready", False):
                self.deps_tip_widget.setVisible(True)

    def _install_tts_deps(self):
        """一键安装 TTS 依赖。"""
        import subprocess
        r = QMessageBox.question(
            self, "确认安装",
            "将执行：pip install modelscope transformers torchaudio sounddevice numpy pyttsx3\n\n"
            "（包含 CosyVoice 情感TTS + Sherpa-ONNX + 系统TTS 兜底）\n继续？"
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            # 清除缓存，下次打开设置时重新检查
            SettingsDialog._cached_deps = None
            subprocess.Popen(
                ["pip", "install", "modelscope", "transformers", "torchaudio",
                 "sounddevice", "numpy", "pyttsx3"],
                shell=True,
            )
            QMessageBox.information(self, "安装已启动",
                                    "请在终端查看安装进度。安装完成后需重启应用。\n\n"
                                    "注意：CosyVoice 还需要模型文件，可在设置中指定模型目录。")
        except Exception as e:
            QMessageBox.warning(self, "安装失败", str(e))

    def _test_tts(self):
        """试听 TTS（复用同一个 TTSManager，避免重复创建导致音频流冲突）。"""
        try:
            from modules.tts_module import TTSManager
            engine = self.cmb_tts_engine.currentData()
            vol = self.sl_tts_volume.value() / 10.0
            if engine == "off":
                QMessageBox.information(self, "提示", "当前选择了关闭朗读。")
                return

            if engine == "cosyvoice":
                model_dir = self.ed_cosyvoice_model.text().strip()
                speaker = self.cmb_cosyvoice_speaker.currentText()
                emotion = self.ed_cosyvoice_emotion.text().strip()
                if not hasattr(self, "_test_mgr") or self._test_mgr is None:
                    self._test_mgr = TTSManager(
                        engine=engine, model_dir=model_dir,
                        volume=vol,
                        cosyvoice_speaker=speaker,
                        cosyvoice_emotion=emotion,
                    )
                else:
                    self._test_mgr.set_engine(engine)
                    self._test_mgr.set_model_dir(model_dir)
                    self._test_mgr.set_volume(vol)
                    self._test_mgr.set_cosyvoice_speaker(speaker)
                    self._test_mgr.set_cosyvoice_emotion(emotion)
            else:
                model_dir = self.ed_tts_model.text().strip()
                sid = self.sp_tts_sid.value()
                if not hasattr(self, "_test_mgr") or self._test_mgr is None:
                    self._test_mgr = TTSManager(engine=engine, model_dir=model_dir,
                                                speaker_id=sid, volume=vol)
                else:
                    self._test_mgr.set_engine(engine)
                    self._test_mgr.set_model_dir(model_dir)
                    self._test_mgr.set_speaker_id(sid)
                    self._test_mgr.set_volume(vol)
                if engine == "sherpa" and self._test_mgr._ensure_engine():
                    sherpa = self._test_mgr._sherpa
                    if sherpa:
                        n = sherpa.num_speakers
                        self.sp_tts_sid.setRange(0, max(0, n - 1))
                        self.lbl_tts_sid_info.setText(f"（共 {n} 个说话人）")
                        if sid >= n:
                            self.sp_tts_sid.setValue(0)
                            self._test_mgr.set_speaker_id(0)
            self._test_mgr.speak_immediately("你好，这是语音朗读测试。")
        except Exception as e:
            QMessageBox.warning(self, "试听失败", str(e))

    def closeEvent(self, event):
        """关闭时清理试听 TTS 资源，避免音频流残留。"""
        if hasattr(self, "_test_mgr") and self._test_mgr is not None:
            try:
                self._test_mgr.shutdown()
            except Exception:
                pass
            self._test_mgr = None
        super().closeEvent(event)

    def _reset_defaults(self):
        r = QMessageBox.question(self, "确认", "恢复默认设置？当前未保存的修改会丢失。")
        if r != QMessageBox.StandardButton.Yes:
            return
        defaults = {
            "user_name": "用户",
            "ai_name": "小智",
            "ai_persona": "你是一个贴心的智能助手，乐于助人，说话亲切自然。你可以帮用户打开电脑软件、搜索网页信息、回答各种问题。回答时尽量简洁明了，不要啰嗦。",
            "llm_provider": "ollama",
            "llm_configs": {
                "lmstudio": {"base_url": "http://localhost:1234/v1", "api_key": "lmstudio", "model": "local-model"},
                "ollama":   {"base_url": "http://localhost:11434/v1", "api_key": "ollama",   "model": "qwen2.5:7b"},
                "custom":   {"base_url": "https://api.openai.com/v1", "api_key": "",         "model": "gpt-4o-mini"},
            },
            "tts_engine": "system",
            "tts_model_dir": "",
            "tts_auto_play": False,
            "tts_speaker_id": 0,
            "tts_volume": 2.5,
            "cosyvoice_model_dir": "",
            "cosyvoice_speaker": "中文女",
            "cosyvoice_emotion": "",
            "sherpa_emotion": "",
        }
        self._working = defaults
        # 重新填充
        self.ed_user.setText(defaults["user_name"])
        self.ed_ai.setText(defaults["ai_name"])
        self.ed_persona.setPlainText(defaults["ai_persona"])
        self.cmb_provider.setCurrentText(defaults["llm_provider"])
        for key, wmap in self.cfg_widgets.items():
            wmap["base_url"].setText(defaults["llm_configs"][key]["base_url"])
            wmap["api_key"].setText(defaults["llm_configs"][key]["api_key"])
            wmap["model"].setText(defaults["llm_configs"][key]["model"])
        # TTS
        self.cmb_tts_engine.setCurrentIndex(1)
        self.chk_tts_auto.setChecked(False)
        self.ed_tts_model.setText("")
        self.sp_tts_sid.setValue(0)
        self.sl_tts_volume.setValue(25)  # 2.5x default
        self.cmb_sherpa_emotion.setCurrentIndex(0)
        # CosyVoice
        self.ed_cosyvoice_model.setText("")
        self.cmb_cosyvoice_speaker.setCurrentIndex(0)
        self.ed_cosyvoice_emotion.setText("")
        self._on_tts_engine_changed(self.cmb_tts_engine.currentIndex())

    def _save(self):
        user = self.ed_user.text().strip() or "用户"
        ai = self.ed_ai.text().strip() or "小智"
        persona = self.ed_persona.toPlainText().strip() or "你是一个贴心的智能助手。"
        provider = self.cmb_provider.currentText()
        cfgs = {}
        for key, wmap in self.cfg_widgets.items():
            cfgs[key] = {
                "base_url": wmap["base_url"].text().strip(),
                "api_key": wmap["api_key"].text().strip(),
                "model": wmap["model"].text().strip(),
                "type": "openai",
            }
            if not cfgs[key]["base_url"] or not cfgs[key]["model"]:
                # 允许不填，但保存时给出警告
                pass
        if not cfgs[provider]["base_url"] or not cfgs[provider]["model"]:
            QMessageBox.warning(self, "配置不完整", f"当前选择的「{provider}」模型名称或BaseURL为空，请完善后再保存。")
            return

        # TTS 配置
        tts_engine = self.cmb_tts_engine.currentData() or "off"
        tts_model_dir = self.ed_tts_model.text().strip()
        tts_auto = self.chk_tts_auto.isChecked()
        tts_sid = self.sp_tts_sid.value()
        tts_volume = self.sl_tts_volume.value() / 10.0

        # CosyVoice 配置
        cosyvoice_model_dir = self.ed_cosyvoice_model.text().strip()
        cosyvoice_speaker = self.cmb_cosyvoice_speaker.currentText()
        cosyvoice_emotion = self.ed_cosyvoice_emotion.text().strip()

        # Sherpa 情感标签
        sherpa_emotion = self.cmb_sherpa_emotion.currentData() or ""

        # 声纹配置
        vp_enabled = self.chk_vp_enable.isChecked()
        vp_threshold = self.sl_vp_threshold.value() / 100.0

        # 情绪引擎配置
        emotion_enabled = self.chk_emotion_enable.isChecked()
        personality_openness = self.sl_openness.value() / 100.0
        personality_conscientiousness = self.sl_conscientiousness.value() / 100.0
        personality_extraversion = self.sl_extraversion.value() / 100.0
        personality_agreeableness = self.sl_agreeableness.value() / 100.0
        personality_neuroticism = self.sl_neuroticism.value() / 100.0

        self._working.update({
            "user_name": user,
            "ai_name": ai,
            "ai_persona": persona,
            "llm_provider": provider,
            "llm_configs": cfgs,
            "tts_engine": tts_engine,
            "tts_model_dir": tts_model_dir,
            "tts_auto_play": tts_auto,
            "tts_speaker_id": tts_sid,
            "tts_volume": tts_volume,
            "cosyvoice_model_dir": cosyvoice_model_dir,
            "cosyvoice_speaker": cosyvoice_speaker,
            "cosyvoice_emotion": cosyvoice_emotion,
            "sherpa_emotion": sherpa_emotion,
            "voiceprint_enabled": vp_enabled,
            "voiceprint_threshold": vp_threshold,
            "emotion_enabled": emotion_enabled,
            "personality_openness": personality_openness,
            "personality_conscientiousness": personality_conscientiousness,
            "personality_extraversion": personality_extraversion,
            "personality_agreeableness": personality_agreeableness,
            "personality_neuroticism": personality_neuroticism,
        })

        # 同步到语音管理器
        if self.voiceprint_mgr:
            self.voiceprint_mgr.set_verification_enabled(vp_enabled)

        self.llm.settings = self._working
        self.llm.save_settings()
        self.settings_changed.emit(self._working)
        self.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(14, 16, 38))
        grad.setColorAt(1.0, QColor(18, 22, 48))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()
