import os
import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread
from PyQt6.QtGui import QImage, QPixmap, QFont, QColor, QPainter, QLinearGradient, QBrush, QPalette
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QLineEdit,
    QFrame, QMessageBox, QSizePolicy, QSpacerItem, QApplication,
)

from modules.face_recognition_module import FaceRecognizer, _sanitize_frame


class FaceRecognitionThread(QThread):
    finished = pyqtSignal(str, object)

    def __init__(self, recognizer: FaceRecognizer, camera_id: int = 0, timeout: int = 15):
        super().__init__()
        self.recognizer = recognizer
        self.camera_id = camera_id
        self.timeout = timeout

    def run(self):
        try:
            name, frame = self.recognizer.recognize_from_camera(self.camera_id, self.timeout)
            self.finished.emit(name, frame)
        except Exception as e:
            self.finished.emit(None, None)


class CameraPreviewThread(QThread):
    frame_ready = pyqtSignal(np.ndarray)

    def __init__(self, camera_id: int = 0):
        super().__init__()
        self.camera_id = camera_id
        self._stop = False

    def stop(self):
        self._stop = True

    def _try_open(self):
        candidates = [
            cv2.CAP_DSHOW,
            cv2.CAP_ANY,
        ]
        for backend in candidates:
            cap = cv2.VideoCapture(self.camera_id, backend)
            if not cap.isOpened():
                cap.release()
                continue
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.release()
                continue
            # 验证帧有效性
            if frame.ndim == 3 and frame.shape[2] == 3 and frame.std() > 5:
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                return cap
            cap.release()
        # 最后兜底：默认后端
        cap = cv2.VideoCapture(self.camera_id)
        if cap.isOpened():
            return cap
        return None

    def run(self):
        cap = self._try_open()
        if cap is None:
            return
        try:
            while not self._stop:
                ret, frame = cap.read()
                if not ret or frame is None:
                    self.msleep(30)
                    continue
                # 清洗帧
                clean = _sanitize_frame(frame)
                if clean is not None and clean.shape[0] > 0 and clean.shape[1] > 0:
                    self.frame_ready.emit(clean)
                self.msleep(25)
        finally:
            cap.release()


class LoginWindow(QWidget):
    """登录/人脸注册界面。"""

    login_success = pyqtSignal(str)  # 登录成功，返回用户名

    def __init__(self, recognizer: FaceRecognizer):
        super().__init__()
        self.recog = recognizer
        self.preview_thread = None
        self.recog_thread = None
        self._setup_ui()
        self._start_preview()

    def closeEvent(self, e):
        if self.preview_thread:
            self.preview_thread.stop()
            self.preview_thread.wait(1000)
        super().closeEvent(e)

    # ---------- UI ----------
    def _setup_ui(self):
        self.setWindowTitle("智能助手 · 登录")
        self.resize(900, 640)
        self.setMinimumSize(780, 560)
        self._apply_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 30, 40, 30)
        root.setSpacing(20)

        # 顶部标题
        title = QLabel("智能助手 · 人脸识别登录")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tf = QFont()
        tf.setPointSize(26)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #ffffff; background: transparent;")
        root.addWidget(title)

        # 中部：摄像头预览
        self.preview = QLabel("摄像头加载中...")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(640, 420)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview.setStyleSheet(
            "QLabel { background: rgba(255,255,255,10); border: 2px solid rgba(120,160,255,0.6);"
            " border-radius: 16px; color: rgba(255,255,255,180); font-size: 14px; }"
        )
        root.addWidget(self.preview, stretch=1)

        # 状态提示
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("color: #8fd6ff; font-size: 15px; background: transparent;")
        root.addWidget(self.status_label)

        # 底部操作区
        bot = QFrame()
        bot_layout = QHBoxLayout(bot)
        bot_layout.setContentsMargins(0, 0, 0, 0)
        bot_layout.setSpacing(12)

        if self.recog.has_registered_faces():
            self.btn_login = QPushButton("开始人脸识别登录")
            self.btn_login.clicked.connect(self._on_click_login)
            self.btn_login.setStyleSheet(self._btn_style("#4f8cff", "#3a6fd6"))
            bot_layout.addWidget(self.btn_login, stretch=3)
        else:
            self.btn_login = None
            self.status_label.setText("⚠️ 尚未注册任何人脸，请先注册后再使用")

        # 注册区
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("输入你的名字（用于新用户注册）")
        self.name_edit.setStyleSheet(
            "QLineEdit { padding: 10px 14px; border-radius: 10px; background: rgba(255,255,255,12);"
            " color: #ffffff; border: 1px solid rgba(255,255,255,30); font-size: 14px; }"
            "QLineEdit:focus { border: 1px solid #4f8cff; }"
        )
        bot_layout.addWidget(self.name_edit, stretch=3)

        self.btn_register = QPushButton("注册新人脸")
        self.btn_register.clicked.connect(self._on_click_register)
        self.btn_register.setStyleSheet(self._btn_style("#7c5cff", "#5e3fd6"))
        bot_layout.addWidget(self.btn_register, stretch=2)

        # 跳过登录
        self.btn_skip = QPushButton("跳过（访客模式）")
        self.btn_skip.clicked.connect(self._on_skip)
        self.btn_skip.setStyleSheet(
            "QPushButton { padding: 10px 18px; border-radius: 10px; font-size: 14px; color: #cfd8ff;"
            " background: rgba(255,255,255,8); border: 1px solid rgba(255,255,255,20); }"
            "QPushButton:hover { background: rgba(255,255,255,18); }"
        )
        bot_layout.addWidget(self.btn_skip, stretch=2)

        root.addWidget(bot)

    def _apply_style(self):
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(18, 22, 40))
        self.setPalette(pal)
        self.setStyleSheet("""
            QWidget { font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif; }
        """)

    def _btn_style(self, normal: str, hover: str) -> str:
        return (
            f"QPushButton {{ padding: 10px 18px; border-radius: 10px; font-size: 14px; color: white;"
            f" background: {normal}; border: none; font-weight: 600; }}"
            f"QPushButton:hover {{ background: {hover}; }}"
            f"QPushButton:disabled {{ background: rgba(255,255,255,30); color: rgba(255,255,255,120); }}"
        )

    # ---------- 摄像头预览 ----------
    def _start_preview(self):
        self.preview_thread = CameraPreviewThread(0)
        self.preview_thread.frame_ready.connect(self._on_preview_frame)
        self.preview_thread.start()

    def _on_preview_frame(self, frame: np.ndarray):
        if self.preview is None:
            return
        # frame 已经是 RGB 格式（由 _sanitize_frame 处理）
        h, w, ch = frame.shape
        img = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
        label_size = self.preview.size()
        pix = QPixmap.fromImage(img).scaled(
            label_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview.setPixmap(pix)

    # ---------- 登录 ----------
    def _on_click_login(self):
        if self.recog_thread is not None and self.recog_thread.isRunning():
            return
        if not self.recog.has_registered_faces():
            QMessageBox.information(self, "提示", "尚未注册任何人脸，请先注册。")
            return
        self.status_label.setText("🔍 正在识别，请将面部对准摄像头...")
        self._set_buttons_enabled(False)
        self.recog_thread = FaceRecognitionThread(self.recog, timeout=15)
        self.recog_thread.finished.connect(self._on_recog_finished)
        self.recog_thread.start()

    def _on_recog_finished(self, name: str, frame):
        self._set_buttons_enabled(True)
        if name:
            self.status_label.setText(f"✅ 识别成功：{name}")
            # 关闭预览
            if self.preview_thread:
                self.preview_thread.stop()
                self.preview_thread.wait(1000)
            QTimer.singleShot(500, lambda: self.login_success.emit(name))
        else:
            self.status_label.setText("❌ 未识别到已注册的人脸，请重试或注册")

    def _on_skip(self):
        if self.preview_thread:
            self.preview_thread.stop()
            self.preview_thread.wait(1000)
        self.login_success.emit("访客")

    # ---------- 注册 ----------
    def _on_click_register(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "请先输入注册的名字")
            return
        if self.recog_thread is not None and self.recog_thread.isRunning():
            return
        self.status_label.setText(f"📷 正在为「{name}」采集人脸，请将面部对准摄像头...")
        self._set_buttons_enabled(False)

        # 用FaceRecognizer.register_from_camera。单独线程避免阻塞。
        class RegThread(QThread):
            done = pyqtSignal(bool, str)
            def __init__(self, recog, n):
                super().__init__()
                self.recog = recog
                self.name = n
            def run(self):
                try:
                    ok, msg = self.recog.register_from_camera(self.name)
                    self.done.emit(ok, msg)
                except Exception as e:
                    self.done.emit(False, str(e))

        self._reg_thread = RegThread(self.recog, name)
        self._reg_thread.done.connect(self._on_reg_done)
        self._reg_thread.start()

    def _on_reg_done(self, ok: bool, msg: str):
        self._set_buttons_enabled(True)
        if ok:
            self.status_label.setText(f"✅ {msg}：{self.name_edit.text().strip()}")
            QMessageBox.information(self, "注册成功", f"人脸已成功注册：{self.name_edit.text().strip()}")
            # 刷新界面按钮（若无登录按钮则重建）
            if not self.recog.has_registered_faces():
                pass
            QApplication.processEvents()
            # 简单处理：重新加载窗口不现实，直接提示后保持
        else:
            self.status_label.setText(f"❌ 注册失败：{msg}")
            QMessageBox.warning(self, "注册失败", msg)

    def _set_buttons_enabled(self, enabled: bool):
        if self.btn_login:
            self.btn_login.setEnabled(enabled)
        self.btn_register.setEnabled(enabled)
        self.btn_skip.setEnabled(enabled)
        self.name_edit.setEnabled(enabled)

    # 绘制渐变背景
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(12, 16, 36))
        grad.setColorAt(0.5, QColor(22, 20, 50))
        grad.setColorAt(1.0, QColor(12, 22, 46))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()
