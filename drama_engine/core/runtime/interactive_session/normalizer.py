"""Normalize new and legacy DSL into interactive_session canonical syntax."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class InteractiveSessionNormalizer:
    """把 raw YAML 转成新版 canonical DSL。

    Legacy support is intentionally concentrated here. Executors should only
    see canonical fields.
    """

    def normalize(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Return a canonical copy of the input document."""
        assert isinstance(doc, dict), "interactive_session DSL 顶层必须是 dict"
        result = deepcopy(doc)
        result.setdefault("runtime", {"type": "interactive_session"})
        result["runtime"] = self._normalize_runtime(result.get("runtime"))
        result["scenes"] = self._normalize_scenes(result)
        result["flow"] = self._normalize_flow(result.get("flow") or {}, result["scenes"])
        result["referee"] = self._normalize_referee(result.get("referee") or {})
        result["scopes"] = self._normalize_top_scopes(result)
        return result

    def _normalize_runtime(self, runtime: Any) -> dict[str, Any]:
        """Normalize runtime declaration."""
        if isinstance(runtime, str):
            return {"type": runtime}
        if isinstance(runtime, dict):
            value = dict(runtime)
            value.setdefault("type", "interactive_session")
            return value
        return {"type": "interactive_session"}

    def _normalize_scenes(self, doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalize top-level and embedded scenes into a dict keyed by scene id."""
        scenes: dict[str, dict[str, Any]] = {}
        top_scenes = doc.get("scenes") or {}
        if isinstance(top_scenes, dict):
            for scene_id, scene_spec in top_scenes.items():
                scene = dict(scene_spec or {})
                scene.setdefault("id", str(scene_id))
                scenes[str(scene_id)] = self._normalize_scene(scene)
        elif isinstance(top_scenes, list):
            for item in top_scenes:
                if isinstance(item, dict):
                    scene = dict(item)
                    scene_id = str(scene.get("id") or scene.get("name"))
                    scene["id"] = scene_id
                    scenes[scene_id] = self._normalize_scene(scene)

        flow = doc.get("flow") or {}
        for scene in self._embedded_flow_scenes(flow):
            scene_id = str(scene.get("id") or scene.get("name"))
            if scene_id and scene_id not in scenes:
                scene["id"] = scene_id
                scenes[scene_id] = self._normalize_scene(scene)
        assert scenes, "interactive_session 需要 scenes 定义或 flow 内嵌 scene"
        return scenes

    def _embedded_flow_scenes(self, flow: Any) -> list[dict[str, Any]]:
        """Collect legacy embedded scene dictionaries from flow."""
        if not isinstance(flow, dict):
            return []
        result = []
        if isinstance(flow.get("scenes"), list):
            result.extend(item for item in flow["scenes"] if isinstance(item, dict))
        states = flow.get("states")
        if isinstance(states, dict):
            for state_spec in states.values():
                scenes = state_spec.get("scenes") if isinstance(state_spec, dict) else []
                if isinstance(scenes, list):
                    result.extend(item for item in scenes if isinstance(item, dict))
        return result

    def _normalize_flow(
        self,
        flow: dict[str, Any],
        scenes: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Normalize flow. Sequence is lowered to one state later by compiler."""
        assert isinstance(flow, dict), "flow 必须是 dict"
        flow_type = str(flow.get("type") or "sequence")
        if flow_type == "sequence":
            scene_ids = self._scene_ids_from_list(flow.get("scenes"), scenes)
            if not scene_ids:
                scene_ids = list(scenes.keys())
            return {"type": "sequence", "scenes": scene_ids}
        if flow_type == "state_machine":
            normalized_states = {}
            states = flow.get("states") or {}
            assert isinstance(states, dict) and states, "state_machine 需要 states"
            for state_id, state_spec in states.items():
                state = dict(state_spec or {})
                state["scenes"] = self._scene_ids_from_list(state.get("scenes"), scenes)
                state["transitions"] = [
                    self._normalize_transition(item)
                    for item in state.get("transitions", []) or []
                    if isinstance(item, dict)
                ]
                normalized_states[str(state_id)] = state
            return {
                "type": "state_machine",
                "initial": str(flow.get("initial") or next(iter(normalized_states))),
                "states": normalized_states,
            }
        raise ValueError(f"未知 flow.type: {flow_type}")

    def _scene_ids_from_list(self, items: Any, scenes: dict[str, dict[str, Any]]) -> list[str]:
        """Resolve scene ids from a scene list that may contain embedded dicts."""
        result = []
        if not isinstance(items, list):
            return result
        for item in items:
            if isinstance(item, str):
                result.append(item)
            elif isinstance(item, dict):
                scene_id = str(item.get("id") or item.get("name"))
                if scene_id:
                    result.append(scene_id)
                    if scene_id not in scenes:
                        item["id"] = scene_id
                        scenes[scene_id] = self._normalize_scene(item)
        return result

    def _normalize_transition(self, item: dict[str, Any]) -> dict[str, Any]:
        """Normalize one transition."""
        result = {"to": item.get("to")}
        if "when" in item:
            result["when"] = item["when"]
        return result

    def _normalize_scene(self, scene: dict[str, Any]) -> dict[str, Any]:
        """Normalize one scene spec."""
        result = deepcopy(scene)
        result.setdefault("type", "scene")
        if "id" not in result and "name" in result:
            result["id"] = result["name"]
        result["scope"] = self._normalize_scope(result.get("scope"))
        result["participants"] = self._normalize_participants(result.get("participants"))
        result["schedule"] = self._normalize_schedule(result)
        result["participant_action"] = self._normalize_participant_action(result)
        result["controller_action"] = self._normalize_controller_action(result.get("controller_action"))
        result["referee"] = self._normalize_referee(result.get("referee") or {})
        result.setdefault("resolution", {})
        result.setdefault("publication", {})
        result.setdefault("hooks", {})
        return result

    def _normalize_scope(self, scope: Any) -> dict[str, Any]:
        """Normalize scope field."""
        if isinstance(scope, str):
            return {"id": scope, "visibility": "public" if scope == "public" else "private"}
        if isinstance(scope, dict):
            result = dict(scope)
            result.setdefault("id", result.get("name") or result.get("scope") or "public")
            result.setdefault("visibility", "private" if result.get("members") else "public")
            return result
        return {"id": "public", "visibility": "public"}

    def _normalize_participants(self, participants: Any) -> Any:
        """Normalize participants selector."""
        if participants is None:
            return {"static": []}
        if participants == "all" or isinstance(participants, (list, dict)):
            return participants
        return {"static": []}

    def _normalize_schedule(self, scene: dict[str, Any]) -> dict[str, Any]:
        """Normalize schedule from new schedule or legacy dialogue_policy."""
        schedule = deepcopy(scene.get("schedule") or {})
        if not schedule:
            dialogue = scene.get("dialogue_policy") or {}
            if isinstance(dialogue, dict):
                schedule = dict(dialogue)
                schedule["mode"] = dialogue.get("mode", "none")
        schedule.setdefault("mode", "none")
        if "rounds" in schedule and "max_rounds" not in schedule:
            schedule["max_rounds"] = schedule["rounds"]
        if "rounds" in schedule and "max_turns" not in schedule:
            schedule["max_turns"] = schedule["rounds"]
        schedule.setdefault("dynamic", {"enabled": False})
        return schedule

    def _normalize_participant_action(self, scene: dict[str, Any]) -> dict[str, Any]:
        """Normalize participant_action from new or legacy action_policy."""
        action = deepcopy(scene.get("participant_action") or {})
        legacy_action = scene.get("action_policy") or {}
        if not action and isinstance(legacy_action, dict):
            action = dict(legacy_action)
        scene_type = str(scene.get("scene_type") or "")
        action.setdefault("kind", self._default_action_kind(scene_type))
        action.setdefault("target", legacy_action.get("target", "none") if isinstance(legacy_action, dict) else "none")
        if "candidates" not in action and "candidates" in scene:
            action["candidates"] = scene["candidates"]
        response = deepcopy(action.get("response") or scene.get("response") or {})
        response.setdefault("mode", self._default_response_mode(action["kind"]))
        response.setdefault("schema", self._default_response_schema(action["kind"], response["mode"]))
        action["response"] = response
        if "cue" not in action:
            action["cue"] = scene.get("cue") or response.get("cue") or ""
        return action

    def _normalize_controller_action(self, controller: Any) -> dict[str, Any]:
        """Normalize controller action."""
        if not isinstance(controller, dict):
            return {"enabled": False, "controller": {"type": "none"}, "kind": "none"}
        result = dict(controller)
        result.setdefault("enabled", False)
        result.setdefault("controller", {"type": "none"})
        result.setdefault("kind", "none")
        result.setdefault("choices", [])
        result.setdefault("free_input", {})
        return result

    def _normalize_referee(self, referee: dict[str, Any]) -> dict[str, Any]:
        """Normalize referee rules."""
        result = dict(referee or {})
        result.setdefault("enabled", bool(result.get("rules") or result.get("conditions") or result.get("win_conditions")))
        rules = result.get("rules")
        if rules is None:
            rules = result.get("conditions") or result.get("win_conditions") or []
        result["rules"] = list(rules or [])
        check_on = result.get("check_on") or ["after_scene"]
        result["check_on"] = [check_on] if isinstance(check_on, str) else list(check_on)
        return result

    def _normalize_top_scopes(self, doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalize top-level scopes and add scopes used by scenes."""
        result: dict[str, dict[str, Any]] = {}
        scopes = doc.get("scopes") or []
        if isinstance(scopes, list):
            for item in scopes:
                if isinstance(item, dict):
                    normalized = self._normalize_scope(item.get("name") or item)
                    if "members" in item:
                        normalized["members"] = item.get("members") or []
                    result[normalized["id"]] = normalized
                elif isinstance(item, str):
                    normalized = self._normalize_scope(item)
                    result[normalized["id"]] = normalized
        for scene in doc.get("scenes", {}).values():
            scope = scene.get("scope") or {}
            if isinstance(scope, dict):
                result.setdefault(scope["id"], scope)
        result.setdefault("public", {"id": "public", "visibility": "public"})
        return result

    def _default_action_kind(self, scene_type: str) -> str:
        """Infer action kind from legacy scene type."""
        mapping = {
            "narration": "none",
            "speak": "speak",
            "story": "speak",
            "vote": "vote",
            "choose": "choose",
            "action": "action",
        }
        return mapping.get(scene_type, "none")

    def _default_response_mode(self, action_kind: str) -> str:
        """Infer response mode from participant action kind."""
        if action_kind in {"none", "narration"}:
            return "none"
        if action_kind == "speak":
            return "text"
        return "structured"

    def _default_response_schema(self, action_kind: str, response_mode: str) -> str:
        """Infer response schema from action kind."""
        if response_mode in {"none", "text"}:
            return response_mode
        if action_kind in {"vote", "choose", "action"}:
            return action_kind
        if action_kind == "form":
            return "custom"
        return "text"
