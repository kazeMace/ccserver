"""Fixed-flow runners for Drama Engine Web multi-session runtime.

本模块把已经迁移到 `drama_engine.core` 的 YAML compiler / Director / Stage /
State 真实链路接入 `PartySessionRuntime`。它只依赖 drama_engine 内部模块，
不修改 ccserver 核心 Agent 代码。

This module is the fixed-flow execution-model implementation. Generic runner
orchestration belongs in ``drama_engine.core.runner``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.engine import (
    Cast,
    Director,
    Narrator,
    SetAttr,
    Stage,
    State,
    StateWriter,
)
from drama_engine.core.session.runtime import PartySessionRuntime
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime_spec.registry import RuntimeSpec
from drama_engine.core.diagnostics.web_trace import PerceptionTracer

logger = logging.getLogger(__name__)


def build_default_adapter_from_env() -> Any:
    """根据环境变量构建默认的 LLM adapter（ModelAdapter）。

    供固定流程 runner 在未显式传入 adapter 时使用，让 AI 玩家能连到
    自定义网关（如 litellm 代理），而不依赖 ccserver 默认的 settings/endpoint
    解析路径——那条路径只认 ANTHROPIC_API_KEY，用 auth_token/网关 token 时
    会报 "Could not resolve authentication method"。

    读取的环境变量：
      CCSERVER_MODEL     — 模型 id，未设置时默认 "claude-sonnet-4-6"
      ANTHROPIC_BASE_URL — 网关地址（如 https://litellm.spaccez.com）
      DEEPSEEK_API_KEY   — api key（优先；避开 .env 里 ANTHROPIC_API_KEY
                           被 deepseek 直连 key 占用导致的串台）
      ANTHROPIC_API_KEY  — api key（DEEPSEEK_API_KEY 未设置时回退）

    Returns:
        构建好的 LLMProvider 实例，可直接传给 AgentFactory.create_root。

    Raises:
        AssertionError: base_url 或 api_key 为空时抛出，并提示具体缺失项。
    """
    from ccserver.model_engine import AdapterFactory, ModelEndpoint

    model_id = os.environ.get("CCSERVER_MODEL", "claude-sonnet-4-6")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    # api_key 优先用 DEEPSEEK_API_KEY：.env 的 ANTHROPIC_API_KEY 已被 deepseek
    # 直连 key 占用，若优先取它会带着错 key 打到 litellm 网关 → 401。
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

    assert base_url, (
        "build_default_adapter_from_env: 缺少 ANTHROPIC_BASE_URL，"
        "请 export ANTHROPIC_BASE_URL 或写入 .env"
    )
    assert api_key, (
        "build_default_adapter_from_env: 缺少 api_key，"
        "请 export DEEPSEEK_API_KEY（或 ANTHROPIC_API_KEY）或写入 .env"
    )

    endpoint = ModelEndpoint(
        model_id=model_id,
        api_type="anthropic-messages",
        base_url=base_url,
        api_key=api_key,
    )
    logger.info(
        "[build_default_adapter_from_env] 构建 adapter | model_id=%s base_url=%s",
        model_id,
        base_url,
    )
    return AdapterFactory.build(endpoint)


@dataclass(slots=True)
class RunnerRuntimeState:
    """Runner 内部运行状态。"""

    script: Any
    initial_state_spec: dict[str, Any]
    player_names: list[str]
    cast: Cast | None = None
    state: State | None = None
    director: Director | None = None
    casting_service: Any | None = None
    result: str | None = None


class FixedFlowGameRunner(BasicGameRunner):
    """Base runner for Director/Stage/State fixed-flow games."""

    def __init__(
        self,
        runtime: PartySessionRuntime,
        dry_run: bool = True,
        adapter: Any = None,
        declaration: RuntimeSpec | None = None,
    ) -> None:
        assert runtime is not None, "runtime 不能为空"
        super().__init__(
            runtime=runtime,
            declaration=declaration or RuntimeSpec(type="game_session"),
            dry_run=dry_run,
        )
        self.adapter = adapter
        # 按 env 懒构建的 adapter 缓存：同局多个 AI 玩家共享一个，避免重复构建。
        # 仅在非 dry_run 的 AI 分支首次需要时填充，dry_run/测试场景不会触发。
        self._resolved_adapter: Any = None
        self._state: RunnerRuntimeState | None = None

    def _resolve_adapter(self) -> Any:
        """Resolve the LLM adapter once per runner."""
        if self._resolved_adapter is None:
            self._resolved_adapter = self.adapter or build_default_adapter_from_env()
        return self._resolved_adapter

    def _session_state(self) -> Any:
        """Return service-owned session state."""
        return self.session_state

    def _runtime_state(self) -> Any:
        """Return runtime-owned transient state."""
        return self.runtime_state

    def action_port(self) -> Any | None:
        """Fixed-flow uses the runtime service action router directly."""
        return None


class SocialDeductionGameRunner(FixedFlowGameRunner):
    """Social deduction party game runner built on fixed-flow engine components."""

    def _publish_trace_event(self, event: dict[str, Any]) -> None:
        """把 Engine 观测旁路事件转成 Host dashboard 可消费的 SSE 事件。

        说明：
          - tracer 是 drama_engine 内部旁路，不修改 ccserver 核心 Agent。
          - host 需要看到所有 actor 的 act/perceive/narration，用于 dashboard。
          - public 只接收 narration，避免泄露 player 私密视角。
        """
        assert isinstance(event, dict), "trace event 必须是 dict"
        payload = dict(event)
        payload.setdefault("kind", "trace")
        payload.setdefault("text", "")
        payload.setdefault("actor", "")
        payload.setdefault("scope", "")
        payload.setdefault("sender", "")
        payload["source"] = "web_trace"
        if payload.get("kind") == "narration" and payload.get("scope") == "public":
            # public 事件会自动推送给 host 订阅者；不能再 append_host 一次，
            # 否则 Host dashboard 会收到同一句主持人公开发言两遍。
            self.event_publisher.public(payload)
            return
        self.event_publisher.host(payload)

    async def reset_runtime_state(self) -> None:
        """取消当前 Director 任务并丢弃本轮 runner 内存状态。

        restart 会保留 Web session、seat 控制方式和玩家链接，但必须丢弃
        Director/State/ActionRequestService 等只属于上一局的运行期对象。
        """
        runtime_state = self._runtime_state()
        if runtime_state.task is not None and not runtime_state.task.done():
            runtime_state.task.cancel()
            try:
                await runtime_state.task
            except asyncio.CancelledError:
                pass
        if self._state is not None:
            self.runtime.action_service.cancel_all()
        runtime_state.task = None
        self._state = None

    async def assign(self) -> None:
        """编译剧本并执行 Director.setup。"""
        session_state = self.session_state
        assert session_state.status == "lobby", (
            f"只有 lobby 状态可以 assign，当前: {session_state.status}"
        )
        runner_state = self._build_runtime_state()
        self._state = runner_state
        runner_state.casting_service.assign(runner_state.state)
        await runner_state.director.setup(runner_state.state)
        session_state.set_status("assigned")
        self.event_publisher.public({"kind": "session_assigned"})
        self.event_publisher.host({"kind": "session_assigned"})
        logger.info("[SocialDeductionGameRunner] assign 完成：session=%s", session_state.session_id)

    async def start(self) -> None:
        """启动 Director.run_flow 后台任务。"""
        session_state = self.session_state
        assert session_state.status == "assigned", (
            f"只有 assigned 状态可以 start，当前: {session_state.status}"
        )
        assert self._state is not None, "start 前必须先 assign"
        session_state.set_status("running")
        self.event_publisher.public({"kind": "session_started"})
        self.event_publisher.host({"kind": "session_started"})
        self._runtime_state().task = asyncio.create_task(self._run_flow())
        logger.info("[SocialDeductionGameRunner] start 完成：session=%s", session_state.session_id)

    async def _run_flow(self) -> None:
        """后台执行游戏流程。"""
        assert self._state is not None, "runner state 不能为空"
        try:
            result = await self._state.director.run_flow(self._state.state)
            self._state.result = result
            self._push_roles_from_state(self._state.state)
            self.session_state.set_status("ended")
            self.runtime_state.phase = "ended"
            self.event_publisher.public({"kind": "session_ended", "result": result})
            self.event_publisher.host({"kind": "session_ended", "result": result})
            logger.info("[SocialDeductionGameRunner] run_flow 完成：session=%s result=%s", self.session_state.session_id, result)
        except asyncio.CancelledError:
            logger.info("[SocialDeductionGameRunner] run_flow 已取消：session=%s", self.session_state.session_id)
            raise
        except Exception as exc:
            self.session_state.set_status("failed")
            self.runtime_state.phase = "failed"
            self.event_publisher.host({"kind": "session_failed", "error": str(exc)})
            logger.exception("[SocialDeductionGameRunner] run_flow 失败：session=%s", self.session_state.session_id)
        finally:
            self.runtime.action_service.cancel_all()

    def _build_runtime_state(self) -> RunnerRuntimeState:
        """构建一局所需的 fixed-flow 运行对象。"""
        compiler = YamlCompiler()
        session_state = self._session_state()
        script_path = session_state.script_path
        params = dict(session_state.params)
        errors = compiler.validate_file(script_path, params)
        assert not errors, f"YAML 校验失败: {errors}"
        script = compiler.compile(script_path, params=params)
        initial_state_spec = self._load_initial_state(script_path, params, compiler)
        player_names = _resolve_player_names(script)

        trace = PerceptionTracer(on_event=self._publish_trace_event)

        cast = self.context.actor_runtime.create_cast_for_script(
            script=script,
            session_state=session_state,
            human_seat_ids=set(session_state.human_seat_ids),
            action_service=self.runtime.action_service,
            tracer=trace,
            dry_run=self.dry_run,
            adapter_resolver=self._resolve_adapter,
            step_gate=self.step_gate,
        )

        state = State(script.vocab)
        state.register_entity("GAME", {})
        writer = StateWriter(state)
        for entity, attrs in (initial_state_spec or {}).items():
            if entity not in state.all_entities():
                state.register_entity(entity, {})
            for attr, value in (attrs or {}).items():
                writer.apply(SetAttr(entity, attr, value))

        stage = Stage(scopes=script.scopes, cast=cast)
        narrator = Narrator(stage=stage, narrator_name="主持人", tracer=trace)
        casting_service = self.context.actor_runtime.create_casting_service(
            script=script,
            stage=stage,
        )

        def on_setup(current_state: State) -> dict[str, dict[str, Any]]:
            return self._push_roles_from_state(current_state)

        director = Director(
            script=script,
            stage=stage,
            narrator=narrator,
            cast=cast,
            on_setup=on_setup,
            on_state_snapshot=on_setup,
            on_view_event=self.event_publisher.public,
            gate=self.step_gate,
            casting_service=casting_service,
        )

        return RunnerRuntimeState(
            script=script,
            initial_state_spec=initial_state_spec,
            player_names=player_names,
            cast=cast,
            state=state,
            director=director,
            casting_service=casting_service,
        )

    def _push_roles_from_state(self, state: State) -> dict[str, dict[str, Any]]:
        """从 State 读取角色快照并同步到 runtime seats / event store。

        夜晚刀人/毒人虽然会先写入 State.alive=False，但狼人杀前端只能在
        「夜晚结果公布」之后显示出局结果；否则 dashboard 会提前剧透。
        Night deaths are therefore masked for host/public role snapshots until
        the script has recorded GAME.night_deaths during the public death report.
        """
        assert self._state is not None, "runner state 不能为空"
        roles_map: dict[str, dict[str, Any]] = {}
        for name in self._state.player_names:
            role = state.get_attr(name, "role")
            real_alive = state.get_attr(name, "alive", True)
            visible_alive = self._visible_alive_for_dashboard(state, name, real_alive)
            if role:
                info = {"role": role, "alive": visible_alive}
                roles_map[name] = info
                if name in self.session_state.seats:
                    seat = self.session_state.seats[name]
                    seat.role_snapshot = role
                    seat.alive_snapshot = visible_alive
        if roles_map:
            self.event_publisher.host({"kind": "roles_snapshot", "roles": roles_map})
            # 不把状态快照重复写入玩家 private timeline。真人玩家的身份档案
            # 已由 HumanActorController.set_actor_profile -> send_profile_now 推送一次；
            # 后续角色/存活快照通过 player view 的 role_card 与 seats 展示即可。
            # Do not duplicate role snapshots into the player's private timeline.
            # The player profile is pushed once during setup; later snapshots are
            # represented by role_card/seats instead of repeated actor_profile events.
        return roles_map

    @staticmethod
    def _visible_alive_for_dashboard(state: State, actor_name: str, real_alive: bool) -> bool:
        """返回 dashboard 当前允许展示的存活状态。

        参数：
          state      — 当前游戏状态。
          actor_name — 玩家实体名。
          real_alive — State 中的真实 alive 值。

        返回：
          bool，供主持人 dashboard / 观战 UI 展示。夜晚 wolf/poison 死亡
          在 GAME.night_deaths 记录前保持为存活，避免提前展示刀人结果。
        """
        if real_alive:
            return True
        cause = state.get_attr(actor_name, "death_cause")
        if cause not in {"wolf", "poison"}:
            return False
        current_round = state.get_attr("GAME", "round") or 0
        death_round = state.get_attr(actor_name, "death_round")
        if death_round != current_round:
            return False
        night_deaths = state.get_attr("GAME", "night_deaths") or []
        if not isinstance(night_deaths, list):
            logger.warning(
                "[SocialDeductionGameRunner] GAME.night_deaths 类型异常：%s",
                type(night_deaths).__name__,
            )
            return True
        return actor_name not in night_deaths

    @staticmethod
    def _load_initial_state(script_path: str, params: dict[str, Any], compiler: YamlCompiler) -> dict[str, Any]:
        """读取 YAML initial_state。"""
        raw = Path(script_path).read_text(encoding="utf-8")
        raw = compiler._expand_params(raw, params)
        doc = yaml.safe_load(raw) or {}
        return doc.get("initial_state", {}) or {}


def _resolve_player_names(script: Any) -> list[str]:
    """从编译后的 Script 解析 seat 名称。"""
    player_config = getattr(script, "player_config", None)
    if player_config is not None and player_config.ids:
        return list(player_config.ids)
    role_counts = getattr(script.casting, "role_counts", None)
    if role_counts:
        total = sum(role_counts.values())
    else:
        total = 9
    return [f"Player_{index}" for index in range(1, total + 1)]


def make_session_id() -> str:
    """生成调试用 session id。"""
    return str(uuid.uuid4())


class BoardGameRunner(SocialDeductionGameRunner):
    """Fixed-flow runner specialization for board-game DSL scripts."""


class CardGameRunner(SocialDeductionGameRunner):
    """Fixed-flow runner specialization for card-game DSL scripts."""


class EconomyGameRunner(SocialDeductionGameRunner):
    """Fixed-flow runner specialization for economy-game DSL scripts."""
