"""Actor ports, AI actor implementation, and AI actor factory."""

import json
import uuid
from typing import Any, Protocol

from pydantic import ValidationError

from ccserver.session import SessionManager
from ccserver.factory import AgentFactory
from ccserver.emitters.collect import CollectEmitter

from .constants import MAX_COLLECT_RETRIES
from .models import ActorProfile, Role


class ActorPort(Protocol):
    """
    ActorPort — Director/Stage/Cast 依赖的最小 Actor 协议。

    Engine core 只认识这个协议，不关心具体控制来源是 LLM、真人、
    mock、回放还是远程 Bot。这样 human 参与是可选 controller，而不是
    engine 的硬依赖。
    """
    name: str
    player_id: str
    display_name: str
    nickname: str
    controller_type: str
    role_name: str | None
    role_display_name: str | None

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """设置玩家席位展示资料。"""

    def set_role_snapshot(self, role: Role) -> None:
        """保存本局角色快照，权威身份仍在 State。"""

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """设置稳定身份档案。"""

    async def perceive(self, msg: dict) -> None:
        """接收一条已经过 Stage 授权路由的消息。"""

    async def act(self, cue: str, collect: Any = None) -> dict:
        """根据当前上下文产出自由文本或结构化动作。"""


class ActorController(Protocol):
    """
    ActorController — SeatActor 的行动来源。

    一个 SeatActor 可以委托给 AI、human、mock 等不同 controller。
    这为掉线后 AI 接管、主持人代操作、测试替身提供稳定扩展点。
    """
    controller_type: str

    def set_actor(self, actor: "SeatActor") -> None:
        """绑定所属 SeatActor。"""

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """接收玩家席位展示资料。"""

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """接收稳定身份档案。"""

    async def perceive(self, msg: dict) -> None:
        """接收一条授权消息。"""

    async def act(self, cue: str, collect: Any = None) -> dict:
        """产出动作。"""


class SeatActor:
    """
    SeatActor — 游戏里的 Actor/席位执行体。

    SeatActor 承载 Actor 的稳定身份和角色快照，把具体行动委托给
    ActorController。Director/Stage 只依赖 SeatActor 的 ActorPort 行为，
    不知道 controller 是 AI 还是真人。
    """

    def __init__(self, name: str, controller: ActorController) -> None:
        """
        初始化席位 Actor。

        参数：
          name       — Actor/seat 名称，如 "Player_1"。
          controller — 行动控制器，负责 perceive/act 的具体实现。
        """
        assert name, "SeatActor.name 不能为空"
        assert controller is not None, "SeatActor.controller 不能为空"

        self.name = name
        self.actor_id = str(uuid.uuid4())
        self.player_id = name
        self.display_name = name
        self.nickname = ""
        self.controller_type = getattr(controller, "controller_type", "unknown")
        self.is_human = self.controller_type == "human"
        self.role_name = None
        self.role_display_name = None
        self._profile: ActorProfile | None = None
        self._controller = controller

        if hasattr(controller, "set_actor"):
            controller.set_actor(self)

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """设置运行时展示资料，并同步给 controller。"""
        self.player_id = player_id
        self.display_name = display_name or player_id
        self.nickname = nickname or ""
        if hasattr(self._controller, "set_player_profile"):
            self._controller.set_player_profile(
                player_id=player_id,
                display_name=self.display_name,
                nickname=self.nickname,
            )

    def set_role_snapshot(self, role: Role) -> None:
        """保存本局角色快照，方便 debug/UI 展示；权威身份仍在 State 中。"""
        self.role_name = role.name
        self.role_display_name = role.display_name or role.name

    def set_candidates(self, candidates: list) -> None:
        """
        注入本幕候选目标，转发给支持该接口的 controller。

        engine 的 _prepare_actor_for_scene 会在每幕开始前调用，让真人
        controller 在创建 ActionRequest 时把 candidates 带上，供后端 submit
        校验和前端渲染选择列表使用。

        参数：
          candidates — 候选目标列表
        """
        if hasattr(self._controller, "set_candidates"):
            self._controller.set_candidates(candidates)

    def set_scene_context(self, scene_name: str, scene_display_name: str = "") -> None:
        """注入当前幕元信息，供真人提交文本自动补前缀。"""
        if hasattr(self._controller, "set_scene_context"):
            self._controller.set_scene_context(scene_name, scene_display_name)

    def set_action_request_hints(self, kind: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        """注入下一次真人输入请求的协议提示。"""
        if hasattr(self._controller, "set_action_request_hints"):
            self._controller.set_action_request_hints(kind=kind, metadata=metadata)

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """
        设置稳定身份档案，并转发给 controller。

        AI controller 会把它注入 prompt；human controller 会把它推给玩家 UI。
        """
        assert isinstance(profile, ActorProfile), (
            f"profile 必须是 ActorProfile，实际：{type(profile)}"
        )
        self._profile = profile
        self.role_name = profile.role_name
        self.role_display_name = profile.role_display_name or profile.role_name
        if hasattr(self._controller, "set_actor_profile"):
            self._controller.set_actor_profile(profile)

    async def perceive(self, msg: dict) -> None:
        """把消息交给 controller。"""
        await self._controller.perceive(msg)

    async def act(self, cue: str, collect: Any = None) -> dict:
        """请求 controller 产出动作。"""
        return await self._controller.act(cue, collect)


class ObservationBuffer:
    """
    感知缓冲 — 暂存 Actor 接收到的所有观察消息。

    perceive() 把消息存这里，act() 时统一格式化发给模型。
    act() 结束后清空缓冲，开始积累下一幕的观察。
    """

    def __init__(self):
        """初始化空缓冲。"""
        # 观察列表，每条观察是一个字典
        # 格式：{"scope": 可见域名, "sender": 发言人, "text": 文本内容}
        self._observations: list = []

    def add(self, msg: dict) -> None:
        """
        添加一条观察消息。

        参数：
          msg — 消息字典，格式：
                {"scope": "wolf-den", "sender": "Player2", "text": "我建议刀 Player5"}
                或任意包含 "text" 的字典
        """
        self._observations.append(msg)

    def clear(self) -> None:
        """清空缓冲（act 结束后调用）。"""
        self._observations.clear()

    def is_empty(self) -> bool:
        """返回缓冲是否为空。"""
        return len(self._observations) == 0

    def get_all(self) -> list:
        """返回所有观察（副本，防止外部修改）。"""
        return self._observations[:]


class PerceptionFormatter:
    """
    感知格式化器 — 把缓冲里的观察消息格式化成一段结构化文本，
    让模型能清晰理解「场上发生了什么」以及「现在轮到你做什么」。

    格式化后的文本示例：
      【场上动态】
      (wolf-den) Player2：我建议今晚刀 Player5
      (wolf-den) Player3：同意，Player5 很可疑
      (town) 主持人：天亮了，昨晚 Player5 被淘汰。

      【你的任务】
      请发表你对局势的看法。

    这个类是 Strategy 模式 —— 可以子类化替换格式，引擎不变。
    例如：未来真人版可以改成 UI 渲染，而不是纯文本。
    """

    def format(
        self,
        observations: list,
        cue: str,
        profile: ActorProfile = None,
    ) -> str:
        """
        把观察列表和提示词合并成一条消息文本。

        参数：
          observations — 观察字典列表（来自 ObservationBuffer.get_all()）
          cue          — 本幕旁白提示词（告诉 Actor 该做什么）
          profile      — 稳定身份档案；非 None 时放在 observation 之前

        返回：
          合并后的完整消息文本，将作为 Agent.run() 的输入
        """
        parts = []

        # 身份档案是稳定上下文，不是“听到的场上消息”。
        # The profile is stable context, not an observed event.
        if profile is not None:
            parts.append(profile.render_for_prompt())

        # 如果有观察消息，先输出「场上动态」区块
        if observations:
            if parts:
                parts.append("")
            parts.append("【场上动态】")
            for obs in observations:
                scope_label = obs.get("scope", "")
                sender = obs.get("sender", "")
                text = obs.get("text", "")

                if sender:
                    # 有发言人：「(可见域) 发言人：内容」
                    parts.append(f"({scope_label}) {sender}：{text}")
                else:
                    # 无发言人（如旁白公告）：直接显示内容
                    parts.append(f"({scope_label}) {text}")

        # 输出「你的任务」区块
        if cue:
            if parts:
                parts.append("")   # 空行分隔
            parts.append("【你的任务】")
            parts.append(cue)

        return "\n".join(parts)


class AgentActor:
    """
    Actor 的 ccserver 实现 — 把 ccserver Agent 包装成舞台上的表演者。

    核心设计（ObservationBuffer + PerceptionFormatter）：
      - perceive()：完全不碰 Agent 内部，只存入 ObservationBuffer
      - act()：PerceptionFormatter 格式化 → Agent.run(合并消息) → 清空缓冲

    这样做的好处：
      1. ccserver 零改动（不调私有方法 _append，不写 context.messages）
      2. 格式化逻辑在 drama 层（SRP），换格式不动引擎（OCP）
      3. 观察随合并消息自然进对话历史，跨幕记忆完整
    """

    def __init__(self, name: str, agent: Any, formatter: PerceptionFormatter = None, tracer: Any = None, gate: Any = None):
        """
        初始化 AgentActor。

        参数：
          name      — Actor 名称，如 "Player1"
          agent     — ccserver Agent 实例（由 AgentFactory.create_root 构建）
          formatter — 感知格式化器，默认使用 PerceptionFormatter()
          tracer    — 可选的观测记录器（如 web_trace.PerceptionTracer）。
                      非 None 时，perceive/act 会把事件结构化记录给它，用于可视化/回放。
                      为 None 时完全不影响原有行为（观测是 opt-in 的旁路，对应设计文档 §7.H）。
          gate      — 可选的「暂停闸门」，需提供 async 方法 wait()。
                      act 开始前会 await gate.wait()：未暂停时立即返回，暂停时阻塞到恢复。
                      为 None 时无影响。
        """
        # Actor 名称（公开属性，Director/Stage 通过 name 查找 Actor）
        self.name = name
        self.actor_id = str(uuid.uuid4())
        self.player_id = name
        self.display_name = name
        self.nickname = ""
        self.controller_type = "ai"
        self.is_human = False
        self.role_name = None
        self.role_display_name = None
        self._profile: ActorProfile | None = None

        # ccserver Agent 实例
        self._agent = agent

        # 感知缓冲：暂存 perceive 接收的消息
        self._buffer = ObservationBuffer()

        # 格式化器：把缓冲格式化成模型可读的文本
        self._formatter = formatter or PerceptionFormatter()

        # 观测记录器（可选）。None 时不记录。
        self._tracer = tracer

        # 暂停闸门（可选）。None 时不暂停。
        self._gate = gate

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """设置运行时展示资料。权威玩家资料仍来自 Script.player_config。"""
        self.player_id = player_id
        self.display_name = display_name or player_id
        self.nickname = nickname or ""

    def set_role_snapshot(self, role: Role) -> None:
        """保存本局角色快照，方便 debug/UI 展示；权威身份仍在 State 中。"""
        self.role_name = role.name
        self.role_display_name = role.display_name or role.name

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """
        设置 AI Actor 的稳定身份档案。

        该档案不进入 observation buffer，而是在每次 act() 构造 prompt 时注入。
        """
        assert isinstance(profile, ActorProfile), (
            f"profile 必须是 ActorProfile，实际：{type(profile)}"
        )
        self._profile = profile
        self.role_name = profile.role_name
        self.role_display_name = profile.role_display_name or profile.role_name

    async def perceive(self, msg: dict) -> None:
        """
        接收一条消息，存入感知缓冲。

        不触发 LLM 生成，只存数据。
        下次 act() 时会把缓冲里所有消息一起格式化发给模型。

        参数：
          msg — 消息字典，格式：
                {"scope": str, "sender": str, "text": str}
        """
        self._buffer.add(msg)
        print(f"[AgentActor:{self.name}] perceive 存入缓冲：{msg.get('text', '')[:50]}")

        # 观测旁路：如果挂了 tracer，记录「这个 actor 听到了什么」（不影响主流程）
        if self._tracer is not None:
            self._tracer.record_perceive(
                actor=self.name,
                scope=msg.get("scope", ""),
                sender=msg.get("sender", ""),
                text=msg.get("text", ""),
            )

    async def act(self, cue: str, collect: Any = None) -> dict:
        """
        根据当前上下文生成发言或结构化动作。

        流程：
          1. PerceptionFormatter 把缓冲里的观察格式化成文本块
          2. 与 cue（旁白提示词）合并成一条完整消息
          3. 调用 Agent.run(合并消息)，获取 LLM 生成的文本
          4. 清空感知缓冲（本幕结束）
          5. 若 response_model 非空，解析结构化输出（最多重试 MAX_COLLECT_RETRIES 次）

        参数：
          cue     — 旁白提示词，告诉 Actor 该做什么
          collect — Pydantic Model class 或 None。
                    非 None 时要求生成符合该 Model 的 JSON

        返回：
          Response 字典：{"actor": str, "text": str, "data": dict 或 None}
            - actor — 发言人名字
            - text  — 发言文本（自由文本时就是这个）
            - data  — 结构化数据（response_model 非空时才有）
        """
        # 步骤 0：暂停闸门。若处于暂停状态，在这里阻塞，直到恢复才继续发言。
        # （放在最前面，保证「暂停」后不会有新的 LLM 调用发生。）
        if self._gate is not None:
            await self._gate.wait()

        # 步骤 1+2：格式化缓冲 + 合并 cue
        observations = self._buffer.get_all()
        full_message = self._formatter.format(observations, cue, profile=self._profile)

        print(f"[AgentActor:{self.name}] act 开始，缓冲条数={len(observations)}")
        print(f"[AgentActor:{self.name}] 发送给 LLM 的消息（前100字）：{full_message[:100]}")

        # 步骤 3：调用 ccserver Agent.run()
        # Agent.run() 把 full_message 作为用户输入追加到对话历史，然后调用 LLM
        if collect is not None:
            # 有结构化输出要求：在消息里提示模型输出 JSON
            json_schema = collect.model_json_schema()
            schema_hint = json.dumps(json_schema, ensure_ascii=False, indent=2)
            full_message = (
                full_message
                + f"\n\n【输出格式要求】\n请以 JSON 格式回复，符合以下 schema：\n{schema_hint}\n"
                + "不要输出任何 JSON 以外的内容，不要包含 markdown 代码块标记。"
            )

        raw_text = await self._agent.run(full_message)

        # 步骤 4：清空缓冲（本幕观察已消费）
        self._buffer.clear()

        print(f"[AgentActor:{self.name}] LLM 返回（前100字）：{raw_text[:100]}")

        # 步骤 5：如果需要结构化输出，解析 JSON
        if collect is not None:
            parsed_data = await self._parse_collect(raw_text, collect, cue)
            return {"actor": self.name, "text": raw_text, "data": parsed_data}

        # 自由文本发言，直接返回
        return {"actor": self.name, "text": raw_text, "data": None}

    async def _parse_collect(self, raw_text: str, collect: Any, original_cue: str) -> dict:
        """
        解析结构化输出，失败时重试。

        内部辅助方法，由 act() 调用。

        参数：
          raw_text     — LLM 返回的原始文本
          collect      — Pydantic Model class
          original_cue — 原始提示词（重试时用）

        返回：
          符合 response_model 的 Pydantic 对象（转成 dict）
        """
        current_text = raw_text

        for attempt in range(MAX_COLLECT_RETRIES):
            try:
                # 尝试把文本解析成 JSON
                # 先尝试直接解析，再尝试提取 ```json ... ``` 块
                json_str = current_text.strip()
                if json_str.startswith("```"):
                    # 去掉 markdown 代码块标记
                    lines = json_str.split("\n")
                    json_lines = [
                        line for line in lines
                        if not line.startswith("```")
                    ]
                    json_str = "\n".join(json_lines).strip()

                data = json.loads(json_str)
                model_instance = collect(**data)
                print(f"[AgentActor:{self.name}] 结构化解析成功（第{attempt+1}次）")
                return model_instance.model_dump()

            except (json.JSONDecodeError, ValidationError, TypeError) as e:
                print(
                    f"[AgentActor:{self.name}] 结构化解析失败（第{attempt+1}次）："
                    f"{e}，原始文本：{current_text[:100]}"
                )

                if attempt < MAX_COLLECT_RETRIES - 1:
                    # 还有重试次数，要求模型重新输出
                    retry_cue = (
                        f"你刚才的输出不是合法的 JSON，解析失败：{e}\n"
                        f"请重新输出，只输出 JSON，不要包含任何其他内容。\n"
                        f"原始要求：{original_cue}"
                    )
                    current_text = await self._agent.run(retry_cue)
                    self._buffer.clear()  # 重试也要清空缓冲

        # 所有重试都失败，抛出异常
        raise ValueError(
            f"Actor {self.name} 的结构化输出解析失败，"
            f"已重试 {MAX_COLLECT_RETRIES} 次，最后输出：{raw_text[:200]}"
        )


class AgentActorController:
    """
    AI ActorController — 通过 AgentActor 执行动作。

    这个 controller 让 SeatActor/ActorController 架构复用 AgentActor 的
    LLM 调用、prompt 拼接和结构化输出解析能力。
    """

    controller_type = "ai"

    def __init__(self, agent_actor: AgentActor) -> None:
        """
        初始化 AI controller。

        参数：
          agent_actor — AgentActor 实例，承载 LLM 调用与感知缓冲。
        """
        assert agent_actor is not None, "agent_actor 不能为空"
        self._agent_actor = agent_actor
        self._actor: SeatActor | None = None

    def set_actor(self, actor: SeatActor) -> None:
        """绑定所属 SeatActor。"""
        self._actor = actor

    def set_player_profile(
        self,
        player_id: str,
        display_name: str = "",
        nickname: str = "",
    ) -> None:
        """同步玩家展示资料给 AgentActor。"""
        if hasattr(self._agent_actor, "set_player_profile"):
            self._agent_actor.set_player_profile(player_id, display_name, nickname)

    def set_actor_profile(self, profile: ActorProfile) -> None:
        """同步稳定身份档案给 AgentActor。"""
        self._agent_actor.set_actor_profile(profile)

    async def perceive(self, msg: dict) -> None:
        """转发消息给 AgentActor。"""
        await self._agent_actor.perceive(msg)

    async def act(self, cue: str, collect: Any = None) -> dict:
        """转发行动请求给 AgentActor。"""
        response = await self._agent_actor.act(cue, collect)
        if self._actor is not None:
            response["actor"] = self._actor.name
        return response


def create_agent_actor(
    name: str,
    system_prompt: str,
    project_root: str = None,
    model: str = None,
    adapter: Any = None,
    tracer: Any = None,
    gate: Any = None,
) -> SeatActor:
    """
    快速创建一个接真实 LLM 的 AgentActor。

    封装了创建 ccserver Session + Agent 的样板代码。

    参数：
      name          — Actor 名字，如 "Player1"
      system_prompt — Agent 的 system 提示词（角色定位）
      project_root  — ccserver 项目根目录路径（用于加载 .ccserver/settings.json）
      model         — LLM 模型名（None 时使用 ccserver 默认配置）
      adapter       — 自定义 ModelAdapter 实例（None 时由 AgentFactory 按
                      settings/环境变量自动构建）。
                      需要自定义渠道时传入，例如：
                        from ccserver.model.factory import AdapterFactory
                        from ccserver.model.endpoint import ModelEndpoint
                        adapter = AdapterFactory.build(ModelEndpoint(
                            model_id="claude-sonnet-4-6",
                            api_type="anthropic-messages",
                            base_url="https://your-gateway.com",
                            api_key="sk-xxx",
                        ))
      tracer        — 可选观测记录器（如 web_trace.PerceptionTracer），透传给 AgentActor。
                      非 None 时记录该 actor 的 perceive/act 事件用于可视化；None 时无影响。
      gate          — 可选「暂停闸门」（含 async wait()），透传给 AgentActor。None 时无影响。

    返回：
      SeatActor 实例，controller_type="ai"
    """
    from pathlib import Path as _Path
    project_path = None if project_root is None else _Path(project_root)
    # 创建 Session
    session_manager = SessionManager(project_root=project_path)
    session = session_manager.create()

    # 创建 Emitter（收集模式，不向 UI 推送 token）
    emitter = CollectEmitter()

    # 用 AgentFactory 创建 Agent（封装了 adapter/tools/settings 的复杂构建逻辑）
    kwargs = {
        "name": name,
        "system": system_prompt,
        "append_system": True,    # 追加到 system，而不是替换整个 system
        "run_mode": "auto",       # 非交互模式，直接运行
        "stream": False,          # 非流式，等待完整结果
    }
    if model:
        kwargs["model"] = model
    if adapter is not None:
        kwargs["adapter"] = adapter   # 传入自定义 ModelAdapter，覆盖自动构建的渠道

    agent = AgentFactory.create_root(session, emitter, **kwargs)

    print(f"[Factory] 创建 AgentActorController：{name}（adapter={'自定义' if adapter else '默认'}）")
    agent_actor = AgentActor(name=name, agent=agent, tracer=tracer, gate=gate)
    controller = AgentActorController(agent_actor)
    return SeatActor(name=name, controller=controller)
