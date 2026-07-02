import asyncio

import pytest
from pydantic import BaseModel

from drama_engine.core.engine import ActorProfile, ActionSubmission, HumanActorController, SeatActor


class RecordingController:
    """测试用 controller，记录 SeatActor 是否正确委托调用。"""

    controller_type = "test"

    def __init__(self):
        self.actor = None
        self.profile = None
        self.messages = []
        self.actions = []

    def set_actor(self, actor):
        self.actor = actor

    def set_player_profile(self, player_id, display_name="", nickname=""):
        self.player_id = player_id
        self.display_name = display_name
        self.nickname = nickname

    def set_actor_profile(self, profile):
        self.profile = profile

    async def perceive(self, msg):
        self.messages.append(msg)

    async def act(self, cue, response_model=None):
        self.actions.append((cue, response_model))
        return {"actor": self.actor.name, "text": "ok", "data": None}


def _profile() -> ActorProfile:
    return ActorProfile(
        actor_name="Player_1",
        display_name="一号",
        nickname="",
        role_name="seer",
        role_display_name="预言家",
        faction="good",
        brief="你是预言家。",
    )


@pytest.mark.asyncio
async def test_seat_actor_delegates_to_controller():
    controller = RecordingController()
    actor = SeatActor("Player_1", controller)
    profile = _profile()

    actor.set_player_profile("Player_1", "一号", "p1")
    actor.set_actor_profile(profile)
    await actor.perceive({"scope": "town", "text": "天亮了"})
    response = await actor.act("请发言")

    assert controller.actor is actor
    assert actor.controller_type == "test"
    assert actor.display_name == "一号"
    assert actor.role_name == "seer"
    assert controller.profile is profile
    assert controller.messages == [{"scope": "town", "text": "天亮了"}]
    assert response == {"actor": "Player_1", "text": "ok", "data": None}


class VoteModel(BaseModel):
    target: str


class FakeHumanInputPort:
    """测试用真人输入端口，先返回非法输入再返回合法输入。"""

    def __init__(self):
        self.profiles = []
        self.perceptions = []
        self.requests = []
        self.errors = []
        self.submissions = [
            {"wrong": "Player_2"},
            {"target": "Player_2"},
        ]

    async def send_profile(self, seat_id, profile):
        self.profiles.append((seat_id, profile))

    async def send_perception(self, seat_id, msg):
        self.perceptions.append((seat_id, msg))

    async def request_action(self, request, collect_model=None):
        self.requests.append(request)
        data = self.submissions.pop(0)
        return ActionSubmission(
            submission_id="sub",
            request_id=request.request_id,
            seat_id=request.seat_id,
            source="human",
            data=data,
            text=str(data),
        )

    async def send_input_error(self, seat_id, request_id, error):
        self.errors.append((seat_id, request_id, error))


@pytest.mark.asyncio
async def test_human_controller_uses_input_port_and_retries_validation():
    input_port = FakeHumanInputPort()
    actor = SeatActor("Player_1", HumanActorController(input_port))
    actor.set_actor_profile(_profile())

    await actor.perceive({"scope": "whisper:seer", "text": "请选择查验目标"})
    response = await actor.act("请选择目标", VoteModel)

    assert input_port.profiles[0][0] == "Player_1"
    assert input_port.perceptions == [
        ("Player_1", {"scope": "whisper:seer", "text": "请选择查验目标"})
    ]
    assert len(input_port.requests) == 2
    assert input_port.errors
    assert response["actor"] == "Player_1"
    assert response["data"] == {"target": "Player_2"}
