"""Human actor input ports and controller implementation."""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from .actors import SeatActor
from .models import ActorProfile


@dataclass(frozen=True)
class ActionRequest:
    """
    动作请求 — runtime 正在等待某个 seat 完成的动作。

    参数：
      request_id  — 本次动作请求 ID。
      seat_id     — 目标 seat/actor 名称。
      cue         — 当前任务提示。
      schema      — response_model 生成的 JSON schema；自由发言时为 None。
      scene_name  — 可选场景 ID。
      scene_display_name — 可选场景展示名，用于真人提交文本前缀。
      candidates  — 可选候选项，后端 submit 时校验。
      deadline_at — 可选截止时间（loop.time() 基准），超时策略使用。
      kind        — 动作类型（speech / vote / night_action / structured），
                    决定超时策略。默认 speech。
      allow_resubmit — 是否允许重复提交覆盖已提交结果。默认 False。
      timeout_seconds — 超时秒数，由 ActionRequestService 写入，供 watcher 使用。
    """
    request_id: str
    seat_id: str
    cue: str
    schema: dict | None = None
    scene_name: str = ""
    scene_display_name: str = ""
    candidates: list | None = None
    deadline_at: Any = None
    kind: str = "speech"
    allow_resubmit: bool = False
    timeout_seconds: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ActionSubmission:
    """
    动作提交 — 真人、AI、主持人或超时策略提交的一次动作。

    参数：
      submission_id — 提交 ID。
      request_id    — 对应 ActionRequest。
      seat_id       — 提交 seat。
      source        — human / ai / moderator / timeout_default。
      data          — 原始结构化数据。
      text          — 可读文本。
      validated     — 是否已通过校验。
      validation_error — 校验失败原因。
    """
    submission_id: str
    request_id: str
    seat_id: str
    source: str
    data: dict | None = None
    text: str = ""
    validated: bool = False
    validation_error: str = ""


class HumanInputPort(Protocol):
    """
    HumanInputPort — human controller 依赖的输入输出通道。

    新版 engine 不直接依赖 PlayerGateway、HTTP、SSE 或 WebSocket。
    这些传输实现只能通过本接口接入。
    """

    async def send_profile(self, seat_id: str, profile: ActorProfile) -> None:
        """向真人玩家发送身份档案。"""

    async def send_perception(self, seat_id: str, msg: dict) -> None:
        """向真人玩家发送一条已授权消息。"""

    async def request_action(self, request: ActionRequest, collect_model: Any = None) -> ActionSubmission:
        """请求真人玩家提交动作。collect_model 为 Pydantic 类，供后端 schema 校验。"""

    async def send_input_error(self, seat_id: str, request_id: str, error: str) -> None:
        """向真人玩家发送输入校验错误。"""


class ServiceHumanInputPort:
    """Human input port backed by the current runtime action service."""

    def __init__(self, service: Any, seat_id: str, tracer: Any = None) -> None:
        """初始化 service 输入端口。"""
        assert service is not None, "service 不能为空"
        assert seat_id, "seat_id 不能为空"
        self._service = service
        self._seat_id = seat_id
        self._tracer = tracer

    async def send_profile(self, seat_id: str, profile: ActorProfile) -> None:
        """向真人玩家发送身份档案。"""
        assert seat_id == self._seat_id, "seat_id 必须匹配当前 input port"
        self.send_profile_now(seat_id, profile)

    def send_profile_now(self, seat_id: str, profile: ActorProfile) -> None:
        """同步推送身份档案，供 setup 阶段立即写入玩家事件流。"""
        assert seat_id == self._seat_id, "seat_id 必须匹配当前 input port"
        self._emit_private(seat_id, {
            "kind": "actor_profile",
            "actor": seat_id,
            "text": profile.render_for_prompt(),
            "role": profile.role_name,
            "role_display_name": profile.role_display_name,
        })

    async def send_perception(self, seat_id: str, msg: dict) -> None:
        """向真人玩家发送感知事件。"""
        assert seat_id == self._seat_id, "seat_id 必须匹配当前 input port"
        assert isinstance(msg, dict), "msg 必须是 dict"
        event = {
            "kind": "perceive",
            "actor": seat_id,
            "scope": msg.get("scope", ""),
            "sender": msg.get("sender", ""),
            "text": msg.get("text", ""),
        }
        self._emit_private(seat_id, event)
        if self._tracer is not None:
            self._tracer.record_perceive(
                actor=seat_id,
                scope=msg.get("scope", ""),
                sender=msg.get("sender", ""),
                text=msg.get("text", ""),
            )

    async def request_action(self, request: ActionRequest, collect_model: Any = None) -> Any:
        """创建 service action request，并等待合法提交。"""
        assert request is not None, "request 不能为空"
        assert request.seat_id == self._seat_id, "request seat_id 必须匹配当前 input port"
        current = self._service.get_current_request(self._seat_id)
        current_request_id = getattr(current, "request_id", "") if current is not None else ""
        if current_request_id:
            return await self._service.wait_submission(current_request_id)
        created = self._service.create_request(
            seat_id=self._seat_id,
            cue=request.cue,
            kind=request.kind,
            candidates=request.candidates,
            schema=request.schema,
            metadata=request.metadata,
            scene_name=request.scene_name,
            scene_display_name=request.scene_display_name,
            allow_resubmit=request.allow_resubmit,
            timeout_seconds=request.timeout_seconds,
            collect_model=collect_model,
        )
        return await self._service.wait_submission(created.request_id)

    async def send_input_error(self, seat_id: str, request_id: str, error: str) -> None:
        """向真人玩家发送输入错误。"""
        assert seat_id == self._seat_id, "seat_id 必须匹配当前 input port"
        self._emit_private(seat_id, {
            "kind": "human_input_error",
            "actor": seat_id,
            "request_id": request_id,
            "error": error,
        })

    def _emit_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """通过 runtime action service 发布玩家私有事件。"""
        assert hasattr(self._service, "emit_private"), "service 必须提供 emit_private"
        self._service.emit_private(seat_id, event)


class HumanActorController:
    """
    真人 ActorController。

    它只依赖 HumanInputPort，不依赖 PlayerGateway。这样 human 参与是可选
    控制器，0 human 时系统仍可只用 AgentController 自动运行。
    """

    controller_type = "human"

    def __init__(self, input_port: HumanInputPort) -> None:
        """初始化真人 controller。"""
        assert input_port is not None, "input_port 不能为空"
        self._input_port = input_port
        self._actor: SeatActor | None = None
        self._profile: ActorProfile | None = None
        self._profile_sent = False
        self._candidates: list = []
        self._scene_name = ""
        self._scene_display_name = ""
        self._request_kind: str | None = None
        self._request_metadata: dict[str, Any] = {}

    def set_actor(self, actor: SeatActor) -> None:
        """绑定所属 SeatActor。"""
        self._actor = actor

    def set_candidates(self, candidates: list) -> None:
        """
        注入本幕候选目标。

        engine 的 _prepare_actor_for_scene 在每幕开始前调用。candidates 会
        带入 ActionRequest，供 service 后端校验和前端渲染选择列表。

        参数：
          candidates — 候选目标列表
        """
        self._candidates = list(candidates) if candidates else []

    def set_scene_context(self, scene_name: str, scene_display_name: str = "") -> None:
        """保存当前幕元信息，用于 ActionRequest 和真人提交前缀。"""
        self._scene_name = scene_name or ""
        self._scene_display_name = scene_display_name or scene_name or ""

    def set_action_request_hints(self, kind: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        """注入下一次真人输入请求的协议提示。

        controller_action 本身没有 collect model，但它有 choice/free_input 语义。
        这些提示必须传到 ActionRequest.metadata，前端才能渲染选项和自由输入。
        """
        self._request_kind = kind or None
        self._request_metadata = dict(metadata or {})

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """真人 controller 当前无需额外保存玩家展示资料。"""
        return None

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """保存身份档案，并在输入端口支持时立即推送。"""
        self._profile = profile
        if self._actor is not None and hasattr(self._input_port, "send_profile_now"):
            self._input_port.send_profile_now(self._actor.name, profile)
            self._profile_sent = True

    async def perceive(self, msg: dict) -> None:
        """把已授权消息推给真人输入通道。"""
        assert self._actor is not None, "HumanActorController 尚未绑定 SeatActor"
        await self._input_port.send_perception(self._actor.name, msg)

    def _infer_action_kind(self, collect: Any) -> str:
        """
        根据 collect 模型字段推断动作类型，决定超时策略。

          - collect 为 None → speech（自由发言）
          - 含 vote / choose 字段 → vote（投票）
          - 含 target / targets 字段 → night_action（夜间技能）
          - 其他 → structured（通用结构化）

        参数：
          collect — Pydantic Model class 或 None

        返回：
          动作类型字符串
        """
        if collect is None:
            return "speech"
        fields = set(getattr(collect, "model_fields", {}).keys())
        if "vote" in fields or "choose" in fields:
            return "vote"
        if "target" in fields or "targets" in fields:
            return "night_action"
        return "structured"

    async def act(self, cue: str, collect: Any = None) -> dict:
        """
        请求真人输入，校验 collect 后返回 Actor response。

        submit 校验在 service 完成，request_action 只在合法提交时返回。

        参数：
          cue     — 任务提示
          collect — Pydantic Model class 或 None

        返回：
            {"actor", "text", "data"}
        """
        assert self._actor is not None, "HumanActorController 尚未绑定 SeatActor"
        assert cue is not None, "cue 不能为 None"

        # 推送身份档案（如尚未推送） / Send profile if not yet sent
        if self._profile is not None and not self._profile_sent:
            await self._input_port.send_profile(self._actor.name, self._profile)
            self._profile_sent = True

        schema = collect.model_json_schema() if collect is not None else None
        kind = self._request_kind or self._infer_action_kind(collect)
        candidates = self._candidates or None
        metadata = dict(self._request_metadata)
        self._request_kind = None
        self._request_metadata = {}

        while True:
            request = ActionRequest(
                request_id=str(uuid.uuid4()),
                seat_id=self._actor.name,
                cue=cue,
                schema=schema,
                candidates=candidates,
                scene_name=self._scene_name,
                scene_display_name=self._scene_display_name,
                kind=kind,
                metadata=metadata,
            )
            submission = await self._input_port.request_action(request, collect_model=collect)
            data = submission.data or {}

            if collect is not None and not submission.validated:
                try:
                    assert isinstance(data, dict), (
                        f"[HumanActorController:{self._actor.name}] data 必须是 dict，实际：{type(data)}"
                    )
                    validated = collect(**data)
                    data = validated.model_dump()
                except Exception as exc:
                    error_msg = str(exc)
                    await self._input_port.send_input_error(
                        self._actor.name,
                        request.request_id,
                        error_msg,
                    )
                    continue

            # 构造可读文本 / Build readable text
            if isinstance(data, dict) and "text" in data:
                text_repr = data["text"]
            elif data:
                text_repr = str(data)
            else:
                text_repr = submission.text or cue

            return {
                "actor": self._actor.name,
                "text": text_repr,
                "data": data,
            }


def create_human_actor_from_port(
    name: str,
    input_port: HumanInputPort,
) -> SeatActor:
    """
    工厂函数：用已构造的 HumanInputPort 创建真人 SeatActor。

    service 层构建 ServiceHumanInputPort，再调用本工厂创建真人 Actor。
    engine core 不依赖任何传输实现，只依赖 HumanInputPort 协议。

    参数：
      name       — Actor 名字，如 "Player_1"
      input_port — 已构造的 HumanInputPort 实例（如 ServiceHumanInputPort）

    返回：
      SeatActor 实例，controller_type="human"
    """
    assert name, "name 不能为空 / name must not be empty"
    assert input_port is not None, "input_port 不能为 None / input_port must not be None"

    controller = HumanActorController(input_port=input_port)
    return SeatActor(name=name, controller=controller)
