# -*- coding: utf-8 -*-
"""
智能助手 桌面应用 入口
功能：人脸识别登录 → 欢迎动画 → 主界面（LLM对话 + 打开/关闭软件 + 网页搜索）
"""
import sys
import os
import json
import ctypes
import logging

# 确保工作目录在项目根
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

# ========== 全局日志配置 ==========
# 日志同时输出到控制台和 data/smart_assistant.log
_LOG_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(_LOG_DIR, exist_ok=True)
_LOG_FILE = os.path.join(_LOG_DIR, "smart_assistant.log")

_log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=_log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),              # 控制台输出
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),  # 文件输出
    ],
)
logger = logging.getLogger("main")
logger.info("=" * 60)
logger.info("Smart Assistant 启动，日志文件: %s", _LOG_FILE)
logger.info("=" * 60)


def _preload_qt6_dlls():
    """
    预加载 Qt6 DLL，解决 conda 环境下 Library\\bin 中的旧版 DLL 冲突。
    用 LOAD_WITH_ALTERED_SEARCH_PATH 让 DLL 自身目录优先搜索。
    """
    try:
        pyqt6_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 '__pycache__', '..', 'Lib', 'site-packages', 'PyQt6')
        # 通过 Python 查找实际路径
        import importlib.util
        spec = importlib.util.find_spec("PyQt6")
        if spec is None:
            return
        pyqt6_dir = os.path.dirname(spec.origin)
        qt6_bin = os.path.join(pyqt6_dir, 'Qt6', 'bin')
        if not os.path.isdir(qt6_bin):
            return

        k32 = ctypes.windll.kernel32
        k32.LoadLibraryExW.restype = ctypes.c_void_p
        k32.LoadLibraryExW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]

        # 按依赖顺序预加载
        dlls = [
            'Qt6Core.dll', 'Qt6Gui.dll', 'Qt6Widgets.dll',
            'Qt6Network.dll', 'Qt6OpenGL.dll', 'Qt6OpenGLWidgets.dll',
            'Qt6Svg.dll', 'Qt6SvgWidgets.dll', 'Qt6PrintSupport.dll',
            'Qt6Multimedia.dll', 'Qt6MultimediaWidgets.dll',
        ]
        for dll in dlls:
            p = os.path.join(qt6_bin, dll)
            if os.path.exists(p):
                h = k32.LoadLibraryExW(p, None, 0x8)  # LOAD_WITH_ALTERED_SEARCH_PATH
    except Exception:
        pass  # 如果预加载失败，让 PyQt6 的 __init__.py 自行处理


_preload_qt6_dlls()


def main():
    from PyQt6.QtWidgets import QApplication, QMessageBox
    from PyQt6.QtGui import QFont

    from modules.face_recognition_module import FaceRecognizer
    from modules.llm_module import LLMClient
    from ui.login_window import LoginWindow
    from ui.welcome_window import WelcomeWindow
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("智能助手")
    app.setQuitOnLastWindowClosed(True)

    # 全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    # 初始化 LLM
    llm = LLMClient()

    # 初始化人脸识别
    recog = FaceRecognizer()

    # 如果 face_recognition 库不可用，给出提示
    try:
        import face_recognition  # noqa: F401
        fr_ok = True
    except Exception:
        fr_ok = False

    # ---------- 阶段1：登录 ----------
    login_result = {"name": None}

    def on_login_success(name: str):
        login_result["name"] = name
        login_win.close()

    login_win = LoginWindow(recog)
    login_win.login_success.connect(on_login_success)

    if not fr_ok:
        # 没有人脸识别库 → 直接访客模式
        btn = QMessageBox.warning(
            None, "人脸识别模块未就绪",
            "未检测到 face_recognition / dlib 库，人脸识别功能不可用。\n"
            "请先安装依赖：pip install face-recognition dlib opencv-python\n"
            "（dlib 在Windows上可能需要先安装CMake和Visual Studio C++构建工具）\n\n"
            "是否以访客模式继续使用其他功能？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if btn == QMessageBox.StandardButton.No:
            sys.exit(1)
        login_result["name"] = llm.settings.get("user_name", "访客")
    else:
        login_win.show()
        app.exec()

    user_name = login_result["name"] or llm.settings.get("user_name", "访客")
    if not login_result["name"] and not fr_ok:
        pass
    if not login_result["name"]:
        # 用户关闭登录窗口，退出
        sys.exit(0)

    # 把人脸识别到的名字写入settings的user_name（若原名为空或"访客"且识别到了真名）
    if user_name != "访客" and llm.settings.get("user_name", "用户") in ("用户", "", "访客"):
        llm.settings["user_name"] = user_name
        llm.save_settings()

    # ---------- 阶段2：欢迎动画 ----------
    welcome_shown = {"done": False}
    welcome = WelcomeWindow(user_name=user_name, llm=llm)

    def on_welcome_finished():
        welcome_shown["done"] = True
        welcome.close()
        # 退出整个应用循环继续
        app.quit()

    welcome.finished.connect(on_welcome_finished)
    welcome.start()
    app.exec()

    # ---------- 阶段3：主窗口 ----------
    win = MainWindow(user_name=user_name, llm=llm)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
