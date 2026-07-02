"""Actor runtime and cast creation utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from drama_engine.core.engine import Cast, CastingService
from drama_engine.core.actors.components import (
    ActorFactory,
    ActorProfilePublisher,
    PlayerResolver,
    SeatRegistry,
    RoleCatalog,
    ScriptCastingPolicy,
    build_cast_from_seats,
)

if TYPE_CHECKING:
    from drama_engine.core.ports.actions import RuntimeActionServiceRouter


@dataclass(slots=True)
class ActorRuntime:
    """Session-level actor/cast runtime shared by all runner types."""

    runtime: Any
    cast: Cast | None = None
    casting_service: CastingService | None = None
    profiles: dict[str, Any] = field(default_factory=dict)
    player_resolver: PlayerResolver = field(default_factory=PlayerResolver)
    seat_registry: SeatRegistry = field(default_factory=SeatRegistry)
    actor_factory: ActorFactory = field(default_factory=ActorFactory)
    profile_publisher: ActorProfilePublisher | None = None
    role_catalog: RoleCatalog | None = None
    casting_policy: Any = None

    def __post_init__(self) -> None:
        """Ensure profile publishing writes into this runtime registry."""
        if self.profile_publisher is None:
            self.profile_publisher = ActorProfilePublisher(self.profiles)

    def reset(self) -> None:
        """Drop transient actor runtime state."""
        self.cast = None
        self.casting_service = None
        self.seat_registry.clear()
        self.role_catalog = None
        self.casting_policy = None
        self.profiles.clear()

    def create_cast(
        self,
        player_names: list[str],
        human_seat_ids: set[str],
        action_service: RuntimeActionServiceRouter,
        tracer: Any = None,
        dry_run: bool = True,
        adapter_resolver: Callable[[], Any] | None = None,
        step_gate: Any = None,
    ) -> Cast:
        """Create a Cast for one runner execution.

        ActorRuntime owns the actor registry. Runners request a cast through this
        method instead of directly constructing human/agent/mock actors.
        """
        assert player_names, "player_names 不能为空"
        seats = self.player_resolver.resolve(
            script=None,
            session_state=_ResolvedSeatState(player_names),
        )
        self.seat_registry.replace(seats)
        cast = build_cast_from_seats(
            seats=seats,
            actor_factory=self.actor_factory,
            human_seat_ids=human_seat_ids,
            action_service=action_service,
            tracer=tracer,
            dry_run=dry_run,
            adapter_resolver=adapter_resolver,
            step_gate=step_gate,
        )
        self.cast = cast
        return cast

    def create_cast_for_script(
        self,
        script: Any,
        session_state: Any,
        human_seat_ids: set[str],
        action_service: RuntimeActionServiceRouter,
        tracer: Any = None,
        dry_run: bool = True,
        adapter_resolver: Callable[[], Any] | None = None,
        step_gate: Any = None,
    ) -> Cast:
        """Create a Cast from script player config and service session state."""
        assert script is not None, "script 不能为空"
        assert session_state is not None, "session_state 不能为空"
        seats = self.player_resolver.resolve(script=script, session_state=session_state)
        self.seat_registry.replace(seats)
        cast = build_cast_from_seats(
            seats=seats,
            actor_factory=self.actor_factory,
            human_seat_ids=human_seat_ids,
            action_service=action_service,
            tracer=tracer,
            dry_run=dry_run,
            adapter_resolver=adapter_resolver,
            step_gate=step_gate,
        )
        self.cast = cast
        self.role_catalog = RoleCatalog(getattr(script, "roles", []))
        self.casting_policy = ScriptCastingPolicy(getattr(script, "casting", None))
        return cast

    def create_casting_service(self, script: Any, stage: Any) -> CastingService:
        """Create a CastingService bound to the current session cast."""
        assert self.cast is not None, "create_casting_service 前必须先 create_cast"
        self.role_catalog = RoleCatalog(getattr(script, "roles", []))
        self.casting_policy = ScriptCastingPolicy(getattr(script, "casting", None))
        self.casting_service = CastingService(
            script=script,
            stage=stage,
            cast=self.cast,
            profile_publisher=self.profile_publisher,
        )
        return self.casting_service


class _ResolvedSeatState:
    """Minimal session-state adapter for resolved player names."""

    def __init__(self, seat_ids: list[str]) -> None:
        self.seat_ids = list(seat_ids)
