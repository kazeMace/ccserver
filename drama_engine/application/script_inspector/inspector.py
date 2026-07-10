"""Script inspector that converts DSL YAML into developer-friendly data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.dsl.validator import DslValidator, ValidationReport
from drama_engine.core.dsl.game_packs import (
    build_default_game_pack_registry,
)
from drama_engine.core.runtime_spec import build_default_runtime_registry

logger = logging.getLogger(__name__)


class ScriptInspector:
    """Build overview, role, scope, scene and state inspection data."""

    def __init__(self, validator: DslValidator | None = None) -> None:
        self.validator = validator or DslValidator()

    def inspect_file(self, yaml_path: str | Path, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Inspect a YAML file and return JSON-friendly data."""
        path = Path(yaml_path)
        assert path.exists(), f"script file not found: {path}"
        raw_text = path.read_text(encoding="utf-8")
        return self.inspect_text(raw_text, source_name=str(path), params=params)

    def inspect_text(
        self,
        raw_text: str,
        source_name: str = "<script>",
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Inspect raw YAML text."""
        assert isinstance(raw_text, str), "raw_text 必须是字符串"
        try:
            doc = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError:
            report = self.validator.validate_text(raw_text, source_name=source_name, params=params)
            return {"source_name": source_name, "parseable": False, "issues": report.to_dict(), "raw_yaml": raw_text}
        if not isinstance(doc, dict):
            doc = {}
        doc = self.validator._expand_param_templates(raw_text, doc, params, ValidationReport(), source_name)
        if not isinstance(doc, dict):
            doc = {}
        report = self.validator.validate_text(raw_text, source_name=source_name, params=params)
        return {
            "source_name": source_name,
            "parseable": True,
            "overview": self._overview(doc),
            "runtime": self._runtime(doc),
            "roles": self._roles(doc),
            "factions": self._factions(doc),
            "players": self._players(doc),
            "scopes": self._scopes(doc),
            "game_pack": self._game_pack(doc),
            "publish": self._publish(doc),
            "publish_inspection": self._publish_inspection(doc, report),
            "scenes": self._scenes(doc),
            "states": self._states(doc),
            "effects": self._effects(doc),
            "concepts": doc.get("concepts") or {},
            "issues": report.to_dict(),
            "raw_yaml": raw_text,
        }

    def _overview(self, doc: dict[str, Any]) -> dict[str, Any]:
        meta = doc.get("meta") or {}
        scenes = self._scene_docs(doc)
        roles = doc.get("roles") or []
        scopes = doc.get("scopes") or []
        states = (doc.get("initial_state") or {}).get("GAME") or {}
        return {
            "title": meta.get("title") or meta.get("name") or "Untitled Script",
            "description": meta.get("description", ""),
            "runtime_type": self._runtime(doc)["type"],
            "has_game_pack": bool(self._game_pack(doc)),
            "min_players": meta.get("min_players"),
            "max_players": meta.get("max_players"),
            "role_count": len(roles),
            "scope_count": len(scopes),
            "scene_count": len(scenes),
            "state_count": len(states),
            "loop": bool((doc.get("flow") or {}).get("loop")),
        }

    def _runtime(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Return runtime declaration inspection data."""
        registry = build_default_runtime_registry()
        spec = doc.get("runtime") or {}
        if isinstance(spec, str):
            runtime_type = spec
            config = {}
        elif isinstance(spec, dict):
            runtime_type = spec.get("type", "interactive_session")
            config = spec.get("config") or {}
            if not isinstance(config, dict):
                config = {}
        else:
            runtime_type = "interactive_session"
            config = {}
        return {
            "type": runtime_type,
            "registered": registry.has(runtime_type),
            "config": dict(config),
            "available": registry.names(),
        }

    def _roles(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for role in doc.get("roles") or []:
            if not isinstance(role, dict):
                continue
            result.append({
                "name": role.get("name"),
                "display_name": role.get("display_name", role.get("name")),
                "faction": role.get("faction", ""),
                "scopes": role.get("scopes") or [],
                "abilities": role.get("abilities") or [],
                "inventory": role.get("inventory") or [],
                "brief": role.get("brief", ""),
            })
        return result

    def _factions(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for role in doc.get("roles") or []:
            faction = role.get("faction") if isinstance(role, dict) else None
            if faction:
                counts[faction] = counts.get(faction, 0) + 1
        concepts = (doc.get("concepts") or {}).get("factions") or {}
        return [{"name": name, "role_type_count": count, "concept": concepts.get(name, {})} for name, count in sorted(counts.items())]

    def _players(self, doc: dict[str, Any]) -> dict[str, Any]:
        players = doc.get("players") or {}
        casting = players.get("casting") or {}
        return {
            "count": players.get("count"),
            "initial_attrs": players.get("initial_attrs") or {},
            "casting_type": casting.get("type", ""),
            "distribution": casting.get("distribution") or {},
            "assignment": casting.get("assignment") or {},
        }

    def _game_pack(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Return declared game pack inspection data."""
        registry = build_default_game_pack_registry()
        spec = doc.get("game_pack") or {}
        if not isinstance(spec, dict) or not spec:
            return {}
        plugin = spec.get("plugin", "")
        item = {
            "plugin": plugin,
            "registered": registry.has(plugin),
            "version": spec.get("version", ""),
            "config": spec.get("config") if isinstance(spec.get("config"), dict) else {},
            "required_extensions": [],
            "supported_runtimes": [],
        }
        if registry.has(plugin):
            metadata = registry.describe(plugin)
            item["required_extensions"] = metadata.get("required_extensions", [])
            item["supported_runtimes"] = metadata.get("supported_runtimes", [])
        return item

    def _publish(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Return top-level publish metadata."""
        spec = doc.get("publish") or {}
        if not isinstance(spec, dict):
            return {}
        return {
            "id": spec.get("id", ""),
            "version": spec.get("version", ""),
            "visibility": spec.get("visibility", ""),
            "tags": list(spec.get("tags") or []) if isinstance(spec.get("tags") or [], list) else [],
            "required_extensions": list(spec.get("required_extensions") or [])
            if isinstance(spec.get("required_extensions") or [], list)
            else [],
            "license": spec.get("license", ""),
            "homepage": spec.get("homepage", ""),
            "repository": spec.get("repository", ""),
        }

    def _publish_inspection(self, doc: dict[str, Any], report: ValidationReport) -> dict[str, Any]:
        """Return publish readiness summary for admin/CLI preview."""
        issue_dict = report.to_dict()
        blocking_levels = {"fatal", "error"}
        blocking = [
            item
            for item in issue_dict.get("issues", [])
            if item.get("level") in blocking_levels
        ]
        return {
            "ready": not blocking,
            "blocking_issue_count": len(blocking),
            "blocking_issue_codes": [item.get("code", "") for item in blocking],
            "runtime_registered": self._runtime(doc)["registered"],
            "game_pack_registered": self._game_pack(doc).get("registered", True),
        }

    def _scopes(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        concepts = (doc.get("concepts") or {}).get("scopes") or {}
        result = []
        for scope in doc.get("scopes") or []:
            if not isinstance(scope, dict):
                continue
            name = scope.get("name")
            result.append({
                "name": name,
                "display_name": scope.get("display_name", name),
                "members": scope.get("members"),
                "delivery": scope.get("delivery", ""),
                "concept": concepts.get(name, {}),
            })
        return result

    def _scenes(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for index, scene in enumerate(self._scene_docs(doc)):
            response = scene.get("response") or {}
            dialogue_policy = scene.get("dialogue_policy") or {}
            action_policy = scene.get("action_policy") or {}
            publication = scene.get("publication") or {}
            result.append({
                "index": index,
                "name": scene.get("name"),
                "display_name": scene.get("display_name", scene.get("name")),
                "scene_type": scene.get("scene_type"),
                "scope": scene.get("scope"),
                "dialogue_policy": dialogue_policy,
                "dialogue_mode": dialogue_policy.get("mode") if isinstance(dialogue_policy, dict) else None,
                "action_policy": action_policy,
                "action_kind": action_policy.get("kind") if isinstance(action_policy, dict) else None,
                "response": response,
                "response_mode": response.get("mode") if isinstance(response, dict) else None,
                "response_schema": response.get("schema") if isinstance(response, dict) else None,
                "has_when": "when" in scene,
                "participants": scene.get("participants"),
                "candidates": scene.get("candidates"),
                "effect_count": len(self._scene_effects(scene)),
                "cue": response.get("cue", "") if isinstance(response, dict) else "",
                "publication": publication,
            })
        return result

    def _states(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        states = (doc.get("initial_state") or {}).get("GAME") or {}
        result = []
        for name, value in states.items():
            result.append({"path": f"GAME.{name}", "initial_value": value, "type": type(value).__name__})
        return result

    def _effects(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for index, scene in enumerate(self._scene_docs(doc)):
            for effect_index, effect in enumerate(self._scene_effects(scene)):
                result.append({
                    "scene": scene.get("name", str(index)),
                    "scene_index": index,
                    "effect_index": effect_index,
                    "type": effect.get("type"),
                    "target": effect.get("target") or effect.get("path") or effect.get("attr"),
                    "raw": effect,
                })
        return result

    def _scene_docs(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        """返回 scene 文档列表，兼容 interactive_session 的顶层 scenes map。

        interactive_session：scenes 是顶层 map（scene_id -> scene）。
        旧结构：flow.scenes 是列表。两者都支持。
        """
        top_scenes = doc.get("scenes")
        if isinstance(top_scenes, dict):
            result = []
            for scene_id, scene in top_scenes.items():
                if isinstance(scene, dict):
                    merged = dict(scene)
                    merged.setdefault("name", scene_id)
                    result.append(merged)
            return result
        return [scene for scene in ((doc.get("flow") or {}).get("scenes") or []) if isinstance(scene, dict)]

    def _scene_effects(self, scene: dict[str, Any]) -> list[dict[str, Any]]:
        effects: list[dict[str, Any]] = []
        resolution = scene.get("resolution") or {}
        if isinstance(resolution, dict) and isinstance(resolution.get("effects"), list):
            effects.extend([item for item in resolution["effects"] if isinstance(item, dict)])
        nested = []
        for effect in effects:
            if isinstance(effect.get("effects"), list):
                nested.extend([item for item in effect["effects"] if isinstance(item, dict)])
        effects.extend(nested)
        return effects
