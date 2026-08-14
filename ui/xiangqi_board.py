"""
象棋棋盘 UI 控件

功能：
- 绘制 9x10 棋盘
- 显示棋子（带颜色和名称）
- 处理玩家点击选子和走子
- 高亮显示合法走法
- 动画效果
"""

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QPointF, QSize
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPolygonF,
    QPainterPath, QRadialGradient
)
from PyQt6.QtWidgets import QWidget, QSizePolicy
from typing import Optional, List, Tuple

from modules.game.xiangqi_engine import XiangqiEngine, Piece, Side, PieceType


class XiangqiBoard(QWidget):
    """象棋棋盘控件"""

    piece_moved = pyqtSignal(list, list)
    game_state_changed = pyqtSignal(dict)

    COLS = 9
    ROWS = 10
    MIN_CELL_SIZE = 48
    MIN_MARGIN = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self.engine: Optional[XiangqiEngine] = None
        self.selected_pos: Optional[Tuple[int, int]] = None
        self.valid_moves: List[Tuple[int, int]] = []
        self.last_move: Optional[Tuple[Tuple[int, int], Tuple[int, int]]] = None
        self._is_player_turn = True

        self.CELL_SIZE = 50
        self.MARGIN = 20
        self.MARGIN_Y = 20

        self._calc_size()

        self.setMinimumSize(self._min_size())
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background-color: #f5e6c8;")

    def _min_size(self) -> QSize:
        grid_w = (self.COLS - 1) * self.MIN_CELL_SIZE
        grid_h = (self.ROWS - 1) * self.MIN_CELL_SIZE
        w = 2 * self.MIN_MARGIN + grid_w
        h = 2 * self.MIN_MARGIN + grid_h
        return QSize(w, h)

    def _calc_size(self):
        """根据当前控件尺寸计算格子大小和边距，确保绘制和点击使用同一坐标系"""
        w = max(self.width(), self._min_size().width())
        h = max(self.height(), self._min_size().height())

        avail_w = w - 2 * self.MIN_MARGIN
        avail_h = h - 2 * self.MIN_MARGIN

        cell_w = avail_w / (self.COLS - 1)
        cell_h = avail_h / (self.ROWS - 1)
        self.CELL_SIZE = int(max(self.MIN_CELL_SIZE, min(cell_w, cell_h)))

        grid_w = (self.COLS - 1) * self.CELL_SIZE
        grid_h = (self.ROWS - 1) * self.CELL_SIZE
        self.MARGIN = max(self.MIN_MARGIN, int((w - grid_w) / 2))
        self.MARGIN_Y = max(self.MIN_MARGIN, int((h - grid_h) / 2))

    def sizeHint(self) -> QSize:
        return self._min_size()

    def minimumSizeHint(self) -> QSize:
        return self._min_size()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._calc_size()
        self.update()

    def set_engine(self, engine: XiangqiEngine):
        self.engine = engine
        self.selected_pos = None
        self.valid_moves = []
        self.last_move = None
        self.update()

    def set_player_turn(self, is_player: bool):
        self._is_player_turn = is_player
        if not is_player:
            self.selected_pos = None
            self.valid_moves = []
        self.update()

    def paintEvent(self, event):
        self._calc_size()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_board(painter)
        self._draw_pieces(painter)
        self._draw_highlights(painter)

    def _draw_board(self, painter: QPainter):
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE

        painter.fillRect(self.rect(), QColor(245, 230, 200))

        pen = QPen(QColor(80, 50, 20), max(1, int(cs * 0.03)))
        painter.setPen(pen)

        for row in range(self.ROWS):
            y = margin_y + row * cs
            painter.drawLine(
                margin_x, y,
                margin_x + (self.COLS - 1) * cs, y
            )

        for col in range(self.COLS):
            x = margin_x + col * cs
            if col == 0 or col == self.COLS - 1:
                painter.drawLine(x, margin_y, x,
                                 margin_y + (self.ROWS - 1) * cs)
            elif col == 4:
                painter.drawLine(x, margin_y, x,
                                 margin_y + 4 * cs)
                painter.drawLine(x, margin_y + 5 * cs,
                                 x, margin_y + 9 * cs)
            else:
                painter.drawLine(x, margin_y, x,
                                 margin_y + 4 * cs)
                painter.drawLine(x, margin_y + 5 * cs,
                                 x, margin_y + 9 * cs)

        painter.drawLine(
            margin_x + 3 * cs, margin_y,
            margin_x + 5 * cs, margin_y + 2 * cs
        )
        painter.drawLine(
            margin_x + 5 * cs, margin_y,
            margin_x + 3 * cs, margin_y + 2 * cs
        )
        painter.drawLine(
            margin_x + 3 * cs,
            margin_y + 7 * cs,
            margin_x + 5 * cs,
            margin_y + 9 * cs
        )
        painter.drawLine(
            margin_x + 5 * cs,
            margin_y + 7 * cs,
            margin_x + 3 * cs,
            margin_y + 9 * cs
        )

        river_font_size = max(10, int(cs * 0.35))
        font = QFont("Microsoft YaHei", river_font_size, QFont.Weight.Bold)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(font)
        painter.setPen(QColor(139, 90, 43))
        river_y = margin_y + 4.5 * cs
        text_h = max(16, int(cs * 0.5))
        painter.drawText(
            QRectF(margin_x + 0.5 * cs, river_y - text_h / 2,
                   3 * cs, text_h),
            Qt.AlignmentFlag.AlignCenter, "楚 河"
        )
        painter.drawText(
            QRectF(margin_x + 5.5 * cs, river_y - text_h / 2,
                   3 * cs, text_h),
            Qt.AlignmentFlag.AlignCenter, "漢 界"
        )

        self._draw_position_markers(painter)

    def _draw_position_markers(self, painter: QPainter):
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE
        mark_size = max(4, int(cs * 0.15))
        offset = max(3, int(cs * 0.1))

        pos_col = [0, 2, 4, 6, 8]
        for col in pos_col:
            for row in [3, 6]:
                self._draw_marker(painter, col, row, offset, mark_size)
        for col in [1, 7]:
            for row in [2, 7]:
                self._draw_marker(painter, col, row, offset, mark_size)

    def _draw_marker(self, painter: QPainter, col: int, row: int,
                     offset: int, size: int):
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE
        x = margin_x + col * cs
        y = margin_y + row * cs
        pen = QPen(QColor(139, 90, 43), 1.5)
        painter.setPen(pen)

        corners = []
        if col > 0:
            corners.append((-1, -1))
            corners.append((-1, 1))
        if col < self.COLS - 1:
            corners.append((1, -1))
            corners.append((1, 1))

        for dx, dy in corners:
            cx = x + dx * offset
            cy = y + dy * offset
            painter.drawLine(cx, cy, cx + dx * size, cy)
            painter.drawLine(cx, cy, cx, cy + dy * size)

    def _draw_pieces(self, painter: QPainter):
        if not self.engine:
            return
        for row in range(self.ROWS):
            for col in range(self.COLS):
                piece = self.engine.get_piece(col, row)
                if piece:
                    self._draw_piece(painter, col, row, piece)

    def _draw_piece(self, painter: QPainter, col: int, row: int,
                    piece: Piece):
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE
        x = margin_x + col * cs
        y = margin_y + row * cs
        radius = cs * 0.42

        painter.setPen(Qt.PenStyle.NoPen)
        shadow_gradient = QRadialGradient(
            QPointF(x + 2, y + 3), radius * 1.1
        )
        shadow_gradient.setColorAt(0, QColor(0, 0, 0, 60))
        shadow_gradient.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setBrush(QBrush(shadow_gradient))
        painter.drawEllipse(QPointF(x + 2, y + 3), radius * 1.1, radius * 1.1)

        if piece.side == Side.RED:
            piece_color = QColor(220, 50, 50)
        else:
            piece_color = QColor(40, 40, 40)
        bg_color = QColor(255, 248, 220)

        outer_pen = QPen(piece_color, max(1, int(cs * 0.04)))
        painter.setPen(outer_pen)
        painter.setBrush(QBrush(bg_color))
        painter.drawEllipse(QPointF(x, y), radius, radius)

        inner_radius = radius * 0.8
        inner_pen = QPen(piece_color, max(1, int(cs * 0.025)))
        painter.setPen(inner_pen)
        painter.drawEllipse(QPointF(x, y), inner_radius, inner_radius)

        font_size = max(10, int(radius * 0.75))
        font = QFont()
        font.setFamilies(["Microsoft YaHei", "SimHei", "SimSun", "PingFang SC", "KaiTi"])
        font.setPointSize(font_size)
        font.setBold(True)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(font)
        painter.setPen(piece_color)

        text_inset = max(2, int(radius * 0.2))
        text_rect = QRectF(x - radius + text_inset, y - radius + text_inset,
                           (radius - text_inset) * 2, (radius - text_inset) * 2)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, piece.name)

        if self.selected_pos == (col, row):
            highlight_pen = QPen(QColor(255, 200, 0), max(2, int(cs * 0.06)))
            painter.setPen(highlight_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(x, y), radius + 4, radius + 4)

    def _draw_highlights(self, painter: QPainter):
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE

        if self.last_move:
            from_pos, to_pos = self.last_move
            for pos in [from_pos, to_pos]:
                if pos:
                    x = margin_x + pos[0] * cs
                    y = margin_y + pos[1] * cs
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(255, 220, 100, 50)))
                    painter.drawEllipse(QPointF(x, y), cs * 0.35, cs * 0.35)

        if self.selected_pos and self.valid_moves:
            for move_col, move_row in self.valid_moves:
                x = margin_x + move_col * cs
                y = margin_y + move_row * cs
                target_piece = self.engine.get_piece(move_col, move_row)

                if target_piece:
                    pen = QPen(QColor(255, 100, 100), max(1, int(cs * 0.03)),
                               Qt.PenStyle.DashLine)
                    painter.setPen(pen)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(x, y),
                                        cs * 0.48, cs * 0.48)
                else:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(100, 180, 255, 150)))
                    painter.drawEllipse(QPointF(x, y),
                                        cs * 0.15, cs * 0.15)

    def mousePressEvent(self, event):
        if not self.engine or not self._is_player_turn or self.engine.game_over:
            return

        self._calc_size()
        pos = self._click_to_position(event.pos())
        if not pos:
            return

        col, row = pos
        clicked_piece = self.engine.get_piece(col, row)

        if clicked_piece and clicked_piece.side == Side.RED:
            self.selected_pos = pos
            self.valid_moves = self.engine.get_valid_moves(col, row)
            self.update()
            return

        if self.selected_pos:
            if pos in self.valid_moves:
                from_pos = self.selected_pos
                self.last_move = (from_pos, pos)
                self.selected_pos = None
                self.valid_moves = []
                self.piece_moved.emit(list(from_pos), list(pos))
                self.update()
                return

        self.selected_pos = None
        self.valid_moves = []
        self.update()

    @staticmethod
    def _half_up(x: float) -> int:
        """四舍五入（避免 round() 的银行家舍入问题）"""
        return int(x + 0.5) if x >= 0 else int(x - 0.5) + 1

    def _click_to_position(self, pos: QPointF) -> Optional[Tuple[int, int]]:
        margin_x = self.MARGIN
        margin_y = self.MARGIN_Y
        cs = self.CELL_SIZE

        col = self._half_up((pos.x() - margin_x) / cs)
        row = self._half_up((pos.y() - margin_y) / cs)

        if 0 <= col < self.COLS and 0 <= row < self.ROWS:
            x = margin_x + col * cs
            y = margin_y + row * cs
            if abs(pos.x() - x) <= cs * 0.55 and abs(pos.y() - y) <= cs * 0.55:
                return (col, row)
        return None

    def set_last_move(self, from_pos: Tuple[int, int],
                      to_pos: Tuple[int, int]):
        self.last_move = (from_pos, to_pos)
        self.update()
