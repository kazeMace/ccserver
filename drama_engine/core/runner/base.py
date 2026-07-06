"""Base runner and runner context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from drama_engine.core.ports.actions import RuntimeActionPort
from drama_engine.core.ports.events import EventPublisher
from drama_engine.core.ports.input import InputBridge
from drama_engine.core.ports.memory import RuntimeMemoryBackend, RuntimeMemoryStore
from drama_engine.core.runner.config import RuntimeConfigParser
from drama_engine.core.runtime_spec.registry import RuntimeSpec
from drama_engine.core.session.lifecycle import RuntimeLifecycleHooks, RuntimeState
from drama_engine.core.session.summary import SummaryProvider

@dataclass(slots=True)
class RunnerContext:
    """Shared dependencies provided by GameRuntime to runners."""

    runtime: Any
    declaration: RuntimeSpec
    config: dict[str, Any]
    event_publisher: EventPublisher
    input_bridge: Any
    actor_runtime: Any
    step_gate: Any
    config_parser: RuntimeConfigParser
    view_projector: Any = None
    memory_store: Any = None
    summary_provider: SummaryProvider | None = None
    lifecycle_hooks: RuntimeLifecycleHooks | None = None
    task: Any = None
    memory_backend: RuntimeMemoryBackend | None = None


class BasicGameRunner:
    """Base class for all game runner implementations."""

    def __init__(
        self,
        runtime: Any,
        declaration: RuntimeSpec | None = None,
        dry_run: bool = True,
        context: RunnerContext | None = None,
    ) -> None:
        assert runtime is not None, "runtime 不能为空"
        self.runtime = runtime
        self.declaration = declaration or RuntimeSpec(type="game_session")
        self.dry_run = dry_run
        self.context = context or build_runner_context(
            runtime=runtime,
            declaration=self.declaration,
        )

    @property
    def service(self) -> Any:
        """Return service ports exposed by the runtime container."""
        service = getattr(self.runtime, "service", None)
        assert service is not None, "runtime.service 不能为空"
        return service

    @property
    def session_state(self) -> Any:
        """Return service-owned session state."""
        return self.service.session_state

    @property
    def runtime_state(self) -> RuntimeState:
        """Return runtime-owned transient execution state."""
        runtime_state = getattr(self.runtime, "runtime_state", None)
        assert runtime_state is not None, "runtime.runtime_state 不能为空"
        return runtime_state

    @property
    def event_publisher(self) -> EventPublisher:
        """Return shared event publisher."""
        assert self.context.event_publisher is not None, "event_publisher 不能为空"
        return self.context.event_publisher

    @property
    def step_gate(self) -> Any:
        """Return shared step gate."""
        return self.context.step_gate

    @property
    def input_bridge(self) -> InputBridge:
        """Return shared input bridge."""
        assert self.context.input_bridge is not None, "input_bridge 不能为空"
        return self.context.input_bridge

    @property
    def config_parser(self) -> RuntimeConfigParser:
        """Return shared runtime config parser."""
        assert self.context.config_parser is not None, "config_parser 不能为空"
        return self.context.config_parser

    async def assign(self) -> None:
        """Prepare the runner before start."""
        raise NotImplementedError

    async def start(self) -> None:
        """Start runner execution."""
        raise NotImplementedError

    async def pause(self) -> None:
        """Pause runner execution if the runner has active work."""
        return None

    async def resume(self) -> None:
        """Resume runner execution if the runner has active work."""
        return None

    async def step(self, count: int = 1) -> None:
        """Advance a step gate if available."""
        gate = getattr(self.context, "step_gate", None)
        if gate is not None and hasattr(gate, "step"):
            await gate.step(count=count)

    async def reset_runtime_state(self) -> None:
        """Reset transient runner state."""
        return None

    async def terminate(self, reason: str = "terminated") -> None:
        """Terminate runner execution."""
        _ = reason
        await self.reset_runtime_state()

    def status(self) -> dict[str, Any]:
        """Return runner status."""
        session = self.session_state
        return {
            "runtime_type": self.declaration.type,
            "session_status": getattr(session, "status", ""),
        }

    def summary(self, audience: str, seat_id: str | None = None) -> dict[str, Any]:
        """Return a runner-specific summary."""
        return {
            "audience": audience,
            "seat_id": seat_id,
            "runner": self.__class__.__name__,
            "status": self.status(),
        }

    def current_action(self, seat_id: str) -> dict[str, Any] | None:
        """Return the current action for one seat if available."""
        _ = seat_id
        return None

    def action_port(self) -> RuntimeActionPort | None:
        """Return a runner-owned action port, if this runner has one."""
        return None


def build_runner_context(
    runtime: Any,
    declaration: RuntimeSpec,
    config: dict[str, Any] | None = None,
) -> RunnerContext:
    """Build a default RunnerContext from the current runtime container."""
    assert runtime is not None, "runtime 不能为空"
    service = getattr(runtime, "service", None)
    event_store = getattr(service, "event_sink", None) if service is not None else getattr(runtime, "event_store", None)
    event_publisher = getattr(runtime, "event_publisher", None)
    if event_publisher is None and event_store is not None:
        event_publisher = EventPublisher(event_store)
        setattr(runtime, "event_publisher", event_publisher)
    actor_runtime = getattr(runtime, "actor_runtime", None)
    if actor_runtime is None:
        from drama_engine.core.actors import ActorRuntime

        actor_runtime = ActorRuntime(runtime=runtime)
        setattr(runtime, "actor_runtime", actor_runtime)
    input_bridge = getattr(runtime, "input_bridge", None)
    if input_bridge is None:
        input_bridge = InputBridge()
        setattr(runtime, "input_bridge", input_bridge)
    config_parser = getattr(runtime, "runtime_config_parser", None)
    if config_parser is None:
        config_parser = RuntimeConfigParser()
        setattr(runtime, "runtime_config_parser", config_parser)
    memory_store = getattr(runtime, "memory_store", None)
    if memory_store is None:
        memory_store = RuntimeMemoryStore()
        setattr(runtime, "memory_store", memory_store)
    summary_provider = getattr(runtime, "summary_provider", None)
    lifecycle_hooks = getattr(runtime, "lifecycle_hooks", None)
    return RunnerContext(
        runtime=runtime,
        declaration=declaration,
        config=dict(config or declaration.config),
        event_publisher=event_publisher,
        input_bridge=input_bridge,
        actor_runtime=actor_runtime,
        step_gate=getattr(runtime, "step_gate", None),
        config_parser=config_parser,
        memory_store=memory_store,
        memory_backend=getattr(memory_store, "backend", None),
        summary_provider=summary_provider,
        lifecycle_hooks=lifecycle_hooks,
    )
