"""Reusable actor and casting components for runtime sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

from drama_engine.core.diagnostics.debug import MockActor
from drama_engine.core.engine import (
    Cast,
    Role,
    ServiceHumanInputPort,
    create_agent_actor,
    create_human_actor_from_port,
)

if TYPE_CHECKING:
    from drama_engine.core.ports.actions import RuntimeActionServiceRouter


@dataclass(frozen=True, slots=True)
class PlayerSeat:
    """Resolved runtime player seat."""

    seat_id: str
    display_name: str = ""
    nickname: str = ""
    initial_attrs: dict[str, Any] = field(default_factory=dict)


class PlayerResolver:
    """Resolve player seats from DSL player config and service session state."""

    def resolve(self, script: Any, session_state: Any) -> list[PlayerSeat]:
        """Return ordered player seats for the current runtime session."""
        assert session_state is not None, "session_state 不能为空"
        player_config = getattr(script, "player_config", None) if script is not None else None
        seat_ids = self._seat_ids(player_config=player_config, session_state=session_state)
        assert seat_ids, "player seats 不能为空"
        result = []
        for seat_id in seat_ids:
            result.append(PlayerSeat(
                seat_id=seat_id,
                display_name=self._display_name(player_config, seat_id),
                nickname=self._nickname(player_config, seat_id),
                initial_attrs=self._initial_attrs(player_config),
            ))
        return result

    def _seat_ids(self, player_config: Any, session_state: Any) -> list[str]:
        """Resolve seat ids from script player config or service session."""
        if player_config is not None and getattr(player_config, "ids", None):
            return [str(item) for item in player_config.ids]
        return [str(item) for item in getattr(session_state, "seat_ids", [])]

    def _display_name(self, player_config: Any, seat_id: str) -> str:
        """Return display name for one seat."""
        display_names = getattr(player_config, "display_names", {}) if player_config is not None else {}
        if isinstance(display_names, dict):
            return str(display_names.get(seat_id) or seat_id)
        return seat_id

    def _nickname(self, player_config: Any, seat_id: str) -> str:
        """Return nickname for one seat."""
        nicknames = getattr(player_config, "nicknames", {}) if player_config is not None else {}
        if isinstance(nicknames, dict):
            return str(nicknames.get(seat_id) or "")
        return ""

    def _initial_attrs(self, player_config: Any) -> dict[str, Any]:
        """Return default initial attrs for resolved seats."""
        initial_attrs = getattr(player_config, "initial_attrs", {}) if player_config is not None else {}
        return dict(initial_attrs or {})


class SeatRegistry:
    """Session-level registry of resolved player seats."""

    def __init__(self) -> None:
        self._seats: dict[str, PlayerSeat] = {}

    def replace(self, seats: list[PlayerSeat]) -> None:
        """Replace the registry with the seats resolved for one runner."""
        assert seats, "seats 不能为空"
        self._seats = {}
        for seat in seats:
            self.add(seat)

    def add(self, seat: PlayerSeat) -> None:
        """Register one resolved player seat."""
        assert isinstance(seat, PlayerSeat), "seat 必须是 PlayerSeat"
        assert seat.seat_id, "seat_id 不能为空"
        self._seats[seat.seat_id] = seat

    def get(self, seat_id: str) -> PlayerSeat:
        """Return one resolved seat by id."""
        assert seat_id in self._seats, f"未知 seat: {seat_id}"
        return self._seats[seat_id]

    def all(self) -> list[PlayerSeat]:
        """Return all resolved seats in insertion order."""
        return list(self._seats.values())

    def clear(self) -> None:
        """Clear all resolved seats."""
        self._seats.clear()


class RoleCatalog:
    """Index DSL roles for casting and profile creation."""

    def __init__(self, roles: list[Role] | None = None) -> None:
        self._roles: dict[str, Role] = {}
        for role in roles or []:
            self.add(role)

    def add(self, role: Role) -> None:
        """Add one role definition to the catalog."""
        assert role.name, "role.name 不能为空"
        self._roles[role.name] = role

    def get(self, role_name: str) -> Role:
        """Return one role definition by name."""
        assert role_name in self._roles, f"未知角色: {role_name}"
        return self._roles[role_name]

    def all(self) -> list[Role]:
        """Return all role definitions in declaration order."""
        return list(self._roles.values())


class CastingPolicy(Protocol):
    """Policy protocol for mapping seats to roles."""

    def deal(self, actor_names: list[str], roles: list[Role]) -> list[tuple[str, Role]]:
        """Return actor-role assignment."""


class ScriptCastingPolicy:
    """Adapter that uses the casting policy declared by a compiled Script."""

    def __init__(self, script_casting: Any) -> None:
        assert script_casting is not None, "script_casting 不能为空"
        self.script_casting = script_casting

    def deal(self, actor_names: list[str], roles: list[Role]) -> list[tuple[str, Role]]:
        """Delegate assignment to the compiled script casting object."""
        return list(self.script_casting.deal(actor_names, roles))


class ActorProfilePublisher:
    """Publish stable actor profiles and keep a runtime profile registry."""

    def __init__(self, profiles: dict[str, Any] | None = None) -> None:
        self.profiles = profiles if profiles is not None else {}

    def publish(self, actor: Any, role: Role, profile: Any) -> Any:
        """Attach one profile to an actor and remember it by actor name."""
        assert actor is not None, "actor 不能为空"
        assert role is not None, "role 不能为空"
        assert profile is not None, "profile 不能为空"
        actor_name = getattr(profile, "actor_name", "") or getattr(actor, "name", "")
        assert actor_name, "actor_name 不能为空"
        self.profiles[actor_name] = profile
        if hasattr(actor, "set_actor_profile"):
            actor.set_actor_profile(profile)
        elif hasattr(actor, "set_role_snapshot"):
            actor.set_role_snapshot(role)
        return profile


@dataclass(frozen=True, slots=True)
class ActorFactoryRequest:
    """Input needed to create one runtime actor."""

    seat: PlayerSeat
    human_seat_ids: set[str]
    action_service: RuntimeActionServiceRouter
    tracer: Any = None
    dry_run: bool = True
    adapter_resolver: Callable[[], Any] | None = None
    step_gate: Any = None


class ActorFactory:
    """Create human, mock, or AI actors from resolved runtime seats."""

    def create(self, request: ActorFactoryRequest) -> Any:
        """Create one actor controller for a resolved seat."""
        assert request.seat.seat_id, "seat_id 不能为空"
        if request.seat.seat_id in request.human_seat_ids:
            actor = self._create_human(request)
        elif request.dry_run:
            actor = self._create_mock(request)
        else:
            actor = self._create_agent(request)
        self._apply_player_profile(actor, request.seat)
        return actor

    def _create_human(self, request: ActorFactoryRequest) -> Any:
        """Create a human actor backed by the service input port."""
        port = ServiceHumanInputPort(
            service=request.action_service,
            seat_id=request.seat.seat_id,
            tracer=request.tracer,
        )
        return create_human_actor_from_port(name=request.seat.seat_id, input_port=port)

    def _create_mock(self, request: ActorFactoryRequest) -> MockActor:
        """Create a dry-run mock actor."""
        actor = MockActor(name=request.seat.seat_id)
        actor._tracer = request.tracer
        return actor

    def _create_agent(self, request: ActorFactoryRequest) -> Any:
        """Create a real AI actor."""
        adapter = request.adapter_resolver() if request.adapter_resolver is not None else None
        system = (
            "你正在参加一局游戏，是其中一名玩家。"
            "请严格按你被私下告知的身份与目标行动。"
            "只根据你能看到的信息推理，发言简洁、自然、有策略。"
        )
        return create_agent_actor(
            name=request.seat.seat_id,
            system_prompt=system,
            adapter=adapter,
            tracer=request.tracer,
            gate=request.step_gate,
        )

    def _apply_player_profile(self, actor: Any, seat: PlayerSeat) -> None:
        """Attach stable player display profile to the actor when supported."""
        if hasattr(actor, "set_player_profile"):
            actor.set_player_profile(
                player_id=seat.seat_id,
                display_name=seat.display_name or seat.seat_id,
                nickname=seat.nickname,
            )


def build_cast_from_seats(
    seats: list[PlayerSeat],
    actor_factory: ActorFactory,
    human_seat_ids: set[str],
    action_service: RuntimeActionServiceRouter,
    tracer: Any = None,
    dry_run: bool = True,
    adapter_resolver: Callable[[], Any] | None = None,
    step_gate: Any = None,
) -> Cast:
    """Build a Cast from resolved seats through the actor factory."""
    assert seats, "seats 不能为空"
    assert actor_factory is not None, "actor_factory 不能为空"
    cast = Cast()
    for seat in seats:
        actor = actor_factory.create(ActorFactoryRequest(
            seat=seat,
            human_seat_ids=human_seat_ids,
            action_service=action_service,
            tracer=tracer,
            dry_run=dry_run,
            adapter_resolver=adapter_resolver,
            step_gate=step_gate,
        ))
        cast.add(actor)
    return cast
