"""Compiler for interactive_session canonical runtime IR."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

from drama_engine.core.dsl.registry import build_default_dsl_registry
from drama_engine.core.components.effects import EffectExecutor
from drama_engine.core.moderation.models import GuardRailSpec
from drama_engine.core.runtime.interactive_session.models import (
    ControllerActionSpec,
    DynamicScheduleSpec,
    FlowSpec,
    FlowStateSpec,
    FlowTransitionSpec,
    InteractiveScript,
    ParticipantActionSpec,
    ParticipantsSpec,
    RefereeSpec,
    SceneSpec,
    ScheduleSpec,
    ScopeSpec,
    VisibilityPolicy,
)
from drama_engine.core.runtime.interactive_session.normalizer import (
    InteractiveSessionNormalizer,
)
from drama_engine.core.runtime_spec.registry import build_default_runtime_registry


class InteractiveSessionCompiler:
    """把 interactive_session YAML 编译为 runtime IR。"""

    def __init__(self) -> None:
        """初始化 compiler。"""
        self._normalizer = InteractiveSessionNormalizer()
        self._runtime_registry = build_default_runtime_registry()
        self._dsl_registry = build_default_dsl_registry()

    def compile(self, yaml_path: str, params: dict[str, Any] | None = None) -> InteractiveScript:
        """Read and compile a YAML file or package directory.

        支持两种模式：
          - 单文件: yaml_path 指向 .yaml 文件
          - 包目录: yaml_path 指向目录，内含 manifest.yaml + script.yaml + roles.yaml 等
        """
        assert yaml_path, "yaml_path 不能为空"
        path = Path(yaml_path)

        if path.is_dir():
            doc = self._load_package(path)
        else:
            raw_text = path.read_text(encoding="utf-8")
            preliminary = yaml.safe_load(raw_text) or {}
            resolved_params = self._resolve_params(preliminary, params or {})
            expanded_text = self._expand_params(raw_text, resolved_params)
            doc = yaml.safe_load(expanded_text) or {}

        return self.compile_doc(doc)

    def _load_package(self, pkg_dir: Path) -> dict[str, Any]:
        """加载包目录，合并各子文件为统一文档。

        包目录结构:
          manifest.yaml — meta / runtime / game_pack
          roles.yaml    — roles 定义
          script.yaml   — players / state / flow / scenes / referee / concepts 等

        所有文件合并为一个 dict 后交给 compile_doc 处理。
        """
        doc: dict[str, Any] = {}

        # 加载 manifest（必须存在）
        manifest_path = pkg_dir / "manifest.yaml"
        assert manifest_path.exists(), f"包目录缺少 manifest.yaml: {pkg_dir}"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        doc.update(manifest)

        # 加载 roles（可选）
        roles_path = pkg_dir / "roles.yaml"
        if roles_path.exists():
            roles_data = yaml.safe_load(roles_path.read_text(encoding="utf-8")) or {}
            if isinstance(roles_data, dict):
                doc.update(roles_data)
            elif isinstance(roles_data, list):
                doc["roles"] = roles_data

        # 加载 script（必须存在）
        script_path = pkg_dir / "script.yaml"
        assert script_path.exists(), f"包目录缺少 script.yaml: {pkg_dir}"
        script_data = yaml.safe_load(script_path.read_text(encoding="utf-8")) or {}
        doc.update(script_data)

        logger.info("[Compiler] 加载包目录: %s (keys=%s)", pkg_dir.name, list(doc.keys()))
        return doc

    def compile_doc(self, doc: dict[str, Any]) -> InteractiveScript:
        """Compile an already parsed document."""
        assert isinstance(doc, dict), "doc 必须是 dict"
        canonical = self._normalizer.normalize(doc)
        runtime = self._runtime_registry.parse_declaration(canonical.get("runtime"))
        scenes = {
            scene_id: self._compile_scene(scene_id, scene_spec)
            for scene_id, scene_spec in canonical["scenes"].items()
        }
        scopes = {
            scope_id: self._compile_scope(scope_spec)
            for scope_id, scope_spec in canonical.get("scopes", {}).items()
        }
        for scene in scenes.values():
            scopes.setdefault(scene.scope.id, scene.scope)
        flow = self._compile_flow(canonical["flow"], scenes)
        referee = self._compile_referee(canonical.get("referee") or {})
        visibility = self._compile_visibility(canonical.get("visibility") or {})
        guardrail = self._compile_guardrail(canonical.get("guardrail") or {})
        state = dict(canonical.get("state") or canonical.get("initial_state") or {})
        players = dict(canonical.get("players") or {})
        self._validate_script_contract(scenes, flow, scopes, referee)
        self._validate_visibility_refs(visibility, state, players)
        # 【H3 修复】在编译期校验所有 effect.type 是否已知，避免运行时才发现拼写错误。
        # 传递 canonical 以便检查是否有 plugins/game_pack 声明。
        self._validate_all_effects(scenes, flow, referee, canonical)
        roles = self._compile_roles(canonical.get("roles"))
        return InteractiveScript(
            meta=dict(canonical.get("meta") or {}),
            runtime=runtime,
            flow=flow,
            scenes=scenes,
            players=players,
            roles=roles,
            state=state,
            scopes=scopes,
            referee=referee,
            visibility=visibility,
            guardrail=guardrail,
            plugins=list(canonical.get("plugins") or []),
            game_pack=canonical.get("game_pack") or {},
            rule_set=canonical.get("rule_set") or {},
            raw=canonical,
        )

    def validate(self, doc: dict[str, Any]) -> list[str]:
        """Return validation errors without raising."""
        try:
            self.compile_doc(doc)
        except (AssertionError, ValueError, TypeError) as exc:
            return [str(exc)]
        return []

    def validate_file(self, yaml_path: str, params: dict[str, Any] | None = None) -> list[str]:
        """Validate one YAML file."""
        try:
            self.compile(yaml_path, params=params)
        except (AssertionError, ValueError, TypeError, yaml.YAMLError) as exc:
            return [str(exc)]
        return []

    def _compile_roles(self, roles: Any) -> list[dict[str, Any]]:
        """编译角色定义列表。

        顶层 roles: 可以是 list（每项一个角色 dict）或 dict（role_name → 详情）。
        统一编译成 list[dict]，每项保证含 name 字段。

        参数:
            roles: canonical 中的 roles 原始值

        返回:
            角色 dict 列表
        """
        if isinstance(roles, list):
            result = []
            for item in roles:
                if isinstance(item, dict) and item.get("name"):
                    result.append(dict(item))
            return result
        if isinstance(roles, dict):
            result = []
            for name, detail in roles.items():
                entry = dict(detail) if isinstance(detail, dict) else {}
                entry.setdefault("name", str(name))
                result.append(entry)
            return result
        return []

    def _compile_scope(self, spec: dict[str, Any]) -> ScopeSpec:
        """Compile scope spec."""
        return ScopeSpec(
            id=str(spec.get("id") or "public"),
            visibility=str(spec.get("visibility") or "public"),
            members=[str(item) for item in spec.get("members", []) or []],
        )

    def _compile_visibility(self, spec: dict[str, Any]) -> VisibilityPolicy:
        """Compile 顶层 visibility 块 → VisibilityPolicy。

        spec 为空 dict 时返回默认策略（无秘密，全部公开）。
        """
        assert isinstance(spec, dict), "visibility 块必须是 dict"
        return VisibilityPolicy(
            secret_attrs=[str(item) for item in spec.get("secret_attrs", []) or []],
            self_visible=[str(item) for item in spec.get("self_visible", []) or []],
        )

    def _compile_guardrail(self, spec: dict[str, Any]) -> GuardRailSpec:
        """Compile guardrail 块 → GuardRailSpec（全局或 scene 级共用）。

        spec 为空 dict 时返回默认（未启用）守卫。on_violation 的枚举校验在
        GuardRailSpec.__post_init__ 里做，拼写错误会在 validate 阶段被捕获。
        """
        assert isinstance(spec, dict), "guardrail 块必须是 dict"
        # executor 是字符串（标识 GuardRail 实现类型），config 是额外配置 dict
        raw_executor = spec.get("executor")
        if isinstance(raw_executor, dict):
            # 兼容旧 dict 形式：提取 executor 类型，其余作为 config
            executor_type = str(raw_executor.get("executor") or raw_executor.get("kind") or "llm")
            config = {k: v for k, v in raw_executor.items() if k not in ("executor", "kind")}
        elif isinstance(raw_executor, str):
            executor_type = raw_executor
            config = {}
        else:
            executor_type = "llm"
            config = {}
        return GuardRailSpec(
            enabled=bool(spec.get("enabled", False)),
            checks=[str(item) for item in spec.get("checks", []) or []],
            on_violation=str(spec.get("on_violation") or "soft_warn"),
            executor=executor_type,
            min_confidence=float(spec.get("min_confidence") or 0.0),
            config=config,
        )

    def _compile_scene(self, scene_id: str, spec: dict[str, Any]) -> SceneSpec:
        """Compile one scene."""
        assert isinstance(spec, dict), "scene spec 必须是 dict"
        self._validate_participants_spec(spec.get("participants") or {"static": []}, scene_id)
        self._validate_resolution_spec(spec.get("resolution") or {}, scene_id)
        self._validate_publication_spec(spec.get("publication") or {}, scene_id)
        return SceneSpec(
            id=str(spec.get("id") or scene_id),
            type=str(spec.get("type") or "scene"),
            scope=self._compile_scope(spec.get("scope") or {}),
            when=spec.get("when"),
            participants=ParticipantsSpec(spec=spec.get("participants") or {"static": []}),
            schedule=self._compile_schedule(spec.get("schedule") or {}),
            participant_action=self._compile_participant_action(spec.get("participant_action") or {}),
            controller_action=self._compile_controller_action(spec.get("controller_action") or {}),
            resolution=dict(spec.get("resolution") or {}),
            publication=dict(spec.get("publication") or {}),
            referee=self._compile_referee(spec.get("referee") or {}),
            guardrail=self._compile_guardrail(spec.get("guardrail") or {}),
            hooks=dict(spec.get("hooks") or {}),
            context=dict(spec.get("context") or {}),
            raw=dict(spec),
        )

    def _compile_schedule(self, spec: dict[str, Any]) -> ScheduleSpec:
        """Compile schedule spec."""
        dynamic_spec = spec.get("dynamic") if isinstance(spec.get("dynamic"), dict) else {}
        self._validate_schedule_spec(spec, dynamic_spec)
        return ScheduleSpec(
            mode=str(spec.get("mode") or "none"),
            actor=spec.get("actor"),
            order=dict(spec.get("order") or {}),
            planner=dict(spec.get("planner") or {}),
            opening=spec.get("opening") or spec.get("cue") or "",
            max_turns=self._int_from_keys(spec, ("max_turns", "rounds"), 1),
            max_rounds=self._int_from_keys(spec, ("max_rounds", "rounds"), 1),
            timeout_ms=spec.get("timeout_ms"),
            stop_when=spec.get("stop_when") or spec.get("until"),
            dynamic=DynamicScheduleSpec(
                enabled=bool(dynamic_spec.get("enabled", False)),
                check_on=str(dynamic_spec.get("check_on") or "after_message"),
                detector=dict(dynamic_spec.get("detector") or {}),
                allowed=dict(dynamic_spec.get("allowed") or {}),
                patch=dict(dynamic_spec.get("patch") or {}),
                merge_back=dict(dynamic_spec.get("merge_back") or {}),
            ),
        )

    def _compile_participant_action(self, spec: dict[str, Any]) -> ParticipantActionSpec:
        """Compile participant action spec."""
        return ParticipantActionSpec(
            kind=str(spec.get("kind") or "none"),
            target=str(spec.get("target") or "none"),
            candidates=spec.get("candidates"),
            response=dict(spec.get("response") or {}),
            cue=spec.get("cue") or "",
        )

    def _compile_controller_action(self, spec: dict[str, Any]) -> ControllerActionSpec:
        """Compile controller action spec."""
        self._validate_controller_action_spec(spec)
        return ControllerActionSpec(
            enabled=bool(spec.get("enabled", False)),
            controller=dict(spec.get("controller") or {"type": "none"}),
            kind=str(spec.get("kind") or "none"),
            choices=list(spec.get("choices") or []),
            free_input=dict(spec.get("free_input") or {}),
        )

    def _compile_flow(
        self,
        spec: dict[str, Any],
        scenes: dict[str, SceneSpec],
    ) -> FlowSpec:
        """Compile flow. Sequence becomes a one-state state machine."""
        flow_type = str(spec.get("type") or "sequence")
        if flow_type == "sequence":
            scene_ids = [str(item) for item in spec.get("scenes", [])]
            self._assert_scene_refs(scene_ids, scenes)
            state = FlowStateSpec(id="main", scenes=scene_ids, transitions=[], terminal=True)
            return FlowSpec(type="sequence", initial="main", states={"main": state})

        states: dict[str, FlowStateSpec] = {}
        for state_id, state_spec in (spec.get("states") or {}).items():
            scene_ids = [str(item) for item in state_spec.get("scenes", []) or []]
            self._assert_scene_refs(scene_ids, scenes)
            transitions = [
                FlowTransitionSpec(to=str(item.get("to")), when=item.get("when"))
                for item in state_spec.get("transitions", []) or []
                if isinstance(item, dict)
            ]
            states[str(state_id)] = FlowStateSpec(
                id=str(state_id),
                scenes=scene_ids,
                transitions=transitions,
                entry_effects=list(state_spec.get("entry_effects") or []),
                exit_effects=list(state_spec.get("exit_effects") or []),
                terminal=bool(state_spec.get("terminal", False)),
            )
        initial = str(spec.get("initial") or next(iter(states)))
        assert initial in states, f"flow.initial '{initial}' 不在 states 中"
        for state in states.values():
            for transition in state.transitions:
                assert transition.to in states, f"transition.to '{transition.to}' 不在 states 中"
        return FlowSpec(type="state_machine", initial=initial, states=states)

    def _compile_referee(self, spec: dict[str, Any]) -> RefereeSpec:
        """Compile referee spec."""
        check_on = spec.get("check_on") or ["after_scene"]
        if isinstance(check_on, str):
            check_on = [check_on]
        return RefereeSpec(
            enabled=bool(spec.get("enabled", False)),
            check_on=[str(item) for item in check_on],
            include=spec.get("include"),
            exclude=spec.get("exclude"),
            rules=list(spec.get("rules") or []),
            executor=self._explicit_executor_spec(spec),
            result=spec.get("result"),
        )

    def _explicit_executor_spec(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """Extract direct executor declaration from a referee-like object."""
        if "executor" not in spec:
            return None
        result = {
            key: value
            for key, value in spec.items()
            if key not in {"rules", "enabled", "check_on", "include", "exclude", "result"}
        }
        return result

    def _assert_scene_refs(self, scene_ids: list[str], scenes: dict[str, SceneSpec]) -> None:
        """Ensure all flow scene ids exist."""
        for scene_id in scene_ids:
            assert scene_id in scenes, f"flow 引用了未定义 scene: {scene_id}"

    def _int_from_keys(self, spec: dict[str, Any], keys: tuple[str, ...], default: int) -> int:
        """Read an integer field while preserving explicit zero for validation."""
        for key in keys:
            if key in spec and spec.get(key) is not None:
                return int(spec[key])
        return default

    def _validate_participants_spec(self, spec: Any, scene_id: str) -> None:
        """Validate participants selector shape for interactive_session."""
        if spec == "all":
            return
        if isinstance(spec, list):
            assert all(isinstance(item, str) for item in spec), (
                f"scene {scene_id} participants 列表必须只包含字符串"
            )
            return
        assert isinstance(spec, dict), f"scene {scene_id} participants 必须是 dict/list/all"
        allowed = {
            "static",
            "filter",
            "source",
            "where",
            "from_state",
            "from_state_set",
            "ordered",
            "order_by",
            "limit",
            "min",
            "executor",
            "plugin",
            "name",
            "id",
            "input",
            "fallback",
            "protocol",
            "envelope",
        }
        unknown = sorted(key for key in spec if key not in allowed)
        assert not unknown, f"scene {scene_id} participants 包含未知字段 {unknown}"
        if "static" in spec:
            assert isinstance(spec.get("static"), list), f"scene {scene_id} participants.static 必须是列表"
        if "filter" in spec:
            assert isinstance(spec.get("filter"), dict), f"scene {scene_id} participants.filter 必须是字典"
        if "where" in spec:
            assert isinstance(spec.get("where"), dict), f"scene {scene_id} participants.where 必须是条件字典"
        if "from_state" in spec:
            assert isinstance(spec.get("from_state"), str), f"scene {scene_id} participants.from_state 必须是字符串"
        if "from_state_set" in spec:
            assert isinstance(spec.get("from_state_set"), str), (
                f"scene {scene_id} participants.from_state_set 必须是字符串"
            )
        if "ordered" in spec:
            assert isinstance(spec.get("ordered"), bool), f"scene {scene_id} participants.ordered 必须是布尔值"
        if "limit" in spec:
            assert isinstance(spec.get("limit"), int) and spec.get("limit") > 0, (
                f"scene {scene_id} participants.limit 必须是正整数"
            )
        if "min" in spec:
            assert isinstance(spec.get("min"), int) and spec.get("min") >= 0, (
                f"scene {scene_id} participants.min 必须是非负整数"
            )
        self._validate_service_provider(spec, f"scene {scene_id} participants")

    def _validate_controller_action_spec(self, spec: dict[str, Any]) -> None:
        """Validate controller action details that dataclass cannot see."""
        free_input = spec.get("free_input") or {}
        if isinstance(free_input, dict) and "mode" in free_input:
            modes = {"choose_mapping", "branch_then_return", "constrained_continue", "free_continue", "grow_flow"}
            mode = str(free_input.get("mode") or "")
            assert mode in modes, f"未知 free_input.mode: {mode}"
        elif isinstance(free_input, dict) and free_input.get("enabled"):
            # 新语法：没有显式 mode，通过 mapper/generation 存在性推导
            # mapper 存在且无 generation → 等价 choose_mapping
            # 有 generation → 等价 grow_flow
            pass
        controller = spec.get("controller") or {}
        if isinstance(controller, dict):
            controller_type = str(controller.get("type") or "none")
            allowed = {"human", "agent", "system", "plugin", "none"}
            assert controller_type in allowed, f"未知 controller.type: {controller_type}"
            if controller_type == "plugin":
                self._validate_service_provider({"provider": "plugin", **controller}, "controller_action.controller")

    def _validate_schedule_spec(self, spec: dict[str, Any], dynamic_spec: dict[str, Any]) -> None:
        """Validate schedule service declarations."""
        planner = spec.get("planner")
        if isinstance(planner, dict):
            self._validate_service_provider(planner, "schedule.planner")
        order = spec.get("order")
        if isinstance(order, dict) and order.get("executor"):
            self._validate_service_provider(order, "schedule.order")
        detector = dynamic_spec.get("detector")
        if isinstance(detector, dict) and (
            detector.get("executor") or detector.get("plugin")
        ):
            self._validate_service_provider(detector, "schedule.dynamic.detector")
        merge_back = dynamic_spec.get("merge_back")
        if isinstance(merge_back, dict) and merge_back.get("to"):
            assert "." in str(merge_back.get("to")), "schedule.dynamic.merge_back.to 必须是 ENTITY.attr 格式"
        if isinstance(merge_back, dict) and str(merge_back.get("mode") or "summary") == "plugin":
            service = merge_back.get("plugin") or merge_back.get("service") or {"provider": "plugin", **merge_back}
            if isinstance(service, dict):
                self._validate_service_provider(service, "schedule.dynamic.merge_back")

    def _validate_resolution_spec(self, resolution: dict[str, Any], scene_id: str) -> None:
        """Validate resolution fields that affect runtime behavior."""
        assert isinstance(resolution, dict), f"scene {scene_id} resolution 必须是 dict"
        selection = resolution.get("selection")
        if selection is None:
            return
        assert isinstance(selection, dict), f"scene {scene_id} resolution.selection 必须是 dict"
        # 【M5 修复】只允许已实现的字段，拒绝 weight/threshold/top_k 等未实现的参数。
        # scene/executor.py:_selection 只实现了 plurality（简单多数决），未实现加权/阈值/top-k。
        allowed = {
            "source",       # 数据源：responses / controller
            "field",        # 读取字段（vote/target/action）
            "target_field", # field 的别名
            "type",         # 预留字段（当前未使用）
            "tie_policy",   # 平局策略：alphabetical/no_winner/all_tied/runoff
            "runoff",       # 是否允许 runoff（与 tie_policy=runoff 配合）
            "runoff_to",    # runoff 跳转目标（预留）
            "runoff_scene", # runoff 场景（预留）
            "values",       # 候选值列表（用于筛选）
        }
        # 未实现的字段（编译期拒绝，避免误导脚本作者）
        unimplemented = {
            "weight",    # 加权投票（未实现）
            "weights",   # 权重映射（未实现）
            "threshold", # 阈值要求（未实现）
            "top_k",     # 前 k 名（未实现）
        }
        for key in selection:
            if key in unimplemented:
                raise ValueError(
                    f"scene {scene_id} resolution.selection.{key} 尚未实现。\n"
                    f"当前 selection 只支持 plurality（简单多数决）+ tie_policy。\n"
                    f"如需 {key} 能力，请在 scene/executor.py:_selection 中实现。"
                )
            if key not in allowed:
                raise ValueError(
                    f"scene {scene_id} resolution.selection 包含未知字段: {key}"
                )
        tie_policy = selection.get("tie_policy")
        if tie_policy is not None:
            allowed_ties = {"alphabetical", "no_winner", "all_tied", "runoff"}
            assert tie_policy in allowed_ties, (
                f"scene {scene_id} resolution.selection.tie_policy 必须是 alphabetical/no_winner/all_tied/runoff"
            )

    def _validate_publication_spec(self, publication: dict[str, Any], scene_id: str) -> None:
        """Validate publication shape and registered view kinds."""
        assert isinstance(publication, dict), f"scene {scene_id} publication 必须是 dict"
        for field_name in ("messages", "disclosures", "views"):
            if field_name in publication:
                assert isinstance(publication.get(field_name), list), (
                    f"scene {scene_id} publication.{field_name} 必须是列表"
                )
        for index, view in enumerate(publication.get("views") or []):
            assert isinstance(view, dict), f"scene {scene_id} publication.views[{index}] 必须是 dict"
            assert view.get("id") or view.get("view_id"), f"scene {scene_id} publication.views[{index}] 缺少 id"
            kind = view.get("kind") or view.get("view_kind")
            assert isinstance(kind, str) and kind, f"scene {scene_id} publication.views[{index}] 缺少 kind"
            assert self._dsl_registry.has_view_kind(kind), (
                f"scene {scene_id} publication.views[{index}].kind '{kind}' 不合法"
            )

    def _validate_script_contract(
        self,
        scenes: dict[str, SceneSpec],
        flow: FlowSpec,
        scopes: dict[str, ScopeSpec],
        top_referee: RefereeSpec,
    ) -> None:
        """Validate cross-reference contracts after scenes and flow are compiled."""
        target_ids = set(scenes.keys()) | set(flow.states.keys())
        scope_ids = set(scopes.keys())
        for scene in scenes.values():
            self._validate_controller_targets(scene, target_ids)
            self._validate_resolution_targets(scene, target_ids)
            self._validate_referee_targets(scene.referee, target_ids, f"scene {scene.id}.referee")
            self._validate_publication_audiences(scene, scope_ids)
        self._validate_referee_targets(top_referee, target_ids, "referee")

    def _validate_controller_targets(self, scene: SceneSpec, target_ids: set[str]) -> None:
        """Ensure choice targets point at an executable scene or state."""
        for index, choice in enumerate(scene.controller_action.choices):
            if not isinstance(choice, dict):
                raise AssertionError(f"scene {scene.id} controller_action.choices[{index}] 必须是 dict")
            target = choice.get("to") or choice.get("scene") or choice.get("state")
            if target:
                assert str(target) in target_ids, (
                    f"scene {scene.id} controller_action.choices[{index}].to 引用了未知 flow target: {target}"
                )
        free_input = scene.controller_action.free_input or {}
        if isinstance(free_input, dict):
            self._validate_return_to_target(scene.id, free_input.get("return_to"), target_ids)

    def _validate_return_to_target(
        self,
        scene_id: str,
        return_to: Any,
        target_ids: set[str],
    ) -> None:
        """Ensure branch_then_return return target is static and known when declared."""
        if not return_to:
            return
        if isinstance(return_to, str):
            target = return_to
        elif isinstance(return_to, dict):
            target = return_to.get("id") or return_to.get("scene") or return_to.get("state") or return_to.get("to")
        else:
            raise AssertionError(f"scene {scene_id} controller_action.free_input.return_to 必须是字符串或字典")
        if target:
            assert str(target) in target_ids, (
                f"scene {scene_id} controller_action.free_input.return_to 引用了未知 flow target: {target}"
            )

    def _validate_resolution_targets(self, scene: SceneSpec, target_ids: set[str]) -> None:
        """Validate resolution-driven flow targets."""
        resolution = scene.resolution or {}
        selection = resolution.get("selection")
        if not isinstance(selection, dict):
            return
        runoff = selection.get("runoff")
        target = None
        if isinstance(runoff, dict):
            target = runoff.get("to") or runoff.get("scene") or runoff.get("state")
        target = target or selection.get("runoff_to") or selection.get("runoff_scene")
        if target:
            assert str(target) in target_ids, (
                f"scene {scene.id} resolution.selection.runoff.to 引用了未知 flow target: {target}"
            )

    def _validate_referee_targets(self, referee: RefereeSpec, target_ids: set[str], label: str) -> None:
        """Validate referee result jump targets where they are static strings."""
        for index, rule in enumerate(referee.rules):
            if not isinstance(rule, dict):
                raise AssertionError(f"{label}.rules[{index}] 必须是 dict")
            result = rule.get("result")
            if isinstance(result, dict):
                target = result.get("jump") or result.get("to")
                if target:
                    assert str(target) in target_ids, f"{label}.rules[{index}].result.to 未定义: {target}"
        if isinstance(referee.result, dict):
            target = referee.result.get("jump") or referee.result.get("to")
            if target:
                assert str(target) in target_ids, f"{label}.result.to 未定义: {target}"

    def _validate_publication_audiences(self, scene: SceneSpec, scope_ids: set[str]) -> None:
        """Validate string/scope publication audiences."""
        publication = scene.publication or {}
        for field_name in ("messages", "disclosures", "views"):
            for index, item in enumerate(publication.get(field_name) or []):
                if not isinstance(item, dict):
                    continue
                audience = item.get("audience") or item.get("scope")
                scope_name = None
                if isinstance(audience, str):
                    scope_name = audience
                elif isinstance(audience, dict):
                    scope_name = audience.get("scope") or audience.get("id")
                if scope_name:
                    assert str(scope_name) in scope_ids, (
                        f"scene {scene.id} publication.{field_name}[{index}] 引用了未知 scope: {scope_name}"
                    )

    def _validate_visibility_refs(
        self,
        visibility: VisibilityPolicy,
        state: dict[str, Any],
        players: dict[str, Any],
    ) -> None:
        """校验 visibility.secret_attrs 引用的属性名是否出现在静态声明里。

        采用「软校验」（记警告而非报错）：因为 role/faction 这类属性有时由
        casting / game_pack 在运行时动态分配，静态声明里查不到并不一定是错误。
        但对于把 secret_attrs 拼错（如 rolee）的常见失误，这里能给出明确提示。
        """
        # 收集所有静态出现过的属性名：state 块每个实体的属性键 + players.initial_attrs 键。
        known_attrs: set[str] = set()
        for entity_attrs in state.values():
            if isinstance(entity_attrs, dict):
                known_attrs.update(str(key) for key in entity_attrs.keys())
        initial_attrs = players.get("initial_attrs")
        if isinstance(initial_attrs, dict):
            known_attrs.update(str(key) for key in initial_attrs.keys())

        # 如果整个脚本没有任何静态属性声明，说明属性完全靠运行时构建，跳过校验避免误报。
        if not known_attrs:
            return
        for attr in visibility.secret_attrs:
            if attr not in known_attrs:
                logger.warning(
                    "visibility.secret_attrs 声明的属性 '%s' 未在 state/players.initial_attrs 中静态出现，"
                    "请确认它是运行时动态分配的属性，而非拼写错误。",
                    attr,
                )

    def _validate_all_effects(
        self,
        scenes: dict[str, SceneSpec],
        flow: FlowSpec,
        referee: RefereeSpec,
        canonical: dict[str, Any],
    ) -> None:
        """在编译期校验所有 effect.type，避免拼写错误在运行时才炸。

        校验范围（含嵌套）：
          1. flow state 的 entry_effects / exit_effects
          2. scene resolution 的 effects
          3. referee rules（含 top-level 与 scene 级 referee）的 effects
          4. 上述 effect 内部的嵌套 effect（for_each.effects / pending_resolve.effects）

        白名单 = 内置 effect ∪ 脚本声明的 game_pack/rule_set 提供的机制名。因此声明了
        builtin.social 的脚本里 tally_votes/eliminate 合法，但拼错的 tally_vote 仍会被抓。

        lenient 仅在脚本声明了 `plugins:`（自定义 Python 插件，编译期无法静态解析其 effect 名）
        时开启——此时未知类型只告警不报错。只声明 game_pack/rule_set 时不进入 lenient，
        因为机制名可以从注册表静态解析、typo 应当被拒绝。
        """
        allowed_types = self._resolve_allowed_effect_types(canonical)
        # 只有声明了无法静态解析的自定义 plugins 时才宽容；game_pack/rule_set 可解析，不宽容。
        lenient = bool(canonical.get("plugins"))

        for state_id, state in flow.states.items():
            for effect in state.entry_effects:
                self._validate_single_effect(
                    effect, f"flow.states.{state_id}.entry_effects", allowed_types, lenient
                )
            for effect in state.exit_effects:
                self._validate_single_effect(
                    effect, f"flow.states.{state_id}.exit_effects", allowed_types, lenient
                )

        for scene_id, scene in scenes.items():
            resolution = scene.resolution
            if isinstance(resolution, dict):
                for effect in resolution.get("effects", []) or []:
                    self._validate_single_effect(
                        effect, f"scene.{scene_id}.resolution.effects", allowed_types, lenient
                    )
            # scene 级 referee 的 effects 同样校验（scene.referee.rules[].effects）。
            for r_index, rule in enumerate(scene.referee.rules):
                if isinstance(rule, dict):
                    for effect in (rule.get("result") or {}).get("effects", []) or []:
                        self._validate_single_effect(
                            effect, f"scene.{scene_id}.referee.rules[{r_index}].result.effects",
                            allowed_types, lenient,
                        )

        self._validate_referee_effects(referee, "referee", allowed_types, lenient)

    def _validate_referee_effects(
        self,
        referee: RefereeSpec,
        label: str,
        allowed_types: frozenset[str],
        lenient: bool,
    ) -> None:
        """校验一个 referee 的 rules[].result.effects。"""
        for index, rule in enumerate(referee.rules):
            if isinstance(rule, dict):
                for effect in (rule.get("result") or {}).get("effects", []) or []:
                    self._validate_single_effect(
                        effect, f"{label}.rules[{index}].result.effects", allowed_types, lenient
                    )

    def _resolve_allowed_effect_types(self, canonical: dict[str, Any]) -> frozenset[str]:
        """合并「内置 effect」与「脚本声明的 game_pack/rule_set 机制名」为白名单。

        机制名来自 GamePackRuntimeRegistry 的 manifest.mechanisms（effect + condition 混合，
        对 effect 校验宁可放宽也不误伤）。未知/未注册的 pack id 忽略（其存在性由 runner
        的 assert 负责，这里只负责 effect 名白名单）。
        """
        allowed = set(EffectExecutor.BUILTIN_EFFECT_TYPES)
        from drama_engine.core.game_packs import build_default_game_pack_runtime_registry

        registry = build_default_game_pack_runtime_registry()
        for source in (canonical.get("game_pack"), canonical.get("rule_set")):
            for spec in self._normalize_pack_specs(source):
                plugin_id = spec.get("plugin")
                if plugin_id and registry.has(plugin_id):
                    allowed.update(registry.get(plugin_id).mechanisms)
        return frozenset(allowed)

    def _normalize_pack_specs(self, source: Any) -> list[dict[str, Any]]:
        """把 game_pack / rule_set 声明归一为 spec 列表（单个 dict / 列表 / 字符串）。"""
        if source is None:
            return []
        items = source if isinstance(source, list) else [source]
        specs: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, str) and item:
                specs.append({"plugin": item})
            elif isinstance(item, dict) and item.get("plugin"):
                specs.append(item)
        return specs

    def _validate_single_effect(
        self,
        effect: Any,
        location: str,
        allowed_types: frozenset[str],
        lenient: bool,
    ) -> None:
        """校验单个 effect 的 type，并递归校验其嵌套 effect（for_each/pending_resolve）。"""
        if not isinstance(effect, dict):
            return
        effect_type = effect.get("type")
        if not effect_type:
            raise ValueError(f"{location} 包含缺少 type 字段的 effect: {effect}")

        if effect_type not in allowed_types:
            if lenient:
                logger.debug(
                    "%s 含非内置 effect.type '%s'，脚本声明了 plugins，留待运行时校验",
                    location, effect_type,
                )
            else:
                raise ValueError(
                    f"{location} 包含未知的 effect.type: '{effect_type}'。\n"
                    f"合法类型：{', '.join(sorted(allowed_types))}。\n"
                    f"若为自定义 effect，请在 plugins: 块声明；若为机制 effect，请在 game_pack/rule_set 声明对应包。"
                )

        # 递归校验嵌套子 effect（for_each.effects / pending_resolve.effects）。
        for child in effect.get("effects", []) or []:
            self._validate_single_effect(child, f"{location}[{effect_type}].effects", allowed_types, lenient)

    def _validate_service_provider(self, spec: dict[str, Any], label: str) -> None:
        """Validate runtime-service provider names when present."""
        if not isinstance(spec, dict):
            return
        executor = spec.get("executor")
        if executor is None and spec.get("plugin"):
            executor = "plugin"
        if executor is None:
            return
        allowed = {"builtin", "plugin", "http", "llm", "code"}
        assert str(executor) in allowed, f"{label}.executor 未知: {executor}"

    def _resolve_params(self, doc: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """Resolve params defaults and overrides."""
        result: dict[str, Any] = {}
        for param_def in doc.get("params", []) if isinstance(doc, dict) else []:
            if isinstance(param_def, dict) and param_def.get("name"):
                result[str(param_def["name"])] = param_def.get("default")
        result.update(override)
        return result

    def _expand_params(self, raw_text: str, params: dict[str, Any]) -> str:
        """Replace {{param}} placeholders in YAML text."""
        def replace(match: re.Match) -> str:
            name = match.group(1).strip()
            if name not in params:
                return match.group(0)
            return str(params[name])

        return re.sub(r"\{\{\s*([^}]+)\s*\}\}", replace, raw_text)
