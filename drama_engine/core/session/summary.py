"""Runtime summary provider."""

from __future__ import annotations

from typing import Any

class SummaryProvider:
    """Build runtime summaries and delegate runner-specific details."""

    def session_summary(self, runtime: Any) -> dict[str, Any]:
        """Return common session summary."""
        data = runtime.session.to_summary()
        data["player_links"] = dict(runtime.player_links)
        data["step_gate"] = runtime.step_gate.status()
        runtime_state = getattr(runtime, "runtime_state", None)
        if runtime_state is not None:
            data["runtime_state"] = {
                "phase": runtime_state.phase,
                "has_task": runtime_state.task is not None,
                "metadata": dict(runtime_state.metadata or {}),
            }
        runner = getattr(runtime, "runner", None)
        if runner is not None:
            data["runner"] = runner.status()
        return data

    def seat_summary(self, runtime: Any) -> list[dict[str, Any]]:
        """Return seats with service join link information."""
        result = []
        for seat in runtime.session.seats.values():
            item = seat.to_dict()
            item["join_link"] = runtime.player_links.get(seat.seat_id, "")
            item["claim_status"] = "claimed" if seat.claimed_by else "unclaimed"
            result.append(item)
        return result
