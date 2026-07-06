"""棋盘领域机制（board）。

提供落子/连线检测等原子机制，供五子棋、象棋、围棋、跳棋等棋盘类游戏在 DSL 里直接
按名引用。所有机制只通过 StateWriter 写状态，棋盘数据存放在 GAME.board（dict: "r,c"->棋子）。

机制清单：
- effect  board_place        ：在指定坐标落子，并记录 last_move / last_actor。
- condition board.connect_n  ：判断最近一手是否形成 n 连（横/竖/两斜）。
- condition board.cell_empty ：判断指定坐标是否为空。

坐标约定：cell 用 "row,col" 字符串键；DSL 里 position 传 [row, col]。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)

# 四个方向：横、竖、正斜、反斜。用于连线检测。
_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


def _cell_key(row: int, col: int) -> str:
    """把坐标转成 board dict 的键。"""
    return f"{int(row)},{int(col)}"


def _read_board(state: Any) -> dict[str, Any]:
    """读取当前棋盘 dict；不存在时返回空 dict。"""
    board = state.get_attr("GAME", "board")
    return dict(board) if isinstance(board, dict) else {}


def _resolve_position(effect: dict, context: Any) -> tuple[int, int] | None:
    """从 effect 或当前 response 解析落子坐标 [row, col]。

    优先用 effect.cell（"r,c"）或 effect.position（[r,c]）；否则读第一个 response 的
    data.position。解析失败返回 None。
    """
    cell = effect.get("cell")
    if isinstance(cell, str) and "," in cell:
        row_s, col_s = cell.split(",", 1)
        return int(row_s), int(col_s)
    position = effect.get("position")
    if position is None:
        responses = getattr(context, "responses", None) or []
        if responses:
            data = responses[0].get("data") if isinstance(responses[0], dict) else None
            position = (data or {}).get("position") if isinstance(data, dict) else None
    if isinstance(position, (list, tuple)) and len(position) == 2:
        return int(position[0]), int(position[1])
    return None


def _handle_board_place(effect: dict, context: Any) -> None:
    """在棋盘上落子。

    effect 字段：
      cell / position — 落子坐标；缺省时读当前 response.data.position。
      piece           — 棋子标记；缺省时用 actor 的 role 或 actor 名。
    写入：GAME.board[cell]=piece，GAME.last_move=[r,c]，GAME.last_actor=actor。
    """
    pos = _resolve_position(effect, context)
    assert pos is not None, "board_place 需要 cell/position 或当前 response.data.position"
    row, col = pos
    board = _read_board(context.state)
    key = _cell_key(row, col)
    assert key not in board, f"该点已有子: {key}"
    actor = getattr(context, "actor", None)
    piece = effect.get("piece")
    if not piece:
        piece = context.state.get_attr(actor, "role") if actor else None
    piece = piece or actor or "?"
    board[key] = piece
    context.writer.apply(SetAttr("GAME", "board", board))
    context.writer.apply(SetAttr("GAME", "last_move", [row, col]))
    context.writer.apply(SetAttr("GAME", "last_actor", actor))
    logger.debug("[board_place] cell=%s piece=%s actor=%s", key, piece, actor)


def _max_line(board: dict[str, Any], last_move: list[int]) -> int:
    """返回经过 last_move 这一手、同色棋子的最长连续长度。"""
    if not (isinstance(last_move, (list, tuple)) and len(last_move) == 2):
        return 0
    row, col = int(last_move[0]), int(last_move[1])
    piece = board.get(_cell_key(row, col))
    if piece is None:
        return 0
    best = 0
    for d_row, d_col in _DIRECTIONS:
        count = 1
        # 正方向延伸
        step = 1
        while board.get(_cell_key(row + d_row * step, col + d_col * step)) == piece:
            count += 1
            step += 1
        # 反方向延伸
        step = 1
        while board.get(_cell_key(row - d_row * step, col - d_col * step)) == piece:
            count += 1
            step += 1
        best = max(best, count)
    return best


def _cond_connect_n(spec: dict, context: dict) -> bool:
    """判断最近一手是否形成至少 n 连。

    spec.input.n 或 spec.n 指定连线长度，默认 5（五子棋）。
    """
    state = context.get("state")
    if state is None:
        return False
    n = _read_int(spec, "n", 5)
    board = _read_board(state)
    last_move = state.get_attr("GAME", "last_move")
    return _max_line(board, last_move) >= n


def _cond_cell_empty(spec: dict, context: dict) -> bool:
    """判断指定坐标是否为空。spec.input.position 或 spec.position 指定坐标。"""
    state = context.get("state")
    if state is None:
        return False
    position = _read_value(spec, "position")
    if not (isinstance(position, (list, tuple)) and len(position) == 2):
        return False
    board = _read_board(state)
    return _cell_key(int(position[0]), int(position[1])) not in board


def _read_int(spec: dict, key: str, default: int) -> int:
    """从 spec.input.<key> 或 spec.<key> 读整数。"""
    value = _read_value(spec, key)
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _read_value(spec: dict, key: str) -> Any:
    """从 spec.input.<key> 或 spec.<key> 读值。"""
    if isinstance(spec, dict):
        source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
        return source.get(key)
    return None


def register(api: Any) -> None:
    """把 board 机制注册进 PluginRegistry。"""
    api.register_effect("board_place", _handle_board_place)
    api.register_condition("board.connect_n", _cond_connect_n)
    api.register_condition("board.cell_empty", _cond_cell_empty)


__all__ = ["register"]
