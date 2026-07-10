"""Runner for runtime.type=interactive_session."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from drama_engine.core.components import CandidateResolver, ConditionEvaluator, EffectExecutor, ValueResolver
from drama_engine.core.plugins import build_default_plugin_registry
from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.executor import build_executor_registry
from drama_engine.core.ports.memory import configure_runtime_memory_backend
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime.interactive_session.context import (
    InteractiveExecutionContext,
    RuntimeEmitters,
    RuntimeServices,
)
from drama_engine.core.runtime.interactive_session.flow.executor import FlowExecutor
from drama_engine.core.runtime.interactive_session.patch.applier import FlowPatchApplier
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal
from drama_engine.core.runtime.interactive_session.patch.materializer import FlowMaterializer
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator
from drama_engine.core.runtime.interactive_session.services.plugin_loader import InteractivePluginLoader
from drama_engine.core.runtime_spec.registry import RuntimeSpec
from drama_engine.core.game_instance.progress import ProgressTracker
from drama_engine.core.game_instance.state import SESSION_ASSIGNED, SESSION_ENDED, SESSION_RUNNING
from drama_engine.core.script_loader import ScriptBundle, ScriptLoader
from drama_engine.core.visibility.disclosure_ledger import DisclosureLedger

logger = logging.getLogger(__name__)


class InteractiveSessionRunner(BasicGameRunner):
    """Runnable interactive_session execution model."""

    def __init__(
        self,
        runtime: Any,
        declaration: RuntimeSpec,
        dry_run: bool = True,
    ) -> None:
        """Initialize runner."""
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration, dry_run=dry_run)
        self._loader = ScriptLoader()
        self._bundle: ScriptBundle | None = None
        self._script = None
        self._ctx: InteractiveExecutionContext | None = None
        self._flow_executor = FlowExecutor()

    async def assign(self) -> None:
        """Compile script, create cast, and initialize runtime state."""
        session_state = self.session_state
        assert session_state.status == "lobby", (
            f"只有 lobby 状态可以 assign，当前: {session_state.status}"
        )
        config = self.config_parser.runtime_config(
            script_path=session_state.script_path,
            declaration=self.declaration,
        )
        configure_runtime_memory_backend(self.context.memory_store, config)

        # 通过 ScriptLoader 加载 ScriptBundle，再取编译产物
        bundle = await self._loader.load(
            Path(session_state.script_path),
            params=session_state.params,
        )
        self._bundle = bundle
        script = bundle.compiled
        self._script = script
        player_names = self._resolve_player_names(script)
        self.input_bridge.create_cast(
            actor_runtime=self.context.actor_runtime,
            player_names=player_names,
            human_seat_ids=set(getattr(session_state, "human_seat_ids", set())),
            action_service=self.runtime.action_service,
            dry_run=self.dry_run,
            step_gate=self.step_gate,
        )
        state = self._build_state(script, player_names)
        plugins = build_default_plugin_registry()
        InteractivePluginLoader().load(plugins, script.plugins)
        # 加载 plugins/ 目录扫描到的插件
        if bundle.plugin_specs:
            InteractivePluginLoader().load_from_specs(plugins, bundle.plugin_specs)
        # 安装 DSL 声明的 GamePack（机制集合）：把其机制注册进 plugin registry，
        # 并把默认 config 与 DSL config 合并后写入 GAME 状态。
        self._install_game_pack(script, plugins, state)
        executor_registry = build_executor_registry(session_state.metadata, plugins)
        evaluator = ConditionEvaluator(plugins, executor_registry=executor_registry)

        # 加载 hooks/ 目录扫描到的钩子
        hook_runner = None
        if bundle.hook_specs:
            from drama_engine.core.runtime.interactive_session.services.hook_runner import HookRunner
            hook_runner = HookRunner()
            hook_runner.load_from_specs(bundle.hook_specs)

        # 构建分层服务依赖
        services = RuntimeServices(
            condition_evaluator=evaluator,
            effect_executor=EffectExecutor(evaluator, plugins),
            candidate_resolver=CandidateResolver(evaluator),
            value_resolver=ValueResolver(plugins),
            executor_registry=executor_registry,
            plugin_registry=plugins,
        )
        emitters = RuntimeEmitters(
            emit_public=self._emit_public,
            emit_host=self._emit_host,
            emit_private=self._emit_private,
        )

        ctx = InteractiveExecutionContext(
            script=script,
            services=services,
            emitters=emitters,
            state=state,
            writer=StateWriter(state),
            cast=self.context.actor_runtime.cast,
            patch_journal=PatchJournal(),
            session_metadata=session_state.metadata,
            disclosure_ledger=DisclosureLedger(),
            base_raw=deepcopy(script.raw),
            on_progress=ProgressTracker(session_state).record_progress,
            hook_runner=hook_runner,
            on_persist=self._persist_patches,
        )
        self._ctx = ctx
        session_state.metadata["human_seat_ids"] = list(getattr(session_state, "human_seat_ids", set()))
        session_state.metadata["runtime_type"] = "interactive_session"
        session_state.metadata["dry_run"] = self.dry_run
        session_state.metadata["project_root"] = str(getattr(self.context, "project_root", "") or "")
        session_state.metadata["interactive_session"] = {
            "flow_type": script.flow.type,
            "scene_count": len(script.scenes),
            "players": player_names,
            "base_flow": deepcopy(script.raw),
        }
        session_state.set_status(SESSION_ASSIGNED)
        self._emit_public({"kind": "session_assigned", "runtime_type": "interactive_session"})
        logger.info("[InteractiveSessionRunner] assign 完成 session=%s", session_state.session_id)

    async def start(self) -> None:
        """Start the interactive session flow."""
        session_state = self.session_state
        assert session_state.status == SESSION_ASSIGNED, (
            f"只有 assigned 状态可以 start，当前: {session_state.status}"
        )
        assert self._ctx is not None, "start 前必须先 assign"
        session_state.set_status(SESSION_RUNNING)
        self._emit_public({"kind": "session_started", "runtime_type": "interactive_session"})
        # 触发 on_session_start hook
        await self._trigger_hook("on_session_start")
        self.runtime_state.task = asyncio.create_task(self._run_flow())

    async def cancel_task(self) -> None:
        """仅取消正在运行的 flow task，保留 script/ctx（用于回滚前暂停）。

        与 reset_runtime_state 不同，本方法不清空 ctx，因此回滚后仍可继续在同一
        InteractiveExecutionContext（含 game_state、patch_journal）上恢复执行。
        """
        runtime_state = self.runtime_state
        if runtime_state.task is not None and not runtime_state.task.done():
            runtime_state.task.cancel()
            try:
                await runtime_state.task
            except asyncio.CancelledError:
                pass
        runtime_state.task = None

    async def reset_runtime_state(self) -> None:
        """Cancel current task and clear transient runtime state."""
        await self.cancel_task()
        self._bundle = None
        self._script = None
        self._ctx = None

    @property
    def game_state(self) -> Any:
        """返回当前游戏事实状态 engine.State；未 assign 时为 None。

        供 GameInstance 的 SnapshotManager/RollbackManager 采集 GameState 快照。
        """
        return self._ctx.state if self._ctx is not None else None

    @property
    def patch_journal(self) -> Any:
        """返回当前 patch journal；未 assign 时为 None。"""
        return self._ctx.patch_journal if self._ctx is not None else None

    def apply_flow_patch(self, patch: dict[str, Any]) -> dict[str, Any]:
        """应用一个外部（如 ControlPlane 提案）flow patch 到运行时（M4）。

        接通 ControlPlane 与执行层：director/writer 提案通过裁定后，经此入口真正驱动 flow。
        复用与 grow_flow 相同的安全路径——先校验+dry-run 预览，再以 record type "flow_patch"
        入账，最后 materialize 生效；失败回滚 journal record，保证不留下污染。

        参数：patch — flow patch dict，含 type: add_scene | add_transition | set_state。
        返回：{"applied": True, "flow_patch": patch}。
        """
        assert self._ctx is not None, "apply_flow_patch 前必须先 assign"
        ctx = self._ctx
        errors = PatchValidator().validate_flow_patch(patch, ctx.script)
        assert not errors, f"flow_patch 校验失败: {errors}"
        applier = FlowPatchApplier()
        applier.preview(ctx, patch)  # dry-run 编译，确认可合成
        record = ctx.patch_journal.append("flow_patch", patch, {"source": "control_plane"})
        try:
            applier.apply(ctx, patch)
        except Exception:
            removed = ctx.patch_journal.rollback_last()
            assert removed is not None and removed.patch_id == record.patch_id, "patch journal 回滚顺序错误"
            raise
        return {"applied": True, "flow_patch": patch}

    @property
    def disclosure_ledger(self) -> Any:
        """返回当前披露账本；未 assign 时为 None。

        供 GameInstance 的 SnapshotManager/RollbackManager 采集披露快照，
        以及 project_context 合成 actor view 的已披露事实。
        """
        return self._ctx.disclosure_ledger if self._ctx is not None else None

    def summary(self, audience: str, seat_id: str | None = None) -> dict[str, Any]:
        """Return current summary for host/player views."""
        base = super().summary(audience, seat_id)
        if self._ctx is not None:
            base["interactive_session"] = {
                "current_state": self._ctx.current_state_id,
                "current_scene": self._ctx.current_scene_id,
                "patches": self._ctx.patch_journal.snapshot(),
                "base_flow": deepcopy(self._ctx.base_raw),
                "materialized_flow": FlowMaterializer().materialize(
                    self._ctx.script,
                    self._ctx.patch_journal,
                    self._ctx.base_raw,
                ),
            }
        return base

    async def _run_flow(self) -> None:
        """Run flow in background task."""
        assert self._ctx is not None, "interactive context 不能为空"
        try:
            result = await self._flow_executor.execute(self._ctx)
            # 触发 on_session_end hook
            await self._trigger_hook("on_session_end", {"result": result})
            self.session_state.metadata["interactive_session"]["result"] = result
            self.session_state.metadata["interactive_session"]["patches"] = self._ctx.patch_journal.snapshot()
            self.session_state.set_status(SESSION_ENDED)
            self._emit_public({
                "kind": "session_ended",
                "runtime_type": "interactive_session",
                "result": result,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - runner must surface failures.
            self.session_state.set_status("failed")
            self._emit_host({"kind": "session_failed", "runtime_type": "interactive_session", "error": str(exc)})
            logger.exception("[InteractiveSessionRunner] run_flow 失败")

    def _persist_patches(self) -> None:
        """即时持久化 patch journal 到 session metadata。

        由 FlowGrower.grow() 成功后调用，确保生成的场景不因进程崩溃丢失。
        """
        if self._ctx is None:
            return
        self.session_state.metadata.setdefault("interactive_session", {})
        self.session_state.metadata["interactive_session"]["patches"] = self._ctx.patch_journal.snapshot()
        logger.debug("[InteractiveSessionRunner] patches 已持久化, count=%d", len(self._ctx.patch_journal.snapshot()))

    def _resolve_player_names(self, script: Any) -> list[str]:
        """Resolve player/agent seat names."""
        players = script.players or {}
        ids = players.get("ids") if isinstance(players, dict) else None
        if isinstance(ids, list) and ids:
            return [str(item) for item in ids]
        count = int(players.get("count") or 0) if isinstance(players, dict) else 0
        if count > 0:
            return [f"Player_{index}" for index in range(1, count + 1)]
        if self.session_state.seat_ids:
            return [str(item) for item in self.session_state.seat_ids]
        names = set()
        for scene in script.scenes.values():
            spec = scene.participants.spec
            if isinstance(spec, dict) and isinstance(spec.get("static"), list):
                names.update(str(item) for item in spec["static"])
            elif isinstance(spec, list):
                names.update(str(item) for item in spec)
        return sorted(names) or ["Player_1"]

    def _build_state(self, script: Any, player_names: list[str]) -> State:
        """Build open runtime State."""
        vocab = Vocabulary(
            roles=frozenset(),
            factions=frozenset(),
            scopes=frozenset(script.scopes.keys()),
            abilities=frozenset(),
        )
        state = State(vocab)
        state.register_entity("GAME", {"round": 1, "players": list(player_names), "ended": False})
        state.register_entity("STORY", {})
        state.register_entity("SCENE", {})

        # 把 DSL roles 信息存入 GAME.roles（供 actor 读取人设）
        if hasattr(script, 'raw') and isinstance(script.raw, dict):
            roles_list = script.raw.get('roles', [])
            if roles_list:
                roles_data = {}
                for role_spec in roles_list:
                    if isinstance(role_spec, dict) and role_spec.get('name'):
                        role_name = role_spec['name']
                        roles_data[role_name] = {
                            "name": role_name,
                            "display_name": role_spec.get("display_name", ""),
                            "description": role_spec.get("description", "") or role_spec.get("brief", ""),
                            "portrait_url": role_spec.get("portrait_url", ""),
                            "emoji": role_spec.get("emoji", ""),
                            "voice_id": role_spec.get("voice_id", ""),
                            "tts_config": role_spec.get("tts_config", {}),
                            "faction": role_spec.get("faction", ""),
                        }
                StateWriter(state).apply(SetAttr("GAME", "roles", roles_data))

        for entity, attrs in (script.state or {}).items():
            if not state.has_entity(str(entity)):
                state.register_entity(str(entity), )
            for key, value in (attrs or {}).items():
                StateWriter(state).apply(SetAttr(str(entity), str(key), value))
        player_initial = {}
        if isinstance(script.players, dict):
            player_initial = dict(script.players.get("initial_attrs") or {})
        player_initial.setdefault("alive", True)
        for name in player_names:
            if not state.has_entity(name):
                state.register_entity(name, dict(player_initial))
                continue
            # 玩家可能已在 state 块中声明（如按座位分配 role）；此处补齐缺失的初始属性，
            # 保证 alive 等默认值不会因显式声明 role 而丢失。
            for key, value in player_initial.items():
                if state.get_attr(name, key) is None:
                    StateWriter(state).apply(SetAttr(name, str(key), value))
        return state

    def _normalize_pack_specs(self, source: Any) -> list[dict]:
        """把 game_pack / rule_set 声明归一为 spec 列表。

        支持三种写法：
          - 单个 dict：{plugin: ..., config: ...}
          - dict 列表：[{plugin: ...}, {plugin: ...}]
          - 字符串：直接是 plugin id
        无 plugin 的项会被忽略。
        """
        if source is None:
            return []
        items = source if isinstance(source, list) else [source]
        specs: list[dict] = []
        for item in items:
            if isinstance(item, str) and item:
                specs.append({"plugin": item})
            elif isinstance(item, dict) and item.get("plugin"):
                specs.append(item)
        return specs

    def _install_game_pack(self, script: Any, plugins: Any, state: State) -> None:
        """安装 DSL 声明的 GamePack / RuleSet 机制集合。

        - 读取 script.game_pack.plugin（或 rule_set.plugin）。
        - 从运行层 GamePack 注册表取 manifest，把其机制注册进 plugin registry。
        - 把 manifest 默认 config 与 DSL config 合并后写入 GAME.<key>，供机制读取。
        无声明时直接返回（纯剧情/社交推理脚本零关联）。
        """
        from drama_engine.core.plugins import PluginApi
        from drama_engine.core.game_packs import build_default_game_pack_runtime_registry

        # game_pack / rule_set 都可能声明机制集合，且各自都支持单个或列表形式，
        # 因此一个脚本可以引入任意多个机制集合（例如 RPG = dice + inventory + stats）。
        specs: list[dict] = []
        for source in (script.game_pack, script.rule_set):
            specs.extend(self._normalize_pack_specs(source))
        if not specs:
            return
        registry = build_default_game_pack_runtime_registry()
        api = PluginApi(plugins)
        writer = StateWriter(state)
        for spec in specs:
            plugin_id = spec["plugin"]
            assert registry.has(plugin_id), f"未知 game_pack/rule_set: {plugin_id}"
            default_config = registry.install(plugin_id, api)
            merged_config = dict(default_config)
            merged_config.update(dict(spec.get("config") or {}))
            # 把 config 写入 GAME，机制通过 state.get_attr("GAME", key) 读取。
            for key, value in merged_config.items():
                writer.apply(SetAttr("GAME", str(key), value))
            logger.info(
                "[InteractiveSessionRunner] 安装 game_pack=%s config_keys=%s",
                plugin_id,
                sorted(merged_config.keys()),
            )

    def _emit_public(self, event: dict[str, Any]) -> None:
        """Publish event to public stream (host timeline 由 append_public 自动包含)."""
        self.event_publisher.public(dict(event))

    def _emit_host(self, event: dict[str, Any]) -> None:
        """Publish host-only event."""
        self.event_publisher.host(dict(event))

    def _emit_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """Publish one private seat event."""
        self.event_publisher.private(seat_id, dict(event))

    async def _trigger_hook(self, event: str, payload: dict[str, Any] | None = None) -> None:
        """触发指定事件的 hook（若 hook_runner 已挂载）。"""
        if self._ctx is None or self._ctx.hook_runner is None:
            return
        from drama_engine.core.runtime.interactive_session.services.hook_runner import HookContext
        ctx = HookContext(
            event=event,
            payload=payload or {},
            state=self._ctx.state,
            writer=self._ctx.writer,
            cast=self._ctx.cast,
            session_metadata=self._ctx.session_metadata,
            scene_id=self._ctx.current_scene_id,
        )
        await self._ctx.hook_runner.trigger(event, ctx)


InteractiveSessionExecutionModel = InteractiveSessionRunner

__all__ = ["InteractiveSessionExecutionModel", "InteractiveSessionRunner"]
