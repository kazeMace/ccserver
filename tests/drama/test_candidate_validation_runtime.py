"""运行时候选集校验测试。"""

import asyncio

from pydantic import create_model

from drama_engine.core.engine import (
    Cast,
    Director,
    FixedDeal,
    Narrator,
    Role,
    Scope,
    Scene,
    Script,
    Sequence,
    Single,
    OpenChat,
    Stage,
    State,
    Vocabulary,
)


class ScriptedActor:
    """按预设响应依次返回的测试 Actor。"""

    def __init__(self, name: str, responses: list):
        self.name = name
        self.responses = list(responses)
        self.cues = []
        self.received = []

    async def act(self, cue: str, response_model=None) -> dict:
        self.cues.append(cue)
        assert self.responses, "测试 Actor 没有剩余响应"
        return self.responses.pop(0)

    async def perceive(self, msg: dict) -> None:
        self.received.append(msg)


def _make_state() -> State:
    vocab = Vocabulary(
        roles=frozenset(),
        factions=frozenset(),
        scopes=frozenset({"public"}),
        abilities=frozenset(),
    )
    state = State(vocab)
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True})
    state.register_entity("P2", {"alive": True})
    return state


def test_single_retries_when_vote_is_outside_candidates():
    """结构化投票目标不在 candidates 内时，应重试并使用合法结果。"""

    async def main():
        vote_model = create_model("VoteModel", vote=(str, ...), reason=(str, ...))
        scene = Scene(
            name="vote",
            scope="public",
            participants=lambda state: {"P1"},
            cue="请投票",
            dialogue_policy=Single(),
            response_model=vote_model,
            candidates=lambda state, actor: ["P2"],
        )
        state = _make_state()
        cast = Cast()
        actor = ScriptedActor(
            "P1",
            responses=[
                {"actor": "P1", "text": "bad", "data": {"vote": "P3", "reason": "x"}},
                {"actor": "P1", "text": "ok", "data": {"vote": "P2", "reason": "y"}},
            ],
        )
        listener = ScriptedActor("P2", responses=[])
        cast.add(actor)
        cast.add(listener)
        stage = Stage(scopes=[Scope("public", lambda state: {"P1", "P2"})], cast=cast)

        responses = await scene.dialogue_policy.run(scene, stage, state, cast)

        assert responses[0]["data"]["vote"] == "P2"
        assert len(actor.cues) == 2
        assert "上次输出无效" in actor.cues[1]
        assert "当前可选目标：P2" in actor.cues[1]

    asyncio.run(main())


def test_candidate_validation_allows_self_when_self_is_candidate():
    """引擎不内置禁选自己；只要 self 在候选集内，就允许选择自己。"""

    async def main():
        vote_model = create_model("VoteModelSelf", vote=(str, ...), reason=(str, ...))
        scene = Scene(
            name="vote",
            scope="public",
            participants=lambda state: {"P1"},
            cue="请投票",
            dialogue_policy=Single(),
            response_model=vote_model,
            candidates=lambda state, actor: ["P1", "P2"],
        )
        state = _make_state()
        cast = Cast()
        actor = ScriptedActor(
            "P1",
            responses=[
                {"actor": "P1", "text": "self", "data": {"vote": "P1", "reason": "x"}},
            ],
        )
        cast.add(actor)
        stage = Stage(scopes=[Scope("public", lambda state: {"P1"})], cast=cast)

        responses = await scene.dialogue_policy.run(scene, stage, state, cast)

        assert responses[0]["data"]["vote"] == "P1"
        assert len(actor.cues) == 1

    asyncio.run(main())


def test_choose_many_retries_when_count_or_distinct_constraints_fail():
    """ChooseMany 应支持 count/distinct 硬约束。"""

    async def main():
        model = create_model("ChooseManyModel", targets=(list[str], ...), reason=(str, ...))
        scene = Scene(
            name="cupid-link",
            scope="public",
            participants=lambda state: {"P1"},
            cue="请选择两名情侣",
            dialogue_policy=Single(),
            response_model=model,
            candidates=lambda state, actor: ["P1", "P2"],
            candidate_constraints={"count": 2, "distinct": True},
        )
        state = _make_state()
        cast = Cast()
        actor = ScriptedActor(
            "P1",
            responses=[
                {"actor": "P1", "text": "one", "data": {"targets": ["P1"], "reason": "x"}},
                {"actor": "P1", "text": "dup", "data": {"targets": ["P1", "P1"], "reason": "y"}},
                {"actor": "P1", "text": "ok", "data": {"targets": ["P1", "P2"], "reason": "z"}},
            ],
        )
        listener = ScriptedActor("P2", responses=[])
        cast.add(actor)
        cast.add(listener)
        stage = Stage(scopes=[Scope("public", lambda state: {"P1", "P2"})], cast=cast)

        responses = await scene.dialogue_policy.run(scene, stage, state, cast)

        assert responses[0]["data"]["targets"] == ["P1", "P2"]
        assert len(actor.cues) == 3
        assert "必须选择 2 个目标" in actor.cues[1]
        assert "不能包含重复目标" in actor.cues[2]

    asyncio.run(main())


def test_choose_many_retries_when_count_must_match_all_candidates():
    """ChooseMany 的 count=all_candidates 应要求选中所有当前候选目标。"""

    async def main():
        model = create_model("ChooseManyAllModel", targets=(list[str], ...), reason=(str, ...))
        scene = Scene(
            name="speech-order",
            scope="public",
            participants=lambda state: {"P1"},
            cue="请选择所有发言玩家",
            dialogue_policy=Single(),
            response_model=model,
            candidates=lambda state, actor: ["P1", "P2", "P3"],
            candidate_constraints={"count": "all_candidates", "distinct": True},
        )
        state = _make_state()
        cast = Cast()
        actor = ScriptedActor(
            "P1",
            responses=[
                {"actor": "P1", "text": "short", "data": {"targets": ["P1", "P2"], "reason": "x"}},
                {"actor": "P1", "text": "ok", "data": {"targets": ["P2", "P3", "P1"], "reason": "z"}},
            ],
        )
        cast.add(actor)
        cast.add(ScriptedActor("P2", responses=[]))
        cast.add(ScriptedActor("P3", responses=[]))
        stage = Stage(scopes=[Scope("public", lambda state: {"P1", "P2", "P3"})], cast=cast)

        responses = await scene.dialogue_policy.run(scene, stage, state, cast)

        assert responses[0]["data"]["targets"] == ["P2", "P3", "P1"]
        assert len(actor.cues) == 2
        assert "必须选择 3 个目标" in actor.cues[1]

    asyncio.run(main())


def test_director_does_not_announce_private_action_cue():
    """announce_response_cue=false 时，主持人不把行动提示广播给 scene.scope。"""

    async def main():
        model = create_model("HunterActionModel", action=(bool, ...), target=(str | None, None))
        hunter_role = Role(
            name="hunter",
            display_name="猎人",
            faction="good",
            abilities=[],
            brief="你是猎人。",
            scopes=["town"],
        )
        villager_role = Role(
            name="villager",
            display_name="村民",
            faction="good",
            abilities=[],
            brief="你是村民。",
            scopes=["town"],
        )
        scene = Scene(
            name="hunter-day",
            display_name="猎人开枪（白天）",
            scope="town",
            participants=lambda state: {"P1"},
            cue="你已死亡。作为猎人，你可以选择开枪或不开枪。是否开枪？",
            announce_response_cue=False,
            dialogue_policy=Single(),
            response_model=model,
            response_messages=[{
                "source": "action",
                "targets": [
                    {"to": "self", "render": "self"},
                    {
                        "to": "scope",
                        "when": {"value": {"ref": "data.action"}, "equal": True},
                        "render": "{actor} 是猎人，开枪带走了 {data.target}。",
                    },
                ],
            }],
        )
        script = Script(
            vocab=Vocabulary(
                roles=frozenset({"hunter", "villager"}),
                factions=frozenset({"good"}),
                scopes=frozenset({"town"}),
                abilities=frozenset(),
            ),
            roles=[hunter_role, villager_role],
            casting=FixedDeal({"P1": "hunter", "P2": "villager"}),
            scopes=[Scope("town", lambda state: {"P1", "P2"})],
            flow=Sequence([scene], loop=False),
            referee=lambda state: None,
        )
        cast = Cast()
        hunter = ScriptedActor(
            "P1",
            responses=[{"actor": "P1", "text": "no", "data": {"action": False, "target": None}}],
        )
        villager = ScriptedActor("P2", responses=[])
        cast.add(hunter)
        cast.add(villager)
        stage = Stage(scopes=script.scopes, cast=cast)
        narrator = Narrator(stage)
        director = Director(script, stage, narrator, cast)
        state = State(script.vocab)

        await director.run(state)

        assert hunter.cues
        assert "是否开枪" in hunter.cues[0]
        assert not any("是否开枪" in msg.get("text", "") for msg in villager.received)
        assert not any("猎人" in msg.get("text", "") for msg in villager.received)

    asyncio.run(main())

def test_openchat_runs_random_free_conversation_rounds():
    """OpenChat 应在 PartySessionRuntime scene 内调度多轮自由发言。"""

    async def main():
        scene = Scene(
            name="free-chat",
            scope="public",
            participants=lambda state: ["P1", "P2"],
            cue="请自由讨论",
            dialogue_policy=OpenChat(rounds=2, speakers_per_round=1),
            response_model=None,
        )
        state = _make_state()
        cast = Cast()
        actor1 = ScriptedActor(
            "P1",
            responses=[
                {"actor": "P1", "text": "P1-a", "data": None},
                {"actor": "P1", "text": "P1-b", "data": None},
            ],
        )
        actor2 = ScriptedActor(
            "P2",
            responses=[
                {"actor": "P2", "text": "P2-a", "data": None},
                {"actor": "P2", "text": "P2-b", "data": None},
            ],
        )
        cast.add(actor1)
        cast.add(actor2)
        stage = Stage(scopes=[Scope("public", lambda state: {"P1", "P2"})], cast=cast)

        responses = await scene.dialogue_policy.run(scene, stage, state, cast)

        assert len(responses) == 2
        assert {response["actor"] for response in responses}.issubset({"P1", "P2"})
        assert len(actor1.received) + len(actor2.received) >= 2

    asyncio.run(main())

