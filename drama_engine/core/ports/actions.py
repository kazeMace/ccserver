"""Action request ports for runtime and execution models."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

@runtime_checkable
class RuntimeActionPort(Protocol):
    """Common action port protocol used by runtime action routing."""

    def pending_summary(self) -> list[dict[str, Any]]:
        """Return pending action summaries."""

    def current_action(self, seat_id: str) -> dict[str, Any] | None:
        """Return current action for one seat."""

    def current_request_object(self, seat_id: str) -> Any | None:
        """Return the raw current request object for one seat."""

    async def submit_current(
        self,
        runtime: Any,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None,
        text: str,
    ) -> Any | None:
        """Submit the current action for one seat."""

    def cancel_all(self) -> None:
        """Cancel all pending actions owned by this port."""


class ServiceActionPort(RuntimeActionPort):
    """Adapter for the lightweight service action facade."""

    def __init__(self, service_action: Any) -> None:
        assert service_action is not None, "service_action 不能为空"
        self._service_action = service_action

    def pending_summary(self) -> list[dict[str, Any]]:
        """Return pending action summaries."""
        if hasattr(self._service_action, "pending_summary"):
            return list(self._service_action.pending_summary())
        return []

    def current_action(self, seat_id: str) -> dict[str, Any] | None:
        """Return current action for one seat."""
        assert seat_id, "seat_id 不能为空"
        request = self.current_request_object(seat_id)
        return _request_to_dict(request) if request is not None else None

    def current_request_object(self, seat_id: str) -> Any | None:
        """Return the raw current request object for one seat."""
        assert seat_id, "seat_id 不能为空"
        return self._service_action.get_current_request(seat_id)

    async def submit_current(
        self,
        runtime: Any,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None,
        text: str,
    ) -> Any | None:
        """Submit the current action for one seat."""
        _ = runtime
        return await self._service_action.submit(
            seat_id=seat_id,
            source=source,
            data=data,
            text=text,
        )

    def cancel_all(self) -> None:
        """Cancel all pending service actions."""
        if hasattr(self._service_action, "cancel_all"):
            self._service_action.cancel_all()


class RuntimeActionServiceRouter:
    """Runtime-facing action service that routes to service or runner storage."""

    def __init__(self, runtime: Any, service_action: Any) -> None:
        assert runtime is not None, "runtime 不能为空"
        assert service_action is not None, "service_action 不能为空"
        self._runtime = runtime
        self._service_action = service_action
        self._service_port = ServiceActionPort(service_action)
        self.session_id = service_action.session_id

    @property
    def service_action(self) -> Any:
        """Return the service-owned lightweight action facade."""
        return self._service_action

    def create_request(
        self,
        seat_id: str,
        cue: str,
        kind: str = "generic",
        candidates: list[str] | None = None,
        schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        scene_name: str = "",
        scene_display_name: str = "",
        allow_resubmit: bool = False,
        timeout_seconds: float | None = None,
        collect_model: Any = None,
    ) -> Any:
        """Create an action request through the active runtime action owner."""
        request_metadata = dict(metadata or {})
        if scene_name:
            request_metadata["scene_name"] = scene_name
        if scene_display_name:
            request_metadata["scene_display_name"] = scene_display_name
        if allow_resubmit:
            request_metadata["allow_resubmit"] = allow_resubmit
        if timeout_seconds is not None:
            request_metadata["timeout_seconds"] = timeout_seconds
        if collect_model is not None:
            request_metadata["collect_model"] = collect_model
        return self._service_action.create_request(
            seat_id=seat_id,
            cue=cue,
            kind=kind,
            candidates=candidates,
            schema=schema,
            metadata=request_metadata,
        )

    def get_current_request(self, seat_id: str) -> Any | None:
        """Return the current request from the active action owner."""
        assert seat_id, "seat_id 不能为空"
        port = self._active_port()
        if hasattr(port, "current_request_object"):
            return port.current_request_object(seat_id)
        return port.current_action(seat_id)

    def current_action(self, seat_id: str) -> dict[str, Any] | None:
        """Return the current request normalized for API/UI use."""
        assert seat_id, "seat_id 不能为空"
        return self._active_port().current_action(seat_id)

    def pending_summary(self) -> list[dict[str, Any]]:
        """Return pending action summaries from the active action owner."""
        return self._active_port().pending_summary()

    async def submit(
        self,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None = None,
        text: str = "",
    ) -> Any | None:
        """Submit the current request through the active action owner."""
        assert seat_id, "seat_id 不能为空"
        assert source, "source 不能为空"
        return await self._active_port().submit_current(
            runtime=self._runtime,
            seat_id=seat_id,
            source=source,
            data=data,
            text=text,
        )

    async def submit_current(
        self,
        runtime: Any,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None,
        text: str,
    ) -> Any | None:
        """Action-port compatible submit entry."""
        _ = runtime
        return await self.submit(seat_id=seat_id, source=source, data=data, text=text)

    async def wait_submission(self, request_id: str) -> Any:
        """Wait for an action submission through the owning action service."""
        assert request_id, "request_id 不能为空"
        return await self._service_action.wait_submission(request_id)

    def emit_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """Publish one private event through the runtime event sink."""
        assert seat_id, "seat_id 不能为空"
        assert isinstance(event, dict), "event 必须是 dict"
        self._runtime.event_store.append_private(seat_id, dict(event))

    def cancel_all(self) -> None:
        """Cancel pending service actions."""
        self._service_port.cancel_all()

    def _active_port(self) -> RuntimeActionPort:
        """Return the current action owner port."""
        runner_port = self._runner_action_port()
        if runner_port is not None:
            return runner_port
        return self._service_port

    def _runner_action_port(self) -> RuntimeActionPort | None:
        """Return the runner action port if the current runner owns actions."""
        runner = getattr(self._runtime, "runner", None)
        if runner is None or not hasattr(runner, "action_port"):
            return None
        port = runner.action_port()
        if port is None:
            return None
        assert isinstance(port, RuntimeActionPort), "runner.action_port 必须实现 RuntimeActionPort"
        return port


class RuntimeActionView:
    """Unified pending-action facade for service and runner action services."""

    def __init__(self, service_action: Any) -> None:
        assert service_action is not None, "service_action 不能为空"
        self._service_port = ServiceActionPort(service_action)

    def pending_summary(self, runtime: Any) -> list[dict[str, Any]]:
        """Return pending action summary from runner service or service facade."""
        return self._action_port(runtime).pending_summary()

    def current_action(self, runtime: Any, seat_id: str) -> dict[str, Any] | None:
        """Return current action request for one seat."""
        assert seat_id, "seat_id 不能为空"
        return self._action_port(runtime).current_action(seat_id)

    async def submit_current(
        self,
        runtime: Any,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None,
        text: str,
    ) -> Any | None:
        """Submit current pending action for one seat."""
        assert runtime is not None, "runtime 不能为空"
        assert seat_id, "seat_id 不能为空"
        assert source, "source 不能为空"
        return await self._action_port(runtime).submit_current(runtime, seat_id, source, data, text)

    def _action_port(self, runtime: Any) -> RuntimeActionPort:
        """Return the active action port for the current runtime."""
        runner = getattr(runtime, "runner", None)
        if runner is not None and hasattr(runner, "action_port"):
            port = runner.action_port()
            if port is not None:
                assert isinstance(port, RuntimeActionPort), "runner.action_port 必须实现 RuntimeActionPort"
                return port
        return self._service_port


def _request_to_dict(request: Any) -> dict[str, Any]:
    """Normalize action request objects."""
    metadata = getattr(request, "metadata", None)
    return {
        "request_id": getattr(request, "request_id", ""),
        "seat_id": getattr(request, "seat_id", ""),
        "type": getattr(request, "kind", ""),
        "kind": getattr(request, "kind", ""),
        "cue": getattr(request, "cue", ""),
        "schema": getattr(request, "schema", None),
        "candidates": getattr(request, "candidates", None),
        "metadata": dict(metadata or {}) if isinstance(metadata, dict) else {},
        "scene_name": getattr(request, "scene_name", ""),
        "scene_display_name": getattr(request, "scene_display_name", ""),
        "allow_resubmit": bool(getattr(request, "allow_resubmit", False)),
        "timeout_seconds": getattr(request, "timeout_seconds", None),
        "deadline_at": getattr(request, "deadline_at", None),
    }
