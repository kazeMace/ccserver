"""Frontend view contracts for Drama Engine.

这些 dataclass 是后端给前端的稳定视图契约，不是游戏规则 DSL。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ViewerPrincipal:
    """当前请求对应的 viewer 身份。"""

    viewer_kind: str
    session_id: str
    seat_id: str | None = None
    user_id: str | None = None

    def __post_init__(self) -> None:
        assert self.viewer_kind in {"public", "host", "player"}, f"未知 viewer_kind: {self.viewer_kind}"
        assert self.session_id, "session_id 不能为空"
        if self.viewer_kind == "player":
            assert self.seat_id, "player viewer 必须带 seat_id"


@dataclass(frozen=True, slots=True)
class ViewSnapshot:
    """前端刷新/重连时使用的完整视图快照。"""

    viewer_kind: str
    session_id: str
    session_status: str
    seat_id: str | None = None
    role_card: dict[str, Any] | None = None
    visible_scopes: list[str] = field(default_factory=list)
    seats: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    current_action: dict[str, Any] | None = None
    controls: dict[str, bool] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 可序列化 dict。"""
        return {
            "viewer_kind": self.viewer_kind,
            "session_id": self.session_id,
            "session_status": self.session_status,
            "seat_id": self.seat_id,
            "role_card": self.role_card,
            "visible_scopes": list(self.visible_scopes),
            "seats": [dict(item) for item in self.seats],
            "timeline": [dict(item) for item in self.timeline],
            "current_action": dict(self.current_action) if self.current_action else None,
            "controls": dict(self.controls),
            "meta": dict(self.meta),
        }
