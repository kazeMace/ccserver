"""Projection from GameRuntime to frontend snapshots."""

from __future__ import annotations

from typing import Any

from drama_engine.core.ports.views import BaseViewProjector
from drama_engine.core.session.runtime import GameRuntime
from drama_engine.core.session.view_contract import ViewSnapshot, ViewerPrincipal

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
            roles=_roles_panel(runtime),  # 新增：roles 信息
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
            roles=_roles_panel(runtime),  # 新增
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
            roles=_roles_panel(runtime),  # 新增
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


def _game_attr(runtime: GameRuntime, entity: str, key: str) -> Any:
    """从 runner 的活跃游戏状态读取一个实体属性；不可用时返回 None。

    视图层不认识任何具体游戏的角色/阵营——展示信息（role_title/faction/visible_scopes）
    由脚本或 game_pack 写进游戏状态，视图只负责读取与通用回退（M1 去狼人杀硬编码）。
    """
    runner = getattr(runtime, "runner", None)
    state = getattr(runner, "game_state", None) if runner is not None else None
    if state is None or not state.has_entity(entity):
        return None
    return state.get_attr(entity, key)


def _role_card(runtime: GameRuntime, seat_id: str) -> dict[str, Any] | None:
    seat = runtime.session.seats.get(seat_id)
    if seat is None or not seat.role_snapshot:
        return None
    role = seat.role_snapshot
    # title/faction 从游戏状态读取（脚本/game_pack 声明），无声明时通用回退：
    # title 回退为 role 本身，faction 回退 unknown。视图层不写死任何角色名。
    title = _game_attr(runtime, seat_id, "role_title") or role
    faction = _game_attr(runtime, seat_id, "faction") or "unknown"
    return {
        "role": role,
        "title": title,
        "faction": faction,
        "alive": seat.alive_snapshot if seat.alive_snapshot is not None else True,
    }


def _visible_scopes(runtime: GameRuntime, seat_id: str) -> list[str]:
    """返回该席位可见的 scope 列表。

    额外私密 scope（如狼人频道/预言家私聊）由脚本写进席位状态的 visible_scopes 属性，
    视图层只做通用合并——不再按 werewolf/seer/witch 等具体角色硬编码。
    """
    base = ["public", "town"]
    declared = _game_attr(runtime, seat_id, "visible_scopes")
    if isinstance(declared, (list, tuple)):
        for scope in declared:
            if str(scope) not in base:
                base.append(str(scope))
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


def _roles_panel(runtime: GameRuntime) -> dict[str, Any]:
    """从 State 读取所有 roles 信息并投影为前端所需格式。

    返回格式：
    {
      "nora": {
        "name": "Nora Hampton",
        "description": "精英律师...",
        "portrait_url": "https://...",
        "emoji": "⚖️",
        "voice_id": "en-US-JennyNeural",
        "faction": "protagonist"
      },
      ...
    }
    """
    # 从 runner 获取 State
    runner = getattr(runtime, "runner", None)
    if runner is None:
        return {}

    state = getattr(runner, "game_state", None)
    if state is None:
        return {}

    # 读取 GAME.roles
    roles = state.get_attr("GAME", "roles")
    if not roles or not isinstance(roles, dict):
        return {}

    # 投影为前端格式（只传必要字段）
    result = {}
    for role_id, role_data in roles.items():
        result[role_id] = {
            "name": role_data.get("display_name", role_id),
            "description": role_data.get("description", ""),
            "portrait_url": role_data.get("portrait_url", ""),
            "emoji": role_data.get("emoji", ""),
            "voice_id": role_data.get("voice_id", ""),
            "faction": role_data.get("faction", ""),
        }

    return result

