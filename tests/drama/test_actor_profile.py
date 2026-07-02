import pytest

from drama_engine.core.engine import (
    ActorProfile,
    Cast,
    Director,
    FixedDeal,
    Narrator,
    Narration,
    PerceptionFormatter,
    Role,
    Scene,
    Sequence,
    SetAttr,
    Script,
    Scope,
    Stage,
    State,
    Vocabulary,
)


class EmptyFlow:
    """测试用空流程：只跑开场，不执行任何场景。"""

    loop = False

    def next_scenes(self, state):
        return []


class ProfileRecorderActor:
    """记录 Director 是否通过 profile 而不是 perceive 注入身份。"""

    def __init__(self, name):
        self.name = name
        self.display_name = name
        self.nickname = ""
        self.profile = None
        self.perceived = []

    def set_actor_profile(self, profile):
        self.profile = profile

    async def perceive(self, msg):
        self.perceived.append(msg)


def test_profile_formatter_places_identity_before_observations():
    profile = ActorProfile(
        actor_name="Player_12",
        display_name="Player_12",
        nickname="",
        role_name="guard",
        role_display_name="守卫",
        faction="good",
        brief="每晚可以守护一名玩家。",
        role_context="【概念说明】\n- 守护(protect)：保护目标。",
    )
    formatter = PerceptionFormatter()

    message = formatter.format(
        [{"scope": "town", "sender": "主持人", "text": "天黑了。"}],
        "请选择今晚要守护的玩家。",
        profile=profile,
    )

    assert message.index("【身份档案】") < message.index("【场上动态】")
    assert "你是 Player_12。" in message
    assert "当你说“我”“自己”“本玩家”时，指的是 Player_12。" in message
    assert "你的角色是【守卫】。" in message
    assert "(town) 主持人：天黑了。" in message
    assert "请选择今晚要守护的玩家。" in message


@pytest.mark.asyncio
async def test_director_sets_actor_profile_without_private_identity_perceive():
    actor = ProfileRecorderActor("Player_12")
    cast = Cast()
    cast.add(actor)

    role = Role(
        name="guard",
        display_name="守卫",
        faction="good",
        brief="你是【守卫】。",
        scopes=[],
        abilities=["protect"],
    )
    vocab = Vocabulary(
        roles=frozenset({"guard"}),
        factions=frozenset({"good"}),
        scopes=frozenset(),
        abilities=frozenset({"protect"}),
    )
    script = Script(
        vocab=vocab,
        roles=[role],
        casting=FixedDeal({"Player_12": "guard"}),
        scopes=[],
        flow=EmptyFlow(),
        referee=lambda state: None,
        concepts={
            "roles": {
                "guard": {
                    "display_name": "守卫",
                    "description": "夜晚守护一名玩家。",
                }
            },
            "abilities": {
                "protect": {
                    "display_name": "守护",
                    "description": "保护目标不被狼人袭击。",
                }
            },
        },
    )
    state = State(vocab)
    state.register_entity("GAME", {})
    stage = Stage(scopes=[], cast=cast)
    narrator = Narrator(stage=stage)
    director = Director(script=script, stage=stage, narrator=narrator, cast=cast)

    result = await director.run(state)

    assert result == "剧目正常结束"
    assert actor.profile is not None
    assert actor.profile.actor_name == "Player_12"
    assert actor.profile.role_name == "guard"
    assert "【概念说明】" in actor.profile.role_context
    assert actor.perceived == []


@pytest.mark.asyncio
async def test_narration_effects_run_before_cue_is_published():
    """Narration 结算幕应先改 State，再按新 State 生成公告。"""
    events = []

    class EventRecorderActor(ProfileRecorderActor):
        async def perceive(self, msg):
            events.append(("message", msg["text"]))
            await super().perceive(msg)

    actor = EventRecorderActor("Player_1")
    cast = Cast()
    cast.add(actor)

    role = Role(
        name="villager",
        display_name="村民",
        faction="good",
        brief="你是【村民】。",
        scopes=["town"],
        abilities=[],
    )
    vocab = Vocabulary(
        roles=frozenset({"villager"}),
        factions=frozenset({"good"}),
        scopes=frozenset({"town"}),
        abilities=frozenset(),
    )

    def publish_new_value(responses, state, writer):
        writer.apply(SetAttr("GAME", "notice", "new"))

    scene = Scene(
        name="stateful-narration",
        scope="town",
        participants=lambda state: set(),
        cue=lambda state: "公告：" + str(state.get_attr("GAME", "notice")),
        dialogue_policy=Narration(),
        on_result=publish_new_value,
        display_name="状态公告",
    )
    script = Script(
        vocab=vocab,
        roles=[role],
        casting=FixedDeal({"Player_1": "villager"}),
        scopes=[Scope("town", lambda state: {"Player_1"})],
        flow=Sequence([scene], loop=False),
        referee=lambda state: None,
    )
    state = State(vocab)
    state.register_entity("GAME", {"notice": "old"})
    stage = Stage(scopes=script.scopes, cast=cast)
    narrator = Narrator(stage=stage)
    director = Director(
        script=script,
        stage=stage,
        narrator=narrator,
        cast=cast,
        on_state_snapshot=lambda state: events.append(
            ("snapshot", state.get_attr("GAME", "notice"))
        ),
    )

    await director.run(state)

    assert any(msg["text"] == "公告：new" for msg in actor.perceived)
    assert events.index(("message", "公告：new")) < events.index(("snapshot", "new"))
