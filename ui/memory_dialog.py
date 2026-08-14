"""
记忆管理对话框 —— 查看和管理 MemSkill 长期记忆库。
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter, QLinearGradient, QBrush, QPalette
from PyQt6.QtWidgets import (
    QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFormLayout,
    QTextEdit, QFrame, QListWidget, QListWidgetItem, QSpinBox, QMessageBox,
    QSplitter, QWidget, QSizePolicy, QApplication,
)

from modules.memory_system import MemSkillManager, MemorySkill


class MemoryDialog(QDialog):
    """记忆查看与管理对话框。"""

    def __init__(self, memory: MemSkillManager, parent=None):
        super().__init__(parent)
        self.memory = memory
        self.setWindowTitle("🧠 长期记忆管理")
        self.resize(780, 600)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog, QWidget {
                font-family: 'Microsoft YaHei', 'PingFang SC', sans-serif;
                color: #e8ecff;
            }
            QLabel { background: transparent; font-size: 14px; }
            QTextEdit, QListWidget {
                background: rgba(255,255,255,10);
                border: 1px solid rgba(255,255,255,25);
                border-radius: 8px;
                padding: 8px;
                color: white;
                font-size: 14px;
                selection-background-color: #4f8cff;
            }
            QTextEdit:focus, QListWidget:focus { border: 1px solid #4f8cff; }
            QPushButton {
                padding: 8px 18px; border-radius: 8px; font-size: 14px;
                color: white; border: none; font-weight: 600;
            }
            QPushButton#primary { background: #4f8cff; }
            QPushButton#primary:hover { background: #3a6fd6; }
            QPushButton#ghost { background: rgba(255,255,255,10); border: 1px solid rgba(255,255,255,25); }
            QPushButton#ghost:hover { background: rgba(255,255,255,18); }
            QPushButton#danger { background: rgba(255,90,110,0.2); color: #ff99a8; border: 1px solid rgba(255,90,110,0.5); }
            QPushButton#danger:hover { background: rgba(255,90,110,0.35); }
        """)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(16, 20, 40))
        self.setPalette(pal)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        # 顶部统计
        self.lbl_stats = QLabel()
        self.lbl_stats.setStyleSheet("color: #9fb5ff; font-size: 13px;")
        root.addWidget(self.lbl_stats)

        # 主体：左列表 + 右详情
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧列表
        left = QFrame()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        ll.addWidget(QLabel("记忆列表（按重要性排序）"))
        self.list_memories = QListWidget()
        self.list_memories.currentItemChanged.connect(self._on_select_memory)
        ll.addWidget(self.list_memories, 1)

        # 右侧详情
        right = QFrame()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rl.addWidget(QLabel("记忆详情"))

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(280)
        rl.addWidget(self.detail, 1)

        # 重要性调整
        row_imp = QHBoxLayout()
        row_imp.addWidget(QLabel("重要性："))
        self.spn_importance = QSpinBox()
        self.spn_importance.setRange(1, 5)
        self.btn_set_imp = QPushButton("更新")
        self.btn_set_imp.setObjectName("ghost")
        self.btn_set_imp.clicked.connect(self._update_importance)
        row_imp.addWidget(self.spn_importance)
        row_imp.addWidget(self.btn_set_imp)
        row_imp.addStretch(1)
        rl.addLayout(row_imp)

        # 删除按钮
        self.btn_delete = QPushButton("🗑 删除此记忆")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self._delete_memory)
        rl.addWidget(self.btn_delete)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([340, 420])
        root.addWidget(splitter, 1)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.btn_maintain = QPushButton("🔧 立即维护（合并+衰减）")
        self.btn_maintain.setObjectName("ghost")
        self.btn_maintain.clicked.connect(self._run_maintenance)
        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setObjectName("ghost")
        self.btn_refresh.clicked.connect(self._refresh_list)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("primary")
        self.btn_close.clicked.connect(self.accept)
        bottom.addWidget(self.btn_maintain)
        bottom.addWidget(self.btn_refresh)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)

    def _refresh_list(self):
        """刷新记忆列表。"""
        self.list_memories.clear()
        self._memories = self.memory.list_all()
        for mem in self._memories:
            stars = "★" * mem.importance + "☆" * (5 - mem.importance)
            display = f"{stars}  {mem.name}\n     {mem.summary}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, mem.skill_id)
            self.list_memories.addItem(item)
        self.lbl_stats.setText(f"共 {len(self._memories)} 条活跃记忆")
        self.detail.clear()

    def _on_select_memory(self, current, previous):
        if not current:
            self.detail.clear()
            return
        skill_id = current.data(Qt.ItemDataRole.UserRole)
        for mem in self._memories:
            if mem.skill_id == skill_id:
                self._show_detail(mem)
                self.spn_importance.setValue(mem.importance)
                break

    def _show_detail(self, mem: MemorySkill):
        stars = "★" * mem.importance + "☆" * (5 - mem.importance)
        html = (
            f"<div style='line-height:1.6'>"
            f"<p style='font-size:16px;font-weight:bold;color:#4f8cff;'>{mem.name}</p>"
            f"<p><b>重要性：</b>{stars}</p>"
            f"<p><b>摘要：</b>{mem.summary}</p>"
            f"<p><b>关键词：</b>{', '.join(mem.keywords)}</p>"
            f"<p><b>触发场景：</b>{', '.join(mem.triggers)}</p>"
            f"<p><b>创建时间：</b>{mem.created_at}</p>"
            f"<p><b>最近访问：</b>{mem.last_accessed_at}（共 {mem.access_count} 次）</p>"
            f"<p><b>来源会话：</b>{', '.join(mem.source_session_ids) if mem.source_session_ids else '无'}</p>"
            f"<p><b>是否合并：</b>{'是 ← ' + ', '.join(mem.merged_from) if mem.is_merged else '否'}</p>"
            f"<hr style='border:1px solid rgba(255,255,255,0.15)'>"
            f"<p><b>原始记录：</b></p>"
            f"<pre style='white-space:pre-wrap;color:#c8d0f0;'>{mem.raw_content}</pre>"
            f"</div>"
        )
        self.detail.setHtml(html)

    def _update_importance(self):
        current = self.list_memories.currentItem()
        if not current:
            return
        skill_id = current.data(Qt.ItemDataRole.UserRole)
        val = self.spn_importance.value()
        self.memory.update_importance(skill_id, val)
        self._refresh_list()
        # 重新选中
        for i in range(self.list_memories.count()):
            item = self.list_memories.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == skill_id:
                self.list_memories.setCurrentItem(item)
                break

    def _delete_memory(self):
        current = self.list_memories.currentItem()
        if not current:
            return
        skill_id = current.data(Qt.ItemDataRole.UserRole)
        r = QMessageBox.question(self, "确认删除", "确定要删除这条记忆吗？此操作不可撤销。")
        if r != QMessageBox.StandardButton.Yes:
            return
        self.memory.delete_memory(skill_id)
        self._refresh_list()

    def _run_maintenance(self):
        """手动触发维护。"""
        self.btn_maintain.setEnabled(False)
        self.btn_maintain.setText("🔧 维护中...")
        QApplication.processEvents()
        try:
            stats = self.memory.schedule_maintenance()
            merged = stats.get("merged", 0)
            forgotten = stats.get("forgotten", 0)
            QMessageBox.information(
                self, "维护完成",
                f"记忆维护完成。\n合并了 {merged} 条相似记忆\n遗忘了 {forgotten} 条低价值记忆"
            )
        except Exception as e:
            QMessageBox.warning(self, "维护失败", f"维护出错：{e}")
        finally:
            self.btn_maintain.setEnabled(True)
            self.btn_maintain.setText("🔧 立即维护（合并+衰减）")
            self._refresh_list()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QColor(14, 16, 38))
        grad.setColorAt(1.0, QColor(18, 22, 48))
        p.fillRect(self.rect(), QBrush(grad))
        p.end()
