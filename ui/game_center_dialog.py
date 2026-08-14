"""
游戏中心对话框

功能：
- 展示可用游戏列表
- 点击进入具体游戏
- 目前支持：中国象棋
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter, QBrush, QLinearGradient
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFrame, QGridLayout, QSizePolicy
)

from ui.xiangqi_game_dialog import XiangqiGameDialog


class GameCard(QFrame):
    """游戏卡片组件"""

    clicked = pyqtSignal()

    def __init__(self, title: str, description: str, emoji: str,
                 gradient_colors: tuple, parent=None):
        super().__init__(parent)
        self.gradient_colors = gradient_colors
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.setStyleSheet("""
            QFrame {
                border-radius: 16px;
                border: none;
            }
            QFrame:hover {
                border: 2px solid rgba(255,215,0,0.6);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 游戏图标
        emoji_label = QLabel(emoji)
        emoji_label.setFont(QFont("Segoe UI Emoji", 48))
        emoji_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        emoji_label.setStyleSheet("background: transparent;")
        layout.addWidget(emoji_label, 1)

        # 游戏标题
        title_label = QLabel(title)
        title_label.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        title_label.setStyleSheet("color: white; background: transparent;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # 游戏描述
        desc_label = QLabel(description)
        desc_label.setWordWrap(True)
        desc_label.setFont(QFont("Microsoft YaHei", 10))
        desc_label.setStyleSheet("color: rgba(255,255,255,0.8); background: transparent;")
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_label)

    def paintEvent(self, event):
        """绘制渐变背景"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(self.gradient_colors[0]))
        gradient.setColorAt(1, QColor(self.gradient_colors[1]))

        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 16, 16)

    def mousePressEvent(self, event):
        """鼠标点击事件"""
        self.clicked.emit()
        super().mousePressEvent(event)


class GameCenterDialog(QDialog):
    """游戏中心对话框"""

    def __init__(self, llm, parent=None):
        super().__init__(parent)
        self.llm = llm
        self._init_ui()

    def _init_ui(self):
        """初始化 UI"""
        self.setWindowTitle("🎮 小游戏中心")
        self.setMinimumSize(650, 500)
        self.resize(700, 550)

        self.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #1a1a3e, stop:0.5 #2d1b4e, stop:1 #0f0f2f);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # 标题
        title_label = QLabel("🎮 小游戏中心")
        title_label.setFont(QFont("Microsoft YaHei", 24, QFont.Weight.Bold))
        title_label.setStyleSheet("color: #ffd700;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # 副标题
        subtitle_label = QLabel("选择一个游戏开始吧！AI 会作为你的对手和教练")
        subtitle_label.setFont(QFont("Microsoft YaHei", 12))
        subtitle_label.setStyleSheet("color: #b7c4ff;")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle_label)

        # 游戏卡片区域
        cards_frame = QFrame()
        cards_frame.setStyleSheet("""
            QFrame {
                background: rgba(255,255,255,0.05);
                border-radius: 16px;
                border: 1px solid rgba(255,255,255,0.1);
            }
        """)
        cards_layout = QGridLayout(cards_frame)
        cards_layout.setSpacing(24)
        cards_layout.setContentsMargins(30, 30, 30, 30)

        # 象棋卡片
        xiangqi_card = GameCard(
            "中国象棋",
            "与 AI 教练对战，赛后获得专业分析和改进建议",
            "♟️",
            ("#c0392b", "#8e2b2b")
        )
        xiangqi_card.clicked.connect(self._open_xiangqi)
        cards_layout.addWidget(xiangqi_card, 0, 0, Qt.AlignmentFlag.AlignCenter)

        # 占位卡片（后续可扩展更多游戏）
        coming_soon_card = GameCard(
            "更多游戏",
            "敬请期待！五子棋、围棋等即将上线",
            "🎲",
            ("#6c3483", "#4a235a")
        )
        coming_soon_card.setEnabled(False)
        coming_soon_card.setStyleSheet("""
            QFrame {
                border-radius: 16px;
                border: none;
                opacity: 0.6;
            }
        """)
        cards_layout.addWidget(coming_soon_card, 0, 1, Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(cards_frame, 1)

        # 底部提示
        tip_label = QLabel("💡 提示：游戏结束后点击「AI 分析」查看你的棋局评价和改进建议")
        tip_label.setFont(QFont("Microsoft YaHei", 11))
        tip_label.setStyleSheet("color: #7ee7a8;")
        tip_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tip_label)

        # 关闭按钮
        close_btn = QPushButton("返回")
        close_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 30px;
                border-radius: 10px;
                color: white;
                background: rgba(255,255,255,0.1);
                border: 1px solid rgba(255,255,255,0.2);
                font-size: 14px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.2); }
        """)
        close_btn.clicked.connect(self.accept)
        close_layout = QHBoxLayout()
        close_layout.addStretch()
        close_layout.addWidget(close_btn)
        close_layout.addStretch()
        layout.addLayout(close_layout)

    def _open_xiangqi(self):
        """打开象棋游戏"""
        self.accept()
        game = XiangqiGameDialog(self.llm)
        game.exec()
