"""Flow inspector for sequence graph, state machine graph and DSL tree."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class FlowInspector:
    """Convert DSL flow into graph/tree structures for admin frontend."""

    def inspect_file(self, yaml_path: str | Path) -> dict[str, Any]:
        """Inspect flow from YAML file."""
        path = Path(yaml_path)
        assert path.exists(), f"script file not found: {path}"
        raw_text = path.read_text(encoding="utf-8")
        return self.inspect_text(raw_text)

    def inspect_text(self, raw_text: str) -> dict[str, Any]:
        """Inspect flow from raw YAML."""
        assert isinstance(raw_text, str), "raw_text 必须是字符串"
        doc = yaml.safe_load(raw_text) or {}
        if not isinstance(doc, dict):
            doc = {}
        return {
            "sequence": self.sequence_flow(doc),
            "state_machine": self.state_machine(doc),
            "tree": self.dsl_tree(doc),
        }

    def sequence_flow(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Build scene sequence nodes/edges and Mermaid graph."""
        scenes = self._scenes(doc)
        nodes = []
        edges = []
        for index, scene in enumerate(scenes):
            scene_id = scene.get("name") or f"scene_{index}"
            nodes.append({
                "id": scene_id,
                "label": scene.get("display_name") or scene_id,
                "type": scene.get("scene_type", "scene"),
                "scope": scene.get("scope", ""),
                "index": index,
                "condition": scene.get("when"),
            })
            if index + 1 < len(scenes):
                next_scene = scenes[index + 1].get("name") or f"scene_{index + 1}"
                edges.append({"from": scene_id, "to": next_scene, "condition": "next"})
        if scenes and (doc.get("flow") or {}).get("loop"):
            edges.append({"from": nodes[-1]["id"], "to": nodes[0]["id"], "condition": "loop"})
        return {
            "nodes": nodes,
            "edges": edges,
            "tree": self._sequence_tree(nodes),
            "mermaid": self._sequence_mermaid(nodes, edges),
        }

    def state_machine(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Build state read/write graph and simple phase transitions."""
        scenes = self._scenes(doc)
        states: dict[str, dict[str, Any]] = {}
        initial = (doc.get("initial_state") or {}).get("GAME") or {}
        for state_name, value in initial.items():
            states[f"GAME.{state_name}"] = {
                "name": f"GAME.{state_name}",
                "initial_value": value,
                "written_by": ["initial_state.GAME"],
                "read_by": [],
            }
        edges = []
        for index, scene in enumerate(scenes):
            scene_id = scene.get("name") or f"scene_{index}"
            for ref in self._extract_refs(scene):
                if ref.startswith("GAME."):
                    states.setdefault(ref, {"name": ref, "initial_value": None, "written_by": [], "read_by": []})
                    if scene_id not in states[ref]["read_by"]:
                        states[ref]["read_by"].append(scene_id)
                    edges.append({"from": ref, "to": scene_id, "kind": "read"})
            for effect in self._scene_effects(scene):
                state = self._effect_written_state(effect)
                if state:
                    states.setdefault(state, {"name": state, "initial_value": None, "written_by": [], "read_by": []})
                    if scene_id not in states[state]["written_by"]:
                        states[state]["written_by"].append(scene_id)
                    edges.append({"from": scene_id, "to": state, "kind": "write"})
        issues = []
        for state in states.values():
            if not state["written_by"]:
                issues.append({"level": "warning", "code": "STATE_READ_WITHOUT_WRITE", "message": f"{state['name']} 被读取但未写入。"})
            if not state["read_by"]:
                issues.append({"level": "info", "code": "STATE_WRITE_WITHOUT_READ", "message": f"{state['name']} 被写入但未读取。"})
        return {
            "states": sorted(states.values(), key=lambda item: item["name"]),
            "edges": edges,
            "issues": issues,
            "mermaid": self._state_mermaid(edges),
        }

    def dsl_tree(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Build compact DSL tree."""
        root = {"id": "script", "label": "Script", "type": "root", "children": []}
        meta = doc.get("meta") or {}
        root["children"].append({"id": "meta", "label": f"Meta: {meta.get('title', 'Untitled')}", "type": "meta", "children": []})
        roles_node = {"id": "roles", "label": "Roles", "type": "section", "children": []}
        for role in doc.get("roles") or []:
            if isinstance(role, dict):
                roles_node["children"].append({"id": f"role:{role.get('name')}", "label": role.get("display_name") or role.get("name"), "type": "role", "children": []})
        root["children"].append(roles_node)
        scopes_node = {"id": "scopes", "label": "Scopes", "type": "section", "children": []}
        for scope in doc.get("scopes") or []:
            if isinstance(scope, dict):
                scopes_node["children"].append({"id": f"scope:{scope.get('name')}", "label": scope.get("display_name") or scope.get("name"), "type": "scope", "children": []})
        root["children"].append(scopes_node)
        scenes_node = {"id": "scenes", "label": "Scenes", "type": "section", "children": []}
        for index, scene in enumerate(self._scenes(doc)):
            scene_id = scene.get("name") or f"scene_{index}"
            scenes_node["children"].append({
                "id": f"scene:{scene_id}",
                "label": f"{index + 1}. {scene.get('display_name') or scene_id}",
                "type": "scene",
                "children": [
                    {"id": f"scene:{scene_id}:scene_type", "label": f"scene_type: {scene.get('scene_type')}", "type": "field", "children": []},
                    {"id": f"scene:{scene_id}:scope", "label": f"scope: {scene.get('scope')}", "type": "field", "children": []},
                    {"id": f"scene:{scene_id}:effects", "label": f"effects: {len(self._scene_effects(scene))}", "type": "field", "children": []},
                ],
            })
        root["children"].append(scenes_node)
        state_node = {"id": "states", "label": "States", "type": "section", "children": []}
        for name in ((doc.get("initial_state") or {}).get("GAME") or {}).keys():
            state_node["children"].append({"id": f"state:GAME.{name}", "label": f"GAME.{name}", "type": "state", "children": []})
        root["children"].append(state_node)
        return root

    def _scenes(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        return [scene for scene in ((doc.get("flow") or {}).get("scenes") or []) if isinstance(scene, dict)]

    def _sequence_tree(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        return {"id": "sequence", "label": "Sequence Flow", "children": [{"id": node["id"], "label": node["label"], "children": []} for node in nodes]}

    def _sequence_mermaid(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
        lines = ["flowchart TD"]
        for node in nodes:
            lines.append(f"  {self._safe_id(node['id'])}[\"{self._escape(node['label'])}\"]")
        for edge in edges:
            label = edge.get("condition", "")
            if label:
                lines.append(f"  {self._safe_id(edge['from'])} -- {self._escape(label)} --> {self._safe_id(edge['to'])}")
            else:
                lines.append(f"  {self._safe_id(edge['from'])} --> {self._safe_id(edge['to'])}")
        return "\n".join(lines)

    def _state_mermaid(self, edges: list[dict[str, Any]]) -> str:
        lines = ["flowchart LR"]
        for edge in edges[:120]:
            lines.append(f"  {self._safe_id(edge['from'])}[\"{self._escape(edge['from'])}\"] -- {edge['kind']} --> {self._safe_id(edge['to'])}[\"{self._escape(edge['to'])}\"]")
        if len(edges) > 120:
            lines.append("  more[\"... graph truncated for readability ...\"]")
        return "\n".join(lines)

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

    def _effect_written_state(self, effect: dict[str, Any]) -> str:
        entity = effect.get("entity")
        attr = effect.get("attr")
        if entity == "GAME" and attr:
            return f"GAME.{attr}"
        path = effect.get("path")
        if isinstance(path, str) and path.startswith("GAME."):
            return path
        return ""

    def _extract_refs(self, value: Any) -> list[str]:
        refs: list[str] = []
        if isinstance(value, dict):
            ref_value = value.get("ref")
            if isinstance(ref_value, str):
                refs.append(ref_value)
            for child in value.values():
                refs.extend(self._extract_refs(child))
        elif isinstance(value, list):
            for child in value:
                refs.extend(self._extract_refs(child))
        return refs

    def _safe_id(self, value: str) -> str:
        return "n_" + "".join(ch if ch.isalnum() else "_" for ch in str(value))

    def _escape(self, value: Any) -> str:
        return str(value).replace('"', "'")
