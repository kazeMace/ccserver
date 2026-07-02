"""Actor runtime component tests."""

from types import SimpleNamespace

from drama_engine.core.engine import FixedDeal, PlayerConfig, Role, Stage, State, Vocabulary
from drama_engine.core.actors import (
    ActorProfilePublisher,
    ActorRuntime,
    PlayerResolver,
    RoleCatalog,
    ScriptCastingPolicy,
    SeatRegistry,
)


def test_player_resolver_prefers_script_player_config():
    """PlayerResolver 应优先使用 DSL player_config。"""
    resolver = PlayerResolver()
    script = SimpleNamespace(
        player_config=PlayerConfig(
            count=2,
            ids=["Alice", "Bob"],
            display_names={"Alice": "艾丽丝"},
            nicknames={"Bob": "小鲍"},
            initial_attrs={"alive": True},
        )
    )
    session_state = SimpleNamespace(seat_ids=["Player_1", "Player_2"])

    seats = resolver.resolve(script=script, session_state=session_state)

    assert [seat.seat_id for seat in seats] == ["Alice", "Bob"]
    assert seats[0].display_name == "艾丽丝"
    assert seats[1].nickname == "小鲍"
    assert seats[0].initial_attrs == {"alive": True}


def test_role_catalog_indexes_roles_by_name():
    """RoleCatalog 应按 role.name 建立索引。"""
    guard = Role(
        name="guard",
        brief="守卫",
        scopes=[],
        abilities=[],
        faction="good",
        display_name="守卫",
    )
    catalog = RoleCatalog([guard])

    assert catalog.get("guard") is guard
    assert catalog.all() == [guard]


def test_script_casting_policy_delegates_to_script_casting():
    """ScriptCastingPolicy 应复用 DSL 编译后的 casting 策略。"""
    guard = Role(
        name="guard",
        brief="守卫",
        scopes=[],
        abilities=[],
        faction="good",
        display_name="守卫",
    )
    policy = ScriptCastingPolicy(FixedDeal({"Alice": "guard"}))

    assignment = policy.deal(["Alice"], [guard])

    assert assignment == [("Alice", guard)]


def test_actor_runtime_creates_cast_from_script_components():
    """ActorRuntime 应通过 resolver/factory 创建 Cast，并保存 casting 组件。"""
    role = Role(
        name="guard",
        brief="守卫",
        scopes=[],
        abilities=[],
        faction="good",
        display_name="守卫",
    )
    script = SimpleNamespace(
        player_config=PlayerConfig(
            count=1,
            ids=["Alice"],
            display_names={"Alice": "艾丽丝"},
            nicknames={"Alice": "小艾"},
            initial_attrs={},
        ),
        roles=[role],
        casting=FixedDeal({"Alice": "guard"}),
    )
    session_state = SimpleNamespace(seat_ids=["Player_1"])
    runtime = SimpleNamespace()
    actor_runtime = ActorRuntime(runtime=runtime)

    cast = actor_runtime.create_cast_for_script(
        script=script,
        session_state=session_state,
        human_seat_ids=set(),
        action_service=SimpleNamespace(),
        dry_run=True,
    )

    actor = cast.get("Alice")
    assert actor.display_name == "艾丽丝"
    assert actor.nickname == "小艾"
    assert actor_runtime.cast is cast
    assert isinstance(actor_runtime.seat_registry, SeatRegistry)
    assert actor_runtime.seat_registry.get("Alice").display_name == "艾丽丝"
    assert actor_runtime.seat_registry.get("Alice").nickname == "小艾"
    assert actor_runtime.role_catalog is not None
    assert actor_runtime.role_catalog.get("guard") is role
    assert actor_runtime.casting_policy is not None
    assert actor_runtime.casting_policy.deal(["Alice"], [role]) == [("Alice", role)]


def test_actor_runtime_publishes_profiles_through_runtime_registry():
    """ActorRuntime 应通过 ActorProfilePublisher 记录并发布稳定身份档案。"""
    role = Role(
        name="guard",
        brief="守卫说明",
        scopes=[],
        abilities=[],
        faction="good",
        display_name="守卫",
    )
    vocab = Vocabulary(
        roles=frozenset({"guard"}),
        factions=frozenset({"good"}),
        scopes=frozenset(),
        abilities=frozenset(),
    )
    script = SimpleNamespace(
        player_config=PlayerConfig(
            count=1,
            ids=["Alice"],
            display_names={"Alice": "艾丽丝"},
            nicknames={"Alice": "小艾"},
            initial_attrs={"alive": True},
        ),
        roles=[role],
        casting=FixedDeal({"Alice": "guard"}),
        vocab=vocab,
        concepts={},
    )
    actor_runtime = ActorRuntime(runtime=SimpleNamespace())
    cast = actor_runtime.create_cast_for_script(
        script=script,
        session_state=SimpleNamespace(seat_ids=["Alice"]),
        human_seat_ids=set(),
        action_service=SimpleNamespace(),
        dry_run=True,
    )
    stage = Stage(scopes=[], cast=cast)
    state = State(vocab)
    casting_service = actor_runtime.create_casting_service(script=script, stage=stage)

    assignment = casting_service.assign(state)

    actor = cast.get("Alice")
    assert isinstance(actor_runtime.profile_publisher, ActorProfilePublisher)
    assert assignment == [("Alice", role)]
    assert actor_runtime.profiles["Alice"].role_name == "guard"
    assert actor.actor_profile is actor_runtime.profiles["Alice"]
    assert actor_runtime.profiles["Alice"].display_name == "艾丽丝"
