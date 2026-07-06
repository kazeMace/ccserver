"""Compiler for interactive_session canonical runtime IR."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

from drama_engine.core.dsl.registry import build_default_dsl_registry
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
        """Read and compile a YAML file."""
        assert yaml_path, "yaml_path 不能为空"
        raw_text = Path(yaml_path).read_text(encoding="utf-8")
        preliminary = yaml.safe_load(raw_text) or {}
        resolved_params = self._resolve_params(preliminary, params or {})
        expanded_text = self._expand_params(raw_text, resolved_params)
        doc = yaml.safe_load(expanded_text) or {}
        return self.compile_doc(doc)

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
        state = dict(canonical.get("state") or canonical.get("initial_state") or {})
        players = dict(canonical.get("players") or {})
        self._validate_script_contract(scenes, flow, scopes, referee)
        self._validate_visibility_refs(visibility, state, players)
        return InteractiveScript(
            meta=dict(canonical.get("meta") or {}),
            runtime=runtime,
            flow=flow,
            scenes=scenes,
            players=players,
            state=state,
            scopes=scopes,
            referee=referee,
            visibility=visibility,
            plugins=list(canonical.get("plugins") or []),
            game_pack=dict(canonical.get("game_pack") or {}),
            rule_set=dict(canonical.get("rule_set") or {}),
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
            hooks=dict(spec.get("hooks") or {}),
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
            evaluator=self._explicit_evaluator_spec(spec),
            result=spec.get("result"),
        )

    def _explicit_evaluator_spec(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """Extract direct evaluator declaration from a referee-like object."""
        if "evaluator" not in spec:
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
            "evaluator",
            "provider",
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
        if isinstance(order, dict) and (
            order.get("evaluator") or order.get("provider") or order.get("type")
        ):
            self._validate_service_provider(order, "schedule.order")
        detector = dynamic_spec.get("detector")
        if isinstance(detector, dict) and (
            detector.get("evaluator") or detector.get("provider") or detector.get("type") or detector.get("plugin")
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
        allowed = {
            "source",
            "field",
            "target_field",
            "type",
            "tie_policy",
            "runoff",
            "runoff_to",
            "runoff_scene",
            "values",
            "weight",
            "weights",
            "threshold",
            "top_k",
        }
        unknown = sorted(key for key in selection if key not in allowed)
        assert not unknown, f"scene {scene_id} resolution.selection 包含未知字段 {unknown}"
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

    def _validate_service_provider(self, spec: dict[str, Any], label: str) -> None:
        """Validate runtime-service provider names when present."""
        if not isinstance(spec, dict):
            return
        provider = spec.get("provider") or spec.get("evaluator") or spec.get("type")
        if provider is None and spec.get("plugin"):
            provider = "plugin"
        if provider is None:
            return
        allowed = {"builtin", "plugin", "inside", "http", "llm"}
        assert str(provider) in allowed, f"{label}.provider/evaluator 未知: {provider}"

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
