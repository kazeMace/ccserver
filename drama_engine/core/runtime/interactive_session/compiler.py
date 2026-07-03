"""Compiler for interactive_session canonical runtime IR."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

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
        return InteractiveScript(
            meta=dict(canonical.get("meta") or {}),
            runtime=runtime,
            flow=flow,
            scenes=scenes,
            players=dict(canonical.get("players") or {}),
            state=dict(canonical.get("state") or canonical.get("initial_state") or {}),
            scopes=scopes,
            referee=referee,
            plugins=list(canonical.get("plugins") or []),
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

    def _compile_scene(self, scene_id: str, spec: dict[str, Any]) -> SceneSpec:
        """Compile one scene."""
        assert isinstance(spec, dict), "scene spec 必须是 dict"
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
        return ScheduleSpec(
            mode=str(spec.get("mode") or "none"),
            actor=spec.get("actor"),
            order=dict(spec.get("order") or {}),
            max_turns=int(spec.get("max_turns") or spec.get("rounds") or 1),
            max_rounds=int(spec.get("max_rounds") or spec.get("rounds") or 1),
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
        )

    def _explicit_evaluator_spec(self, spec: dict[str, Any]) -> dict[str, Any] | None:
        """Extract direct evaluator declaration from a referee-like object."""
        if "evaluator" not in spec:
            return None
        result = {key: value for key, value in spec.items() if key not in {"rules", "enabled", "check_on", "include", "exclude"}}
        return result

    def _assert_scene_refs(self, scene_ids: list[str], scenes: dict[str, SceneSpec]) -> None:
        """Ensure all flow scene ids exist."""
        for scene_id in scene_ids:
            assert scene_id in scenes, f"flow 引用了未定义 scene: {scene_id}"

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
