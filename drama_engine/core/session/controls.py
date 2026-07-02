"""Service-layer controls for seats, tokens, and join links."""

from __future__ import annotations

from drama_engine.core.session.state import CONTROLLER_AI, CONTROLLER_HUMAN
from drama_engine.core.session.tokens import PlayerTokenService

class ServiceSessionControls:
    """Service-layer controls for seats, tokens, and join links."""

    def set_controller(
        self,
        runtime: "PartySessionRuntime",
        seat_id: str,
        controller_type: str,
        token_service: PlayerTokenService,
    ) -> str:
        """Set one seat controller and return a join link for human seats."""
        assert runtime is not None, "runtime 不能为空"
        assert seat_id in runtime.session.seats, f"seat 不存在: {seat_id}"
        assert controller_type in {CONTROLLER_AI, CONTROLLER_HUMAN}, f"未知 controller_type: {controller_type}"
        seat = runtime.session.seats[seat_id]
        seat.controller_type = controller_type
        if controller_type == CONTROLLER_HUMAN:
            runtime.session.human_seat_ids.add(seat_id)
            token = token_service.create_token(runtime.session.session_id, seat_id)
            link = f"/player?token={token}"
            runtime.player_links[seat_id] = link
            runtime.event_store.append_host({
                "kind": "seat_controller_changed",
                "seat_id": seat_id,
                "controller_type": controller_type,
                "join_link": link,
            })
            return link
        runtime.session.human_seat_ids.discard(seat_id)
        runtime.player_links.pop(seat_id, None)
        runtime.event_store.append_host({
            "kind": "seat_controller_changed",
            "seat_id": seat_id,
            "controller_type": controller_type,
        })
        return ""

    def set_human_count(
        self,
        runtime: "PartySessionRuntime",
        count: int,
        token_service: PlayerTokenService,
    ) -> dict[str, str]:
        """Set the first count seats to human and return generated links."""
        assert runtime is not None, "runtime 不能为空"
        assert count >= 0, "count 不能为负数"
        seat_ids = list(runtime.session.seats.keys())
        assert count <= len(seat_ids), "count 不能超过 seat 数量"
        links = {}
        for index, seat_id in enumerate(seat_ids):
            controller = CONTROLLER_HUMAN if index < count else CONTROLLER_AI
            link = self.set_controller(runtime, seat_id, controller, token_service)
            if link:
                links[seat_id] = link
        return links

    def reset_join_link(
        self,
        runtime: "PartySessionRuntime",
        seat_id: str,
        token_service: PlayerTokenService,
    ) -> str:
        """Reset one human seat join link."""
        assert runtime is not None, "runtime 不能为空"
        assert seat_id in runtime.session.seats, f"seat 不存在: {seat_id}"
        seat = runtime.session.seats[seat_id]
        assert seat.controller_type == CONTROLLER_HUMAN, "只有 human seat 可以重置链接"
        token = token_service.reset_token(runtime.session.session_id, seat_id)
        link = f"/player?token={token}"
        runtime.player_links[seat_id] = link
        runtime.event_store.append_host({
            "kind": "seat_link_reset",
            "seat_id": seat_id,
            "join_link": link,
        })
        return link

__all__ = ["ServiceSessionControls"]
