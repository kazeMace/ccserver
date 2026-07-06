"""Projection from GameRuntime to frontend snapshots."""

from __future__ import annotations

from typing import Any

from drama_engine.core.ports.views import BaseViewProjector
from drama_engine.core.session.runtime import GameRuntime
from drama_engine.core.session.view_contract import ViewSnapshot, ViewerPrincipal

ROLE_NAMES = {
    "werewolf": "狼人",
    "seer": "预言家",
    "witch": "女巫",
    "hunter": "猎人",
    "guard": "守卫",
    "villager": "村民",
}


class SocialViewProjector(BaseViewProjector):
    """Project SocialDeduction runtime state into frontend snapshots."""

    def project(
        self,
        runtime: GameRuntime,
        audience: str,
        seat_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a single snapshot payload for one audience."""
        snapshot = self.snapshot(runtime=runtime, audience=audience, seat_id=seat_id, user_id=user_id)
        return [snapshot.to_dict()]

    def snapshot(
        self,
        runtime: GameRuntime,
        audience: str,
        seat_id: str | None = None,
        user_id: str | None = None,
    ) -> ViewSnapshot:
        """Build one host, public, or player snapshot."""
        assert audience in {"host", "public", "player"}, f"未知 audience: {audience}"
        if audience == "host":
            return self.host_snapshot(runtime)
        if audience == "public":
            return self.public_snapshot(runtime)
        assert seat_id, "player snapshot 必须提供 seat_id"
        return self.player_snapshot(runtime, seat_id=seat_id, user_id=user_id)

    def host_snapshot(self, runtime: GameRuntime) -> ViewSnapshot:
        """Build the host snapshot."""
        principal = ViewerPrincipal(viewer_kind="host", session_id=runtime.session.session_id)
        return ViewSnapshot(
            viewer_kind=principal.viewer_kind,
            session_id=principal.session_id,
            session_status=runtime.session.status,
            seats=runtime.seat_summary(),
            timeline=runtime.event_store.host_backlog(),
            current_action={"items": runtime.action_view.pending_summary(runtime)},
            controls={
                "can_assign": runtime.session.status == "lobby",
                "can_start": runtime.session.status == "assigned",
                "can_pause": runtime.session.status == "running",
                "can_resume": runtime.session.status == "paused",
                "can_terminate": runtime.session.status not in {"ended", "failed", "terminated"},
                "can_step": runtime.step_gate.step_mode and runtime.session.status == "running",
            },
            meta={"step_gate": runtime.step_gate.status()},
        )

    def public_snapshot(self, runtime: GameRuntime) -> ViewSnapshot:
        """Build the public viewer snapshot."""
        principal = ViewerPrincipal(viewer_kind="public", session_id=runtime.session.session_id)
        return ViewSnapshot(
            viewer_kind=principal.viewer_kind,
            session_id=principal.session_id,
            session_status=runtime.session.status,
            visible_scopes=["public"],
            seats=_public_seats(runtime),
            timeline=runtime.event_store.public_backlog(),
            controls={},
        )

    def player_snapshot(
        self,
        runtime: GameRuntime,
        seat_id: str,
        user_id: str | None = None,
    ) -> ViewSnapshot:
        """Build one player private snapshot."""
        principal = ViewerPrincipal(
            viewer_kind="player",
            session_id=runtime.session.session_id,
            seat_id=seat_id,
            user_id=user_id,
        )
        role_card = _role_card(runtime, seat_id)
        current_action = _current_action(runtime, seat_id)
        visible_scopes = _visible_scopes(runtime, seat_id)
        return ViewSnapshot(
            viewer_kind=principal.viewer_kind,
            session_id=principal.session_id,
            session_status=runtime.session.status,
            seat_id=seat_id,
            role_card=role_card,
            visible_scopes=visible_scopes,
            seats=_player_visible_seats(runtime, seat_id),
            timeline=runtime.event_store.private_backlog(seat_id),
            current_action=current_action,
            controls={"can_submit": current_action is not None},
        )


_SOCIAL_VIEW_PROJECTOR = SocialViewProjector()


def build_host_snapshot(runtime: GameRuntime) -> ViewSnapshot:
    """构建主持人视图。"""
    return _SOCIAL_VIEW_PROJECTOR.host_snapshot(runtime)


def build_public_snapshot(runtime: GameRuntime) -> ViewSnapshot:
    """构建公开观众视图。"""
    return _SOCIAL_VIEW_PROJECTOR.public_snapshot(runtime)


def build_player_snapshot(runtime: GameRuntime, seat_id: str, user_id: str | None = None) -> ViewSnapshot:
    """构建玩家视图。"""
    return _SOCIAL_VIEW_PROJECTOR.player_snapshot(runtime, seat_id=seat_id, user_id=user_id)


def _role_card(runtime: GameRuntime, seat_id: str) -> dict[str, Any] | None:
    seat = runtime.session.seats.get(seat_id)
    if seat is None or not seat.role_snapshot:
        return None
    role = seat.role_snapshot
    return {
        "role": role,
        "title": ROLE_NAMES.get(role, role),
        "faction": _role_faction(role),
        "alive": seat.alive_snapshot if seat.alive_snapshot is not None else True,
    }


def _role_faction(role: str) -> str:
    if role == "werewolf":
        return "wolf"
    if role:
        return "good"
    return "unknown"


def _visible_scopes(runtime: GameRuntime, seat_id: str) -> list[str]:
    role = runtime.session.seats.get(seat_id).role_snapshot if seat_id in runtime.session.seats else None
    base = ["public", "town"]
    if role == "werewolf":
        base.append("wolf-den")
    elif role == "seer":
        base.append("whisper:seer")
    elif role == "witch":
        base.append("whisper:witch")
    elif role == "guard":
        base.append("whisper:guard")
    return base


def _current_action(runtime: GameRuntime, seat_id: str) -> dict[str, Any] | None:
    return runtime.action_view.current_action(runtime, seat_id)


def _public_seats(runtime: GameRuntime) -> list[dict[str, Any]]:
    result = []
    for seat in runtime.session.seats.values():
        result.append({
            "seat_id": seat.seat_id,
            "alive_snapshot": seat.alive_snapshot,
        })
    return result


def _player_visible_seats(runtime: GameRuntime, viewer_seat_id: str) -> list[dict[str, Any]]:
    result = []
    for seat in runtime.session.seats.values():
        item = {
            "seat_id": seat.seat_id,
            "alive_snapshot": seat.alive_snapshot,
        }
        if seat.seat_id == viewer_seat_id:
            item["role_snapshot"] = seat.role_snapshot
        result.append(item)
    return result
