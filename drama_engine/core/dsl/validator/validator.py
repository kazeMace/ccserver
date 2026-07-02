"""Static DSL validator for Drama Engine scripts.

校验器用于管理开发端，负责在真正运行前发现 DSL 问题。
The validator is intentionally conservative: it reports clear structural errors
and practical runtime risks without mutating the source document.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.dsl.extensions import build_default_domain_extension_registry
from drama_engine.core.dsl.validator.issue import ValidationIssue, ValidationReport
from drama_engine.core.dsl.game_packs import (
    build_default_game_pack_registry,
    build_default_rule_set_registry,
)
from drama_engine.core.runtime_spec import build_default_runtime_registry

logger = logging.getLogger(__name__)


class DslValidator:
    """Validate YAML syntax, DSL schema, references, states and compileability."""

    def __init__(self, compiler: YamlCompiler | None = None) -> None:
        self.compiler = compiler or YamlCompiler()

    def validate_file(self, yaml_path: str | Path, params: dict[str, Any] | None = None) -> ValidationReport:
        """Validate a script file.

        参数 / Args:
            yaml_path: Script YAML path.
            params: Optional compile params used by compiler checks.

        返回 / Returns:
            ValidationReport with fatal/error/warning/info issues.
        """
        path = Path(yaml_path)
        assert str(path), "yaml_path 不能为空"
        logger.info("[DslValidator] validate file: %s", path)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ValidationReport([
                ValidationIssue(
                    level="fatal",
                    code="SCRIPT_FILE_NOT_FOUND",
                    message=f"剧本文件不存在: {path}",
                    path=str(path),
                    suggestion="请确认 script_id 或上传文件路径是否正确。",
                    source="file_check",
                )
            ])
        return self.validate_text(raw_text, source_name=str(path), params=params)

    def validate_text(
        self,
        raw_text: str,
        source_name: str = "<uploaded>",
        params: dict[str, Any] | None = None,
    ) -> ValidationReport:
        """Validate raw YAML text."""
        assert isinstance(raw_text, str), "raw_text 必须是字符串"
        report = ValidationReport()
        if not raw_text.strip():
            report.add(ValidationIssue(
                level="fatal",
                code="EMPTY_SCRIPT",
                message="剧本文本为空。",
                path=source_name,
                suggestion="请上传或输入有效的 YAML 剧本。",
                source="syntax_check",
            ))
            return report

        try:
            doc = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            report.add(ValidationIssue(
                level="fatal",
                code="YAML_PARSE_ERROR",
                message=f"YAML 解析失败: {exc}",
                path=source_name,
                line=(mark.line + 1) if mark else None,
                column=(mark.column + 1) if mark else None,
                suggestion="请先修复 YAML 缩进、冒号、列表格式或引号问题。",
                source="syntax_check",
            ))
            return report

        if not isinstance(doc, dict):
            report.add(ValidationIssue(
                level="fatal",
                code="ROOT_NOT_OBJECT",
                message="DSL 根节点必须是对象/map。",
                path="$",
                suggestion="请确保 YAML 顶层包含 meta、roles、players、scopes、flow、referee 等字段。",
                source="schema_check",
            ))
            return report

        doc = self._expand_param_templates(raw_text, doc, params, report, source_name)
        if not isinstance(doc, dict):
            return report

        report.extend(self._schema_issues(doc))
        report.extend(self._reference_issues(doc))
        report.extend(self._state_issues(doc))
        report.extend(self._runtime_risk_issues(doc))
        report.extend(self._compiler_issues(doc, params=params))
        return report

    def _expand_param_templates(
        self,
        raw_text: str,
        doc: dict[str, Any],
        params: dict[str, Any] | None,
        report: ValidationReport,
        source_name: str,
    ) -> dict[str, Any]:
        """Expand ``{{param}}`` templates before structural validation.

        参数化脚本（例如狼人杀守卫板）会在 players.count 等位置使用
        ``{{total_players}}``。直接校验未展开的 YAML 会把这些值当字符串，
        导致 compiler validation 误报类型错误。这里复用 compiler 的参数解析
        规则，确保 validate/lint 与真实 compile 入口一致。
        """
        try:
            resolved = self.compiler._resolve_params(doc, params or {})
            if not resolved:
                return doc
            expanded_text = self.compiler._expand_params(raw_text, resolved)
            expanded_doc = yaml.safe_load(expanded_text) or {}
        except Exception as exc:  # noqa: BLE001 - report param expansion failures.
            report.add(ValidationIssue(
                level="error",
                code="PARAM_EXPANSION_ERROR",
                message=f"参数模板展开失败: {exc}",
                path=source_name,
                suggestion="请检查 params 默认值和 --param KEY=VALUE 覆盖值。",
                source="param_check",
            ))
            return doc
        if not isinstance(expanded_doc, dict):
            report.add(ValidationIssue(
                level="fatal",
                code="ROOT_NOT_OBJECT_AFTER_PARAMS",
                message="参数展开后 DSL 根节点必须是对象/map。",
                path="$",
                source="param_check",
            ))
            return {}
        return expanded_doc

    def _schema_issues(self, doc: dict[str, Any]) -> list[ValidationIssue]:
        """Check top-level structure and common field types."""
        issues: list[ValidationIssue] = []
        required = ["meta", "roles", "players", "scopes", "flow", "referee"]
        for key in required:
            if key not in doc:
                issues.append(ValidationIssue(
                    level="error",
                    code="MISSING_REQUIRED_FIELD",
                    message=f"缺少必须字段: {key}",
                    path=key,
                    suggestion=f"请在剧本顶层补充 {key} 字段。",
                    source="schema_check",
                ))
        if issues:
            return issues

        runtime_spec = doc.get("runtime")
        if runtime_spec is not None:
            issues.extend(self._runtime_schema_issues(runtime_spec))
        issues.extend(self._publish_schema_issues(doc.get("publish")))
        extensions_spec = doc.get("extensions", {}) or {}
        issues.extend(self._extension_schema_issues(extensions_spec))
        issues.extend(self._game_pack_schema_issues(doc.get("game_pack")))
        issues.extend(self._rule_set_schema_issues(doc.get("rule_set"), extensions_spec))

        if not isinstance(doc.get("roles"), list) or not doc.get("roles"):
            issues.append(ValidationIssue("error", "INVALID_ROLES", "roles 必须是非空列表。", "roles", source="schema_check"))
        if not isinstance(doc.get("scopes"), list) or not doc.get("scopes"):
            issues.append(ValidationIssue("error", "INVALID_SCOPES", "scopes 必须是非空列表。", "scopes", source="schema_check"))
        flow = doc.get("flow")
        if not isinstance(flow, dict):
            issues.append(ValidationIssue("error", "INVALID_FLOW", "flow 必须是对象。", "flow", source="schema_check"))
        else:
            flow_type = flow.get("type", "sequence")
            if flow_type == "state_machine":
                states = flow.get("states")
                if not isinstance(states, dict) or not states:
                    issues.append(ValidationIssue("error", "INVALID_FLOW_STATES", "state_machine flow.states 必须是非空对象。", "flow.states", source="schema_check"))
                if not flow.get("initial"):
                    issues.append(ValidationIssue("error", "FLOW_INITIAL_MISSING", "state_machine flow.initial 不能为空。", "flow.initial", source="schema_check"))
            elif not isinstance(flow.get("scenes"), list) or not flow.get("scenes"):
                issues.append(ValidationIssue("error", "INVALID_FLOW_SCENES", "flow.scenes 必须是非空列表。", "flow.scenes", source="schema_check"))

        for index, role in enumerate(doc.get("roles") or []):
            if not isinstance(role, dict):
                issues.append(ValidationIssue("error", "INVALID_ROLE_ITEM", f"roles[{index}] 必须是对象。", f"roles[{index}]", source="schema_check"))
                continue
            if not role.get("name"):
                issues.append(ValidationIssue("error", "ROLE_NAME_MISSING", f"roles[{index}] 缺少 name。", f"roles[{index}].name", source="schema_check"))
            if not role.get("faction"):
                issues.append(ValidationIssue("warning", "ROLE_FACTION_MISSING", f"角色 {role.get('name', index)} 缺少 faction。", f"roles[{index}].faction", source="schema_check"))

        removed_scene_keys = {
            "type",
            "turn_policy",
            "performers",
            "collect",
            "effects",
            "selection",
            "messages",
            "publication_messages",
            "publication_views",
            "announce_cue",
            "gate",
            "interaction",
        }
        for index, scene in enumerate(self._scenes(doc)):
            scene_name = scene.get("name", index)
            if not scene.get("name"):
                issues.append(ValidationIssue("error", "SCENE_NAME_MISSING", f"flow.scenes[{index}] 缺少 name。", f"flow.scenes[{index}].name", source="schema_check"))
            if not scene.get("scene_type"):
                issues.append(ValidationIssue("error", "SCENE_TYPE_MISSING", f"scene {scene_name} 缺少 scene_type。", f"flow.scenes[{index}].scene_type", source="schema_check"))
            for removed_key in sorted(removed_scene_keys.intersection(scene.keys())):
                issues.append(ValidationIssue(
                    "error",
                    "REMOVED_SCENE_FIELD",
                    f"scene {scene_name} 使用了已删除字段 {removed_key}。",
                    f"flow.scenes[{index}].{removed_key}",
                    suggestion="请改用 scene_type、participants、dialogue_policy、action_policy、response、resolution、publication。",
                    source="schema_check",
                ))
        return issues

    def _publish_schema_issues(self, publish_spec: Any) -> list[ValidationIssue]:
        """Check top-level publish metadata and required extension references."""
        issues: list[ValidationIssue] = []
        if publish_spec is None:
            return issues
        if not isinstance(publish_spec, dict):
            return [ValidationIssue("error", "INVALID_PUBLISH", "publish 必须是对象。", "publish", source="schema_check")]

        for key in ("id", "version", "license", "homepage", "repository"):
            value = publish_spec.get(key)
            if value is not None and not isinstance(value, str):
                issues.append(ValidationIssue("error", "INVALID_PUBLISH_FIELD", f"publish.{key} 必须是字符串。", f"publish.{key}", source="schema_check"))
        visibility = publish_spec.get("visibility")
        if visibility is not None and visibility not in {"private", "unlisted", "public"}:
            issues.append(ValidationIssue("error", "INVALID_PUBLISH_VISIBILITY", "publish.visibility 必须是 private、unlisted 或 public。", "publish.visibility", source="schema_check"))
        tags = publish_spec.get("tags")
        if tags is not None and (not isinstance(tags, list) or not all(isinstance(item, str) for item in tags)):
            issues.append(ValidationIssue("error", "INVALID_PUBLISH_TAGS", "publish.tags 必须是字符串列表。", "publish.tags", source="schema_check"))
        required_extensions = publish_spec.get("required_extensions")
        if required_extensions is None:
            return issues
        if not isinstance(required_extensions, list) or not all(isinstance(item, str) for item in required_extensions):
            issues.append(ValidationIssue("error", "INVALID_PUBLISH_REQUIRED_EXTENSIONS", "publish.required_extensions 必须是字符串列表。", "publish.required_extensions", source="schema_check"))
            return issues
        extension_registry = build_default_domain_extension_registry()
        for name in required_extensions:
            if not extension_registry.has(name):
                issues.append(ValidationIssue(
                    "error",
                    "UNKNOWN_PUBLISH_REQUIRED_EXTENSION",
                    f"publish.required_extensions 引用了未注册扩展: {name}。",
                    "publish.required_extensions",
                    suggestion=f"可用 extensions: {extension_registry.names()}。",
                    source="schema_check",
                ))
        return issues

    def _extension_schema_issues(self, extensions_spec: Any) -> list[ValidationIssue]:
        """Check top-level domain extension declarations."""
        issues: list[ValidationIssue] = []
        if not extensions_spec:
            return issues
        if not isinstance(extensions_spec, dict):
            return [ValidationIssue("error", "INVALID_EXTENSIONS", "extensions 必须是对象。", "extensions", source="schema_check")]
        registry = build_default_domain_extension_registry()
        for name, config in extensions_spec.items():
            path = f"extensions.{name}"
            if not isinstance(name, str) or not name.strip():
                issues.append(ValidationIssue("error", "INVALID_EXTENSION_NAME", "extensions 的 key 必须是非空字符串。", "extensions", source="schema_check"))
                continue
            if not registry.has(name):
                issues.append(ValidationIssue(
                    "error",
                    "UNKNOWN_EXTENSION",
                    f"extensions.{name} 未注册。",
                    path,
                    suggestion=f"可用 extensions: {registry.names()}。",
                    source="schema_check",
                ))
            if not isinstance(config, dict):
                issues.append(ValidationIssue("error", "INVALID_EXTENSION_CONFIG", f"extensions.{name} 必须是对象配置。", path, source="schema_check"))
                continue
            if "enabled" in config and not isinstance(config.get("enabled"), bool):
                issues.append(ValidationIssue("error", "INVALID_EXTENSION_ENABLED", f"extensions.{name}.enabled 必须是布尔值。", f"{path}.enabled", source="schema_check"))
            if "version" in config and not isinstance(config.get("version"), str):
                issues.append(ValidationIssue("error", "INVALID_EXTENSION_VERSION", f"extensions.{name}.version 必须是字符串。", f"{path}.version", source="schema_check"))
            if "config" in config and not isinstance(config.get("config"), dict):
                issues.append(ValidationIssue("error", "INVALID_EXTENSION_PRIVATE_CONFIG", f"extensions.{name}.config 必须是对象。", f"{path}.config", source="schema_check"))
        return issues

    def _game_pack_schema_issues(self, game_pack_spec: Any) -> list[ValidationIssue]:
        """Check top-level game_pack declaration."""
        issues: list[ValidationIssue] = []
        if not game_pack_spec:
            return issues
        if not isinstance(game_pack_spec, dict):
            return [ValidationIssue("error", "INVALID_GAME_PACK", "game_pack 必须是对象。", "game_pack", source="schema_check")]
        registry = build_default_game_pack_registry()
        plugin = game_pack_spec.get("plugin")
        if not isinstance(plugin, str) or not plugin.strip():
            issues.append(ValidationIssue("error", "GAME_PACK_PLUGIN_MISSING", "game_pack.plugin 必须是非空字符串。", "game_pack.plugin", source="schema_check"))
        elif not registry.has(plugin):
            issues.append(ValidationIssue(
                "error",
                "UNKNOWN_GAME_PACK",
                f"game_pack.plugin '{plugin}' 未注册。",
                "game_pack.plugin",
                suggestion=f"可用 game_pack: {registry.names()}。",
                source="schema_check",
            ))
        if "version" in game_pack_spec and not isinstance(game_pack_spec.get("version"), str):
            issues.append(ValidationIssue("error", "INVALID_GAME_PACK_VERSION", "game_pack.version 必须是字符串。", "game_pack.version", source="schema_check"))
        if "config" in game_pack_spec and not isinstance(game_pack_spec.get("config"), dict):
            issues.append(ValidationIssue("error", "INVALID_GAME_PACK_CONFIG", "game_pack.config 必须是对象。", "game_pack.config", source="schema_check"))
        return issues

    def _rule_set_schema_issues(self, rule_set_spec: Any, extensions_spec: Any) -> list[ValidationIssue]:
        """Check top-level rule_set declaration and required domain extensions."""
        issues: list[ValidationIssue] = []
        if not rule_set_spec:
            return issues
        if not isinstance(rule_set_spec, dict):
            return [ValidationIssue("error", "INVALID_RULE_SET", "rule_set 必须是对象。", "rule_set", source="schema_check")]
        registry = build_default_rule_set_registry()
        plugin = rule_set_spec.get("plugin")
        if not isinstance(plugin, str) or not plugin.strip():
            issues.append(ValidationIssue("error", "RULE_SET_PLUGIN_MISSING", "rule_set.plugin 必须是非空字符串。", "rule_set.plugin", source="schema_check"))
        elif not registry.has(plugin):
            issues.append(ValidationIssue(
                "error",
                "UNKNOWN_RULE_SET",
                f"rule_set.plugin '{plugin}' 未注册。",
                "rule_set.plugin",
                suggestion=f"可用 rule_set: {registry.names()}。",
                source="schema_check",
            ))
        else:
            enabled_extensions = set(extensions_spec.keys()) if isinstance(extensions_spec, dict) else set()
            missing = sorted(
                name
                for name in registry.describe(plugin).get("required_extensions", [])
                if name not in enabled_extensions
            )
            if missing:
                issues.append(ValidationIssue(
                    "error",
                    "RULE_SET_MISSING_EXTENSION",
                    f"rule_set.plugin '{plugin}' 缺少 extensions 声明: {missing}。",
                    "rule_set.plugin",
                    suggestion="请在顶层 extensions 中声明 rule_set 需要的领域扩展。",
                    source="schema_check",
                ))
        if "version" in rule_set_spec and not isinstance(rule_set_spec.get("version"), str):
            issues.append(ValidationIssue("error", "INVALID_RULE_SET_VERSION", "rule_set.version 必须是字符串。", "rule_set.version", source="schema_check"))
        if "config" in rule_set_spec and not isinstance(rule_set_spec.get("config"), dict):
            issues.append(ValidationIssue("error", "INVALID_RULE_SET_CONFIG", "rule_set.config 必须是对象。", "rule_set.config", source="schema_check"))
        return issues

    def _runtime_schema_issues(self, runtime_spec: Any) -> list[ValidationIssue]:
        """Check top-level runtime declaration."""
        issues: list[ValidationIssue] = []
        registry = build_default_runtime_registry()
        if isinstance(runtime_spec, str):
            runtime_type = runtime_spec
            config = {}
        elif isinstance(runtime_spec, dict):
            runtime_type = runtime_spec.get("type", "game_session")
            config = runtime_spec.get("config", {}) or {}
        else:
            issues.append(ValidationIssue(
                "error",
                "INVALID_RUNTIME",
                "runtime 必须是对象或字符串。",
                "runtime",
                suggestion="请使用 runtime: {type: game_session, config: {}}。",
                source="schema_check",
            ))
            return issues

        if not registry.has(runtime_type):
            issues.append(ValidationIssue(
                "error",
                "UNKNOWN_RUNTIME_TYPE",
                f"未知 runtime.type: {runtime_type}。",
                "runtime.type",
                suggestion=f"可用 runtime.type: {registry.names()}。",
                source="schema_check",
            ))
        if not isinstance(config, dict):
            issues.append(ValidationIssue(
                "error",
                "INVALID_RUNTIME_CONFIG",
                "runtime.config 必须是对象。",
                "runtime.config",
                source="schema_check",
            ))
        return issues

    def _reference_issues(self, doc: dict[str, Any]) -> list[ValidationIssue]:
        """Check role/scope references and distribution consistency."""
        issues: list[ValidationIssue] = []
        role_names = self._role_names(doc)
        scope_names = self._scope_names(doc)
        role_seen: set[str] = set()
        for index, role in enumerate(doc.get("roles") or []):
            name = role.get("name") if isinstance(role, dict) else None
            if not name:
                continue
            if name in role_seen:
                issues.append(ValidationIssue("error", "DUPLICATED_ROLE_ID", f"角色重复定义: {name}", f"roles[{index}].name", suggestion="请确保每个 role.name 唯一。", source="reference_check"))
            role_seen.add(name)
            for scope in role.get("scopes") or []:
                if scope not in scope_names:
                    issues.append(ValidationIssue("error", "UNDEFINED_SCOPE_REF", f"角色 {name} 引用了不存在的 scope: {scope}", f"roles[{index}].scopes", suggestion="请在 scopes 中定义该 scope，或修改角色 scopes。", source="reference_check"))

        players = doc.get("players") or {}
        casting = players.get("casting") or {}
        distribution = casting.get("distribution") or {}
        total_distribution = 0
        for role_name, count in distribution.items():
            if role_name not in role_names:
                issues.append(ValidationIssue("error", "UNDEFINED_ROLE_REF", f"发牌分布引用了不存在的角色: {role_name}", "players.casting.distribution", suggestion="请在 roles 中定义该角色，或修改 distribution。", source="reference_check"))
            value = self._safe_int(count)
            if value is not None:
                total_distribution += value
        player_count = self._safe_int(players.get("count"))
        if player_count is not None and total_distribution and total_distribution != player_count:
            issues.append(ValidationIssue("error", "DISTRIBUTION_MISMATCH", f"角色分布人数 {total_distribution} 与玩家人数 {player_count} 不一致。", "players.casting.distribution", suggestion="请调整 players.count 或 distribution。", source="reference_check"))

        for index, scope in enumerate(doc.get("scopes") or []):
            name = scope.get("name") if isinstance(scope, dict) else ""
            members = scope.get("members") if isinstance(scope, dict) else None
            refs = self._extract_values_for_key(members, "equal") + self._extract_values_for_key(members, "not_equal")
            for ref in refs:
                if isinstance(ref, str) and ref in role_names:
                    continue
            if not name:
                issues.append(ValidationIssue("error", "SCOPE_NAME_MISSING", f"scopes[{index}] 缺少 name。", f"scopes[{index}].name", source="reference_check"))

        for index, scene in enumerate(self._scenes(doc)):
            scope = scene.get("scope")
            if scope and scope not in scope_names:
                issues.append(ValidationIssue("error", "UNDEFINED_SCOPE_REF", f"scene {scene.get('name', index)} 引用了不存在的 scope: {scope}", f"flow.scenes[{index}].scope", suggestion="请在 scopes 中定义该 scope，或修改 scene.scope。", source="reference_check"))
        return issues

    def _state_issues(self, doc: dict[str, Any]) -> list[ValidationIssue]:
        """Check simple GAME state read/write usage."""
        issues: list[ValidationIssue] = []
        initial = set((doc.get("initial_state") or {}).get("GAME", {}).keys())
        writes: dict[str, list[str]] = {}
        reads: dict[str, list[str]] = {}
        for location, effect in self._state_entry_effects(doc):
            self._collect_state_write(effect, location, writes)
        for index, scene in enumerate(self._scenes(doc)):
            scene_name = scene.get("name", str(index))
            refs = []
            refs.extend(self._extract_refs(scene.get("when")))
            refs.extend(self._extract_refs(scene.get("participants")))
            refs.extend(self._extract_refs(scene.get("candidates")))
            refs.extend(self._extract_refs(scene.get("dialogue_policy")))
            refs.extend(self._extract_refs(scene.get("action_policy")))
            refs.extend(self._extract_refs(scene.get("response")))
            refs.extend(self._extract_refs(scene.get("resolution")))
            refs.extend(self._extract_refs(scene.get("publication")))
            for ref in refs:
                if ref.startswith("GAME."):
                    reads.setdefault(ref, []).append(f"flow.scenes[{index}]({scene_name})")
            for effect in self._all_effects(scene):
                self._collect_state_write(effect, f"flow.scenes[{index}]({scene_name}).effects", writes)
                for ref in self._extract_refs(effect.get("when")) + self._extract_refs(effect.get("target")) + self._extract_refs(effect.get("value")):
                    if ref.startswith("GAME."):
                        reads.setdefault(ref, []).append(f"flow.scenes[{index}]({scene_name}).effects")
        for key in initial:
            writes.setdefault(f"GAME.{key}", ["initial_state.GAME"])
        for state, locations in sorted(reads.items()):
            if state not in writes:
                issues.append(ValidationIssue("warning", "STATE_READ_BEFORE_WRITE", f"状态 {state} 被读取，但未发现初始化或写入。", locations[0], suggestion="请在 initial_state 或前置 scene effects 中初始化该状态。", source="state_check"))
        for state, locations in sorted(writes.items()):
            if state not in reads and not state.startswith("GAME.__"):
                issues.append(ValidationIssue("info", "UNUSED_STATE_WRITE", f"状态 {state} 被写入，但未发现后续读取。", locations[0], suggestion="如果这是调试或输出状态可以忽略，否则请检查拼写或流程。", source="state_check"))
        return issues

    def _collect_state_write(
        self,
        effect: dict[str, Any],
        location: str,
        writes: dict[str, list[str]],
    ) -> None:
        """Collect simple GAME state writes from one effect."""
        entity = effect.get("entity")
        attr = effect.get("attr")
        path = effect.get("path")
        if entity == "GAME" and attr:
            writes.setdefault(f"GAME.{attr}", []).append(location)
        if isinstance(path, str) and path.startswith("GAME."):
            writes.setdefault(path, []).append(location)
        if effect.get("type") == "avalon_set_quest_rule":
            writes.setdefault("GAME.current_team_size", []).append(location)
            writes.setdefault("GAME.current_fail_threshold", []).append(location)
        if effect.get("type") == "avalon_resolve_mission":
            writes.setdefault("GAME.good_score", []).append(location)
            writes.setdefault("GAME.evil_score", []).append(location)

    def _runtime_risk_issues(self, doc: dict[str, Any]) -> list[ValidationIssue]:
        """Check practical runtime risks."""
        issues: list[ValidationIssue] = []
        scenes = self._scenes(doc)
        if not scenes:
            return issues
        if not any(scene.get("scene_type") == "narration" for scene in scenes):
            issues.append(ValidationIssue("warning", "NO_NARRATION_SCENE", "剧本没有 narration scene，导演推进信息可能不足。", "flow.scenes", source="runtime_risk_check"))
        for index, scene in enumerate(scenes):
            scene_type = scene.get("scene_type")
            if scene_type != "narration" and not scene.get("participants"):
                issues.append(ValidationIssue("warning", "SCENE_WITHOUT_PARTICIPANTS", f"scene {scene.get('name', index)} 没有 participants，运行时可能无人执行。", f"flow.scenes[{index}].participants", suggestion="请确认该 scene 是否需要 participants filter/from_state。", source="runtime_risk_check"))
            action_policy = scene.get("action_policy") or {}
            action_kind = action_policy.get("kind") if isinstance(action_policy, dict) else None
            if (scene_type in {"vote", "choose"} or action_kind in {"vote", "mutual_vote", "choose_one", "choose_many"}) and not scene.get("candidates"):
                issues.append(ValidationIssue("warning", "DECISION_WITHOUT_CANDIDATES", f"scene {scene.get('name', index)} 需要选择目标但没有 candidates。", f"flow.scenes[{index}].candidates", suggestion="请添加 candidates，或确认 action_policy/response 允许无目标。", source="runtime_risk_check"))
        return issues

    def _compiler_issues(self, doc: dict[str, Any], params: dict[str, Any] | None) -> list[ValidationIssue]:
        """Run existing compiler validation/compile checks."""
        issues: list[ValidationIssue] = []
        try:
            compiler_errors = self.compiler.validate(doc)
        except Exception as exc:  # noqa: BLE001 - report all compiler validation failures.
            issues.append(ValidationIssue("error", "COMPILER_VALIDATE_EXCEPTION", f"编译器结构校验异常: {exc}", "$", suggestion="请检查 DSL 字段类型是否符合 compiler 预期。", source="compile_check"))
            return issues
        for message in compiler_errors:
            issues.append(ValidationIssue("error", "COMPILER_VALIDATE_ERROR", str(message), "$", source="compile_check"))
        if compiler_errors:
            return issues
        try:
            self.compiler.compile_doc(doc)
        except Exception as exc:  # noqa: BLE001 - compile error should be visible in admin console.
            issues.append(ValidationIssue("error", "COMPILE_ERROR", f"剧本编译失败: {exc}", "$", suggestion="请根据错误信息检查 flow、roles、scopes、effects。", source="compile_check"))
        return issues

    @staticmethod
    def _scenes(doc: dict[str, Any]) -> list[dict[str, Any]]:
        flow = doc.get("flow") or {}
        if flow.get("type") == "state_machine":
            result: list[dict[str, Any]] = []
            states = flow.get("states") or {}
            if not isinstance(states, dict):
                return result
            for state_spec in states.values():
                if not isinstance(state_spec, dict):
                    continue
                scenes = state_spec.get("scenes") or []
                result.extend([item for item in scenes if isinstance(item, dict)])
            return result
        scenes = flow.get("scenes") or []
        return [item for item in scenes if isinstance(item, dict)]

    @staticmethod
    def _role_names(doc: dict[str, Any]) -> set[str]:
        return {role.get("name") for role in doc.get("roles") or [] if isinstance(role, dict) and role.get("name")}

    @staticmethod
    def _scope_names(doc: dict[str, Any]) -> set[str]:
        return {scope.get("name") for scope in doc.get("scopes") or [] if isinstance(scope, dict) and scope.get("name")}

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None

    def _all_effects(self, scene: dict[str, Any]) -> list[dict[str, Any]]:
        """Return effects from nested resolution blocks."""
        effects: list[dict[str, Any]] = []
        resolution = scene.get("resolution") or {}
        if isinstance(resolution, dict) and isinstance(resolution.get("effects"), list):
            effects.extend([item for item in resolution["effects"] if isinstance(item, dict)])
        nested: list[dict[str, Any]] = []
        for effect in effects:
            if isinstance(effect.get("effects"), list):
                nested.extend([item for item in effect["effects"] if isinstance(item, dict)])
        effects.extend(nested)
        return effects

    def _state_entry_effects(self, doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Return state_machine entry/exit effects with readable locations."""
        flow = doc.get("flow") or {}
        if flow.get("type") != "state_machine":
            return []
        states = flow.get("states") or {}
        if not isinstance(states, dict):
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        for state_name, state_spec in states.items():
            if not isinstance(state_spec, dict):
                continue
            for key in ("entry_effects", "exit_effects"):
                effects = state_spec.get(key) or []
                if not isinstance(effects, list):
                    continue
                for index, effect in enumerate(effects):
                    if isinstance(effect, dict):
                        result.append((f"flow.states.{state_name}.{key}[{index}]", effect))
        return result

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

    def _extract_values_for_key(self, value: Any, key: str) -> list[Any]:
        result: list[Any] = []
        if isinstance(value, dict):
            if key in value:
                result.append(value[key])
            for child in value.values():
                result.extend(self._extract_values_for_key(child, key))
        elif isinstance(value, list):
            for child in value:
                result.extend(self._extract_values_for_key(child, key))
        return result
