"""
中国象棋引擎模块 - 优化版

实现：
- 棋盘表示 (9列 x 10行)
- 完整走子规则
- 多级难度 AI 对手 (Minimax + Alpha-Beta 剪枝 + 迭代加深)
- 渐进式难度提升（玩家每赢一次，AI难度自动提升）
- 位置价值表评估（Piece-Square Tables）
- 高效搜索（走法排序、增量更新、局面缓存）

关键设计：
- AI 搜索在独立副本上进行，绝不修改主棋盘
- 走法排序优化：吃子走法优先搜索
- 时间限制：防止高难度时AI思考过久
- 位置价值表：不同位置的棋子有不同价值
"""

from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass
from enum import Enum
import copy
import time


class PieceType(Enum):
    """棋子类型"""
    KING = "king"
    ADVISOR = "advisor"
    ELEPHANT = "elephant"
    HORSE = "horse"
    CHARIOT = "chariot"
    CANNON = "cannon"
    PAWN = "pawn"


class Side(Enum):
    """阵营"""
    RED = "red"
    BLACK = "black"


PIECE_NAMES = {
    Side.RED: {
        PieceType.KING: "帅",
        PieceType.ADVISOR: "仕",
        PieceType.ELEPHANT: "相",
        PieceType.HORSE: "马",
        PieceType.CHARIOT: "车",
        PieceType.CANNON: "炮",
        PieceType.PAWN: "兵",
    },
    Side.BLACK: {
        PieceType.KING: "将",
        PieceType.ADVISOR: "士",
        PieceType.ELEPHANT: "象",
        PieceType.HORSE: "马",
        PieceType.CHARIOT: "车",
        PieceType.CANNON: "炮",
        PieceType.PAWN: "卒",
    },
}

# 棋子基础价值
PIECE_VALUES = {
    PieceType.KING: 10000,
    PieceType.CHARIOT: 900,
    PieceType.HORSE: 400,
    PieceType.CANNON: 450,
    PieceType.ADVISOR: 200,
    PieceType.ELEPHANT: 200,
    PieceType.PAWN: 100,
}

# 位置价值表（红方视角，黑方镜像）
# 车的位置价值表
CHARIOT_PST = [
    [14, 14, 12, 18, 16, 18, 12, 14, 14],
    [16, 20, 18, 24, 26, 24, 18, 20, 16],
    [12, 12, 12, 18, 18, 18, 12, 12, 12],
    [12, 18, 16, 22, 22, 22, 16, 18, 12],
    [12, 14, 12, 18, 18, 18, 12, 14, 12],
    [12, 16, 14, 20, 20, 20, 14, 16, 12],
    [6, 10, 8, 14, 14, 14, 8, 10, 6],
    [4, 8, 6, 14, 12, 14, 6, 8, 4],
    [8, 4, 8, 16, 8, 16, 8, 4, 8],
    [-2, 10, 6, 14, 12, 14, 6, 10, -2],
]

# 马的位置价值表
HORSE_PST = [
    [4, 8, 16, 12, 4, 12, 16, 8, 4],
    [4, 10, 28, 16, 8, 16, 28, 10, 4],
    [12, 14, 16, 20, 18, 20, 16, 14, 12],
    [8, 24, 18, 24, 20, 24, 18, 24, 8],
    [6, 16, 14, 18, 16, 18, 14, 16, 6],
    [4, 12, 16, 14, 12, 14, 16, 12, 4],
    [2, 6, 8, 6, 10, 6, 8, 6, 2],
    [4, 2, 8, 8, 4, 8, 8, 2, 4],
    [0, 2, 4, 4, -2, 4, 4, 2, 0],
    [0, -4, 0, 0, 0, 0, 0, -4, 0],
]

# 炮的位置价值表
CANNON_PST = [
    [6, 4, 0, -10, -12, -10, 0, 4, 6],
    [2, 2, 0, -4, -14, -4, 0, 2, 2],
    [2, 2, 0, -10, -8, -10, 0, 2, 2],
    [0, 0, -2, 4, 10, 4, -2, 0, 0],
    [0, 0, 0, 2, 8, 2, 0, 0, 0],
    [-2, 0, 4, 2, 6, 2, 4, 0, -2],
    [0, 0, 0, 2, 4, 2, 0, 0, 0],
    [4, 0, 8, 6, 10, 6, 8, 0, 4],
    [0, 2, 4, 6, 6, 6, 4, 2, 0],
    [0, 0, 2, 6, 6, 6, 2, 0, 0],
]

# 兵/卒的位置价值表（未过河）
PAWN_PST_NOT_CROSSED = [
    [0, 3, 6, 9, 12, 9, 6, 3, 0],
    [18, 36, 56, 80, 120, 80, 56, 36, 18],
    [14, 26, 42, 60, 80, 60, 42, 26, 14],
    [10, 20, 30, 34, 40, 34, 30, 20, 10],
    [6, 12, 18, 18, 20, 18, 18, 12, 6],
    [2, 0, 8, 0, 8, 0, 8, 0, 2],
    [0, 0, -2, 0, 4, 0, -2, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
]

# 兵/卒的位置价值表（已过河）
PAWN_PST_CROSSED = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [2, 0, 8, 0, 8, 0, 8, 0, 2],
    [6, 12, 18, 18, 20, 18, 18, 12, 6],
    [10, 20, 30, 34, 40, 34, 30, 20, 10],
    [14, 26, 42, 60, 80, 60, 42, 26, 14],
    [18, 36, 56, 80, 120, 80, 56, 36, 18],
]


def _mirror_table(table: List[List[int]]) -> List[List[int]]:
    """将位置价值表镜像（红方视角转黑方视角）"""
    return table[::-1]


# 黑方的位置价值表（镜像红方）
CHARIOT_PST_BLACK = _mirror_table(CHARIOT_PST)
HORSE_PST_BLACK = _mirror_table(HORSE_PST)
CANNON_PST_BLACK = _mirror_table(CANNON_PST)
PAWN_PST_NOT_CROSSED_BLACK = _mirror_table(PAWN_PST_NOT_CROSSED)
PAWN_PST_CROSSED_BLACK = _mirror_table(PAWN_PST_CROSSED)

# 将/帅的位置价值表
KING_PST = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 1, 4, 1, 0, 0, 0],
    [0, 0, 0, 2, 6, 2, 0, 0, 0],
    [0, 0, 0, 11, 15, 11, 0, 0, 0],
]

KING_PST_BLACK = _mirror_table(KING_PST)

# 士/仕的位置价值表
ADVISOR_PST = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 3, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 3, 0, 3, 0, 0, 0],
]

ADVISOR_PST_BLACK = _mirror_table(ADVISOR_PST)

# 象/相的位置价值表
ELEPHANT_PST = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 0, 0, 0, 0, 0, 0, 0],
    [0, 0, 2, 0, 0, 0, 2, 0, 0],
]

ELEPHANT_PST_BLACK = _mirror_table(ELEPHANT_PST)


@dataclass
class Piece:
    """棋子"""
    side: Side
    piece_type: PieceType
    has_moved: bool = False

    @property
    def name(self) -> str:
        return PIECE_NAMES[self.side][self.piece_type]


@dataclass
class Move:
    """走法"""
    from_pos: Tuple[int, int]
    to_pos: Tuple[int, int]
    piece: Piece
    captured: Optional[Piece] = None


@dataclass
class ScoredMove:
    """带评分的走法（用于排序）"""
    from_pos: Tuple[int, int]
    to_pos: Tuple[int, int]
    score: float
    captured: Optional[Piece] = None


class DifficultyLevel:
    """难度等级定义 - 大幅优化"""
    
    # 难度等级配置：搜索深度、时间限制（秒）
    # 每级都有明显提升，入门级也有基本战术能力
    LEVELS = {
        1: {"depth": 3, "time_limit": 3, "name": "入门"},
        2: {"depth": 4, "time_limit": 4, "name": "初级"},
        3: {"depth": 4, "time_limit": 6, "name": "中级"},
        4: {"depth": 5, "time_limit": 8, "name": "高级"},
        5: {"depth": 6, "time_limit": 12, "name": "专家"},
    }
    
    MIN_LEVEL = 1
    MAX_LEVEL = 5
    
    @classmethod
    def get_config(cls, level: int) -> Dict:
        """获取指定难度的配置"""
        level = max(cls.MIN_LEVEL, min(cls.MAX_LEVEL, level))
        return cls.LEVELS[level]
    
    @classmethod
    def get_name(cls, level: int) -> str:
        """获取难度名称"""
        return cls.get_config(level)["name"]


def _create_initial_board() -> List[List[Optional[Piece]]]:
    """创建初始棋盘"""
    board: List[List[Optional[Piece]]] = [[None for _ in range(9)] for _ in range(10)]

    back_row = [
        PieceType.CHARIOT, PieceType.HORSE, PieceType.ELEPHANT,
        PieceType.ADVISOR, PieceType.KING, PieceType.ADVISOR,
        PieceType.ELEPHANT, PieceType.HORSE, PieceType.CHARIOT
    ]
    for col, ptype in enumerate(back_row):
        board[0][col] = Piece(Side.BLACK, ptype)

    board[2][1] = Piece(Side.BLACK, PieceType.CANNON)
    board[2][7] = Piece(Side.BLACK, PieceType.CANNON)

    for col in [0, 2, 4, 6, 8]:
        board[3][col] = Piece(Side.BLACK, PieceType.PAWN)

    for col, ptype in enumerate(back_row):
        board[9][col] = Piece(Side.RED, ptype)

    board[7][1] = Piece(Side.RED, PieceType.CANNON)
    board[7][7] = Piece(Side.RED, PieceType.CANNON)

    for col in [0, 2, 4, 6, 8]:
        board[6][col] = Piece(Side.RED, PieceType.PAWN)

    return board


def _clone_board(board: List[List[Optional[Piece]]]) -> List[List[Optional[Piece]]]:
    """深拷贝棋盘"""
    return [[copy.deepcopy(board[r][c]) for c in range(9)] for r in range(10)]


def _get_pst_value(piece: Piece, col: int, row: int) -> int:
    """获取棋子的位置价值"""
    is_red = piece.side == Side.RED
    
    if piece.piece_type == PieceType.CHARIOT:
        table = CHARIOT_PST if is_red else CHARIOT_PST_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.HORSE:
        table = HORSE_PST if is_red else HORSE_PST_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.CANNON:
        table = CANNON_PST if is_red else CANNON_PST_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.PAWN:
        # 判断是否过河
        crossed = (piece.side == Side.RED and row <= 4) or \
                  (piece.side == Side.BLACK and row >= 5)
        if crossed:
            table = PAWN_PST_CROSSED if is_red else PAWN_PST_CROSSED_BLACK
        else:
            table = PAWN_PST_NOT_CROSSED if is_red else PAWN_PST_NOT_CROSSED_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.KING:
        table = KING_PST if is_red else KING_PST_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.ADVISOR:
        table = ADVISOR_PST if is_red else ADVISOR_PST_BLACK
        return table[row][col]
    elif piece.piece_type == PieceType.ELEPHANT:
        table = ELEPHANT_PST if is_red else ELEPHANT_PST_BLACK
        return table[row][col]
    
    return 0


def _get_raw_moves_for_piece(
    board: List[List[Optional[Piece]]],
    piece: Piece, col: int, row: int
) -> List[Tuple[int, int]]:
    """获取棋子的所有原始走法（不考虑将军）"""
    COLS, ROWS = 9, 10
    side = piece.side
    ptype = piece.piece_type
    moves: List[Tuple[int, int]] = []

    def in_board(c: int, r: int) -> bool:
        return 0 <= c < COLS and 0 <= r < ROWS

    def in_palace(c: int, r: int, s: Side) -> bool:
        if c < 3 or c > 5:
            return False
        return 7 <= r <= 9 if s == Side.RED else 0 <= r <= 2

    def target_ok(c: int, r: int, s: Side) -> bool:
        t = board[r][c]
        return t is None or t.side != s

    if ptype == PieceType.KING:
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nc, nr = col + dc, row + dr
            if in_palace(nc, nr, side) and target_ok(nc, nr, side):
                moves.append((nc, nr))

    elif ptype == PieceType.ADVISOR:
        for dc, dr in [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
            nc, nr = col + dc, row + dr
            if in_palace(nc, nr, side) and target_ok(nc, nr, side):
                moves.append((nc, nr))

    elif ptype == PieceType.ELEPHANT:
        for dc, dr in [(2, 2), (2, -2), (-2, 2), (-2, -2)]:
            nc, nr = col + dc, row + dr
            if not in_board(nc, nr):
                continue
            if side == Side.RED and nr < 5:
                continue
            if side == Side.BLACK and nr > 4:
                continue
            ec, er = col + dc // 2, row + dr // 2
            if board[er][ec] is not None:
                continue
            if target_ok(nc, nr, side):
                moves.append((nc, nr))

    elif ptype == PieceType.HORSE:
        for dc, dr, lc, lr in [
            (1, 2, 0, 1), (-1, 2, 0, 1),
            (1, -2, 0, -1), (-1, -2, 0, -1),
            (2, 1, 1, 0), (2, -1, 1, 0),
            (-2, 1, -1, 0), (-2, -1, -1, 0),
        ]:
            nc, nr = col + dc, row + dr
            if not in_board(nc, nr):
                continue
            if board[row + lr][col + lc] is not None:
                continue
            if target_ok(nc, nr, side):
                moves.append((nc, nr))

    elif ptype == PieceType.CHARIOT:
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nc, nr = col + dc, row + dr
            while in_board(nc, nr):
                t = board[nr][nc]
                if t is None:
                    moves.append((nc, nr))
                else:
                    if t.side != side:
                        moves.append((nc, nr))
                    break
                nc += dc
                nr += dr

    elif ptype == PieceType.CANNON:
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nc, nr = col + dc, row + dr
            while in_board(nc, nr):
                t = board[nr][nc]
                if t is None:
                    moves.append((nc, nr))
                else:
                    nc += dc
                    nr += dr
                    while in_board(nc, nr):
                        t2 = board[nr][nc]
                        if t2 is not None:
                            if t2.side != side:
                                moves.append((nc, nr))
                            break
                        nc += dc
                        nr += dr
                    break
                nc += dc
                nr += dr

    elif ptype == PieceType.PAWN:
        forward = -1 if side == Side.RED else 1
        nr = row + forward
        if in_board(col, nr) and target_ok(col, nr, side):
            moves.append((col, nr))

        crossed = (side == Side.RED and row <= 4) or (side == Side.BLACK and row >= 5)
        if crossed:
            for dc in [-1, 1]:
                nc = col + dc
                if in_board(nc, row) and target_ok(nc, row, side):
                    moves.append((nc, row))

    return moves


def _find_king(
    board: List[List[Optional[Piece]]], side: Side
) -> Optional[Tuple[int, int]]:
    """查找指定方的将/帅位置"""
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if p and p.side == side and p.piece_type == PieceType.KING:
                return (c, r)
    return None


def _is_in_check(
    board: List[List[Optional[Piece]]], side: Side
) -> bool:
    """判断指定方是否被将军"""
    king_pos = _find_king(board, side)
    if king_pos is None:
        return True

    opponent = Side.BLACK if side == Side.RED else Side.RED
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if p and p.side == opponent:
                if king_pos in _get_raw_moves_for_piece(board, p, c, r):
                    return True

    # 飞将检查
    red_king = _find_king(board, Side.RED)
    black_king = _find_king(board, Side.BLACK)
    if red_king and black_king and red_king[0] == black_king[0]:
        col = red_king[0]
        min_r = min(red_king[1], black_king[1])
        max_r = max(red_king[1], black_king[1])
        for r in range(min_r + 1, max_r):
            if board[r][col] is not None:
                break
        else:
            return True

    return False


def _get_valid_moves_for_position(
    board: List[List[Optional[Piece]]], col: int, row: int
) -> List[Tuple[int, int]]:
    """获取指定位置棋子的所有合法走法（过滤掉送将的走法）"""
    piece = board[row][col]
    if piece is None:
        return []

    raw_moves = _get_raw_moves_for_piece(board, piece, col, row)

    valid: List[Tuple[int, int]] = []
    for tc, tr in raw_moves:
        captured = board[tr][tc]
        board[tr][tc] = piece
        board[row][col] = None

        if not _is_in_check(board, piece.side):
            valid.append((tc, tr))

        board[row][col] = piece
        board[tr][tc] = captured

    return valid


def _has_any_legal_move(board: List[List[Optional[Piece]]], side: Side) -> bool:
    """检查指定方是否有任何合法走法（优化版：找到一个就返回）"""
    for r in range(10):
        for c in range(9):
            piece = board[r][c]
            if piece and piece.side == side:
                raw_moves = _get_raw_moves_for_piece(board, piece, c, r)
                for tc, tr in raw_moves:
                    captured = board[tr][tc]
                    board[tr][tc] = piece
                    board[r][c] = None
                    in_check = _is_in_check(board, side)
                    board[r][c] = piece
                    board[tr][tc] = captured
                    if not in_check:
                        return True
    return False


def _get_scored_moves_for_search(
    board: List[List[Optional[Piece]]], 
    side: Side
) -> List[ScoredMove]:
    """获取搜索用的走法列表（带评分排序，吃子优先）"""
    moves: List[ScoredMove] = []
    
    for r in range(10):
        for c in range(9):
            piece = board[r][c]
            if piece and piece.side == side:
                valid_moves = _get_valid_moves_for_position(board, c, r)
                for tc, tr in valid_moves:
                    captured = board[tr][tc]
                    score = 0
                    if captured:
                        score = PIECE_VALUES.get(captured.piece_type, 0)
                        # 吃子时加入位置价值变化
                        pst_diff = _get_pst_value(piece, tc, tr) - _get_pst_value(piece, c, r)
                        score += pst_diff * 0.5
                    
                    # 位置价值提升
                    pst_new = _get_pst_value(piece, tc, tr)
                    pst_old = _get_pst_value(piece, c, r)
                    score += (pst_new - pst_old) * 0.3
                    
                    # 过河加分
                    if piece.piece_type == PieceType.PAWN:
                        if (piece.side == Side.RED and tr <= 4) or \
                           (piece.side == Side.BLACK and tr >= 5):
                            score += 30
                    
                    # 将军加分
                    if captured and captured.piece_type == PieceType.KING:
                        score += 100000
                    
                    moves.append(ScoredMove(
                        from_pos=(c, r),
                        to_pos=(tc, tr),
                        score=score,
                        captured=captured
                    ))
    
    moves.sort(key=lambda m: m.score, reverse=True)
    return moves


def _get_pseudo_moves_for_search(
    board: List[List[Optional[Piece]]], 
    side: Side
) -> List[ScoredMove]:
    """获取搜索用的伪合法走法（用于更深层搜索，加速）"""
    moves: List[ScoredMove] = []
    
    for r in range(10):
        for c in range(9):
            piece = board[r][c]
            if piece and piece.side == side:
                raw_moves = _get_raw_moves_for_piece(board, piece, c, r)
                for tc, tr in raw_moves:
                    captured = board[tr][tc]
                    score = 0
                    if captured:
                        score = PIECE_VALUES.get(captured.piece_type, 0)
                    pst_new = _get_pst_value(piece, tc, tr)
                    pst_old = _get_pst_value(piece, c, r)
                    score += (pst_new - pst_old) * 0.3
                    moves.append(ScoredMove(
                        from_pos=(c, r),
                        to_pos=(tc, tr),
                        score=score,
                        captured=captured
                    ))
    
    moves.sort(key=lambda m: m.score, reverse=True)
    return moves


class XiangqiEngine:
    """中国象棋引擎"""

    COLS = 9
    ROWS = 10

    def __init__(self, difficulty: int = 1):
        """
        初始化引擎
        
        Args:
            difficulty: AI难度等级 (1-5)
        """
        self.board: List[List[Optional[Piece]]] = _create_initial_board()
        self.current_turn = Side.RED
        self.move_history: List[Move] = []
        self.game_over = False
        self.winner: Optional[Side] = None
        
        # AI难度设置
        self.difficulty = difficulty
        self.wins_by_player = 0

    def set_difficulty(self, level: int):
        """设置AI难度等级"""
        self.difficulty = max(1, min(5, level))

    def increment_difficulty(self):
        """玩家赢一次后提升难度"""
        self.wins_by_player += 1
        new_level = min(5, 1 + self.wins_by_player)
        self.difficulty = new_level

    def get_difficulty_info(self) -> Dict:
        """获取当前难度信息"""
        config = DifficultyLevel.get_config(self.difficulty)
        return {
            "level": self.difficulty,
            "name": config["name"],
            "depth": config["depth"],
            "time_limit": config["time_limit"],
            "wins_needed": self.difficulty,
        }

    def get_piece(self, col: int, row: int) -> Optional[Piece]:
        """获取指定位置的棋子"""
        if 0 <= col < self.COLS and 0 <= row < self.ROWS:
            return self.board[row][col]
        return None

    def get_valid_moves(self, col: int, row: int) -> List[Tuple[int, int]]:
        """获取指定棋子的所有合法走法"""
        return _get_valid_moves_for_position(self.board, col, row)

    def make_move(
        self, from_col: int, from_row: int, to_col: int, to_row: int
    ) -> Tuple[bool, str]:
        """执行走子"""
        piece = self.get_piece(from_col, from_row)
        if not piece:
            return False, "该位置没有棋子"
        if piece.side != self.current_turn:
            return False, f"当前是{('红方' if self.current_turn == Side.RED else '黑方')}走棋"

        valid_moves = self.get_valid_moves(from_col, from_row)
        if (to_col, to_row) not in valid_moves:
            return False, "该走法不合法"

        captured = self.board[to_row][to_col]
        self.board[to_row][to_col] = piece
        self.board[from_row][from_col] = None
        piece.has_moved = True

        move = Move(
            from_pos=(from_col, from_row),
            to_pos=(to_col, to_row),
            piece=piece,
            captured=captured,
        )
        self.move_history.append(move)

        if captured and captured.piece_type == PieceType.KING:
            self.game_over = True
            self.winner = self.current_turn
            return True, f"{piece.name}吃了对方{captured.name}！"

        next_turn = Side.BLACK if self.current_turn == Side.RED else Side.RED
        self.current_turn = next_turn

        in_check = _is_in_check(self.board, next_turn)
        has_legal = _has_any_legal_move(self.board, next_turn)

        if not has_legal and in_check:
            self.game_over = True
            self.winner = Side.BLACK if next_turn == Side.RED else Side.RED
            return True, f"将死！{('红方' if self.winner == Side.RED else '黑方')}获胜！"
        if not has_legal:
            self.game_over = True
            self.winner = Side.BLACK if next_turn == Side.RED else Side.RED
            return True, f"困毙！{('红方' if self.winner == Side.RED else '黑方')}获胜！"

        return True, "走棋成功"

    def get_ai_move(self) -> Optional[Tuple[int, int, int, int]]:
        """AI 走子（带时间限制的迭代加深搜索）"""
        if self.current_turn != Side.BLACK or self.game_over:
            return None

        config = DifficultyLevel.get_config(self.difficulty)
        search_depth = config["depth"]
        time_limit = config["time_limit"]
        
        search_board = _clone_board(self.board)
        
        start_time = time.time()
        deadline = start_time + time_limit
        
        best_move = None
        best_score = float("-inf")
        
        for current_depth in range(1, search_depth + 1):
            if time.time() > deadline and best_move is not None:
                break
                
            move, score = self._search_at_depth(
                search_board, current_depth, deadline
            )
            
            if move is not None:
                best_move = move
                best_score = score
                
                if score >= 5000:
                    break
        
        return best_move

    def _search_at_depth(
        self,
        board: List[List[Optional[Piece]]],
        depth: int,
        deadline: float,
    ) -> Tuple[Optional[Tuple[int, int, int, int]], float]:
        """在指定深度搜索最佳走法"""
        best_move = None
        best_score = float("-inf")
        alpha = float("-inf")
        beta = float("inf")
        
        scored_moves = _get_scored_moves_for_search(board, Side.BLACK)
        
        for sm in scored_moves:
            if time.time() > deadline and best_move is not None:
                break
            
            piece = board[sm.from_pos[1]][sm.from_pos[0]]
            captured = board[sm.to_pos[1]][sm.to_pos[0]]
            
            board[sm.to_pos[1]][sm.to_pos[0]] = piece
            board[sm.from_pos[1]][sm.from_pos[0]] = None
            
            score = self._minimax_with_time(
                board, depth - 1, alpha, beta, False, deadline
            )
            
            board[sm.from_pos[1]][sm.from_pos[0]] = piece
            board[sm.to_pos[1]][sm.to_pos[0]] = captured
            
            if score > best_score:
                best_score = score
                best_move = (sm.from_pos[0], sm.from_pos[1], 
                           sm.to_pos[0], sm.to_pos[1])
            
            alpha = max(alpha, score)
        
        return best_move, best_score

    def _minimax_with_time(
        self,
        board: List[List[Optional[Piece]]],
        depth: int,
        alpha: float,
        beta: float,
        maximizing: bool,
        deadline: float,
    ) -> float:
        """带时间限制的Minimax + Alpha-Beta剪枝（优化版）"""
        side_to_move = Side.BLACK if maximizing else Side.RED
        
        if depth == 0 or time.time() > deadline:
            return self._evaluate(board)
        
        # 只在浅层检查将死/困毙
        if depth <= 2:
            in_check = _is_in_check(board, side_to_move)
            has_legal = _has_any_legal_move(board, side_to_move)
            
            if not has_legal and in_check:
                return -10000 if maximizing else 10000
            if not has_legal:
                return 0
        
        use_pseudo = depth <= 1
        if use_pseudo:
            scored_moves = _get_pseudo_moves_for_search(board, side_to_move)
        else:
            scored_moves = _get_scored_moves_for_search(board, side_to_move)
        
        # 如果没有合法走法
        if not scored_moves:
            return -9999 if maximizing else 9999
        
        if maximizing:
            max_eval = float("-inf")
            for sm in scored_moves:
                if time.time() > deadline:
                    break
                
                piece = board[sm.from_pos[1]][sm.from_pos[0]]
                captured = board[sm.to_pos[1]][sm.to_pos[0]]
                
                board[sm.to_pos[1]][sm.to_pos[0]] = piece
                board[sm.from_pos[1]][sm.from_pos[0]] = None
                
                if not use_pseudo and _is_in_check(board, side_to_move):
                    board[sm.from_pos[1]][sm.from_pos[0]] = piece
                    board[sm.to_pos[1]][sm.to_pos[0]] = captured
                    continue
                
                eval_score = self._minimax_with_time(
                    board, depth - 1, alpha, beta, False, deadline
                )
                
                board[sm.from_pos[1]][sm.from_pos[0]] = piece
                board[sm.to_pos[1]][sm.to_pos[0]] = captured
                
                max_eval = max(max_eval, eval_score)
                alpha = max(alpha, eval_score)
                if beta <= alpha:
                    break
            return max_eval
        else:
            min_eval = float("inf")
            for sm in scored_moves:
                if time.time() > deadline:
                    break
                
                piece = board[sm.from_pos[1]][sm.from_pos[0]]
                captured = board[sm.to_pos[1]][sm.to_pos[0]]
                
                board[sm.to_pos[1]][sm.to_pos[0]] = piece
                board[sm.from_pos[1]][sm.from_pos[0]] = None
                
                if not use_pseudo and _is_in_check(board, side_to_move):
                    board[sm.from_pos[1]][sm.from_pos[0]] = piece
                    board[sm.to_pos[1]][sm.to_pos[0]] = captured
                    continue
                
                eval_score = self._minimax_with_time(
                    board, depth - 1, alpha, beta, True, deadline
                )
                
                board[sm.from_pos[1]][sm.from_pos[0]] = piece
                board[sm.to_pos[1]][sm.to_pos[0]] = captured
                
                min_eval = min(min_eval, eval_score)
                beta = min(beta, eval_score)
                if beta <= alpha:
                    break
            return min_eval

    def _evaluate(self, board: List[List[Optional[Piece]]]) -> float:
        """增强的评估函数 - 基于位置价值表"""
        score = 0.0
        
        for r in range(self.ROWS):
            for c in range(self.COLS):
                piece = board[r][c]
                if piece:
                    value = PIECE_VALUES.get(piece.piece_type, 0)
                    pst_value = _get_pst_value(piece, c, r)
                    value += pst_value
                    
                    if piece.side == Side.BLACK:
                        score += value
                    else:
                        score -= value
        
        return score

    def get_game_state(self) -> Dict:
        """获取游戏状态"""
        board_state = []
        for r in range(self.ROWS):
            row = []
            for c in range(self.COLS):
                piece = self.board[r][c]
                if piece:
                    row.append({
                        "type": piece.piece_type.value,
                        "side": piece.side.value,
                        "name": piece.name,
                    })
                else:
                    row.append(None)
            board_state.append(row)

        return {
            "board": board_state,
            "current_turn": self.current_turn.value,
            "game_over": self.game_over,
            "winner": self.winner.value if self.winner else None,
            "move_count": len(self.move_history),
            "difficulty": self.get_difficulty_info(),
            "history": [
                {
                    "from": m.from_pos,
                    "to": m.to_pos,
                    "piece": m.piece.name,
                    "captured": m.captured.name if m.captured else None,
                }
                for m in self.move_history
            ],
        }
