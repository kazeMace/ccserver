"""Core DSL schema and capability discovery.

本模块从现有 registry/runtime registry 导出稳定的机器可读信息。
它不替代 compiler.validate；validate 仍负责具体 YAML 错误检查。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.dsl import DslRegistry, build_default_dsl_registry
from drama_engine.core.runtime_spec import RuntimeRegistry, build_default_runtime_registry
from drama_engine.core.dsl.extensions import (
    DomainExtensionRegistry,
    build_default_domain_extension_registry,
)
from drama_engine.core.dsl.game_packs import (
    GamePackRegistry,
    RuleSetRegistry,
    build_default_game_pack_registry,
    build_default_rule_set_registry,
)
from drama_engine.core.dsl.schema.types import DslCapability, DslField, DslSchema
from drama_engine.application.authoring.generator import build_default_authoring_templates


META_FIELD_SCHEMA = DslSchema(
    name="meta",
    description="脚本基础元信息，服务运行、展示和 UGC 创作。",
    fields=(
        DslField("id", "string", False, "稳定脚本 ID。"),
        DslField("name", "string", False, "机器可读名称。"),
        DslField("display_name", "string", False, "展示名称。"),
        DslField("title", "string", False, "标题；当前兼容字段。"),
        DslField("version", "string", False, "脚本版本。"),
        DslField("author", "string", False, "作者。"),
        DslField("description", "string", False, "说明。"),
        DslField("tags", "list[string]", False, "标签。"),
        DslField("locale", "string", False, "语言地区，例如 zh-CN。"),
        DslField("license", "string", False, "许可证。"),
    ),
)

PUBLISH_FIELD_SCHEMA = DslSchema(
    name="publish",
    description="发布元信息，服务 marketplace / UGC 发布链路。",
    fields=(
        DslField("id", "string", False, "发布 ID。"),
        DslField("version", "string", False, "发布版本。"),
        DslField("visibility", "string", False, "可见性。", ("private", "unlisted", "public")),
        DslField("tags", "list[string]", False, "发布标签。"),
        DslField("required_extensions", "list[string]", False, "发布所需领域扩展。"),
        DslField("license", "string", False, "许可证。"),
        DslField("homepage", "string", False, "主页 URL。"),
        DslField("repository", "string", False, "源码仓库 URL。"),
    ),
)

SCENE_FIELD_SCHEMA = DslSchema(
    name="scene",
    description="PartySessionRuntime 中的一幕，描述参与者、对话策略、动作、响应、结算和发布。",
    fields=(
        DslField("name", "string", True, "场景唯一名称，非空。"),
        DslField("display_name", "string", False, "展示名称。"),
        DslField("scene_type", "string", True, "业务场景类型。"),
        DslField("scope", "string", False, "本幕默认可见域。"),
        DslField("participants", "selector", False, "谁参与本幕。"),
        DslField("candidates", "selector", False, "动作/投票/选择的候选目标。"),
        DslField("when", "condition", False, "整幕执行条件。"),
        DslField("dialogue_policy", "object", False, "参与者如何发言或提交。"),
        DslField("action_policy", "object", False, "参与者提交什么动作，以及动作约束。"),
        DslField("response", "object", False, "参与者响应的数据协议。"),
        DslField("resolution", "object", False, "本幕结束后的统计和状态变化。"),
        DslField("publication", "object", False, "本幕如何对外公告和展示。"),
        DslField("cue", "string|object", False, "本幕任务提示。"),
    ),
)

RUNTIME_FIELD_SCHEMA = DslSchema(
    name="runtime",
    description="顶层 runtime 声明，决定脚本由哪类 runtime 执行。",
    fields=(
        DslField("type", "string", True, "runtime 类型，例如 game_session。"),
        DslField("config", "object", False, "runtime 私有配置。"),
    ),
)

PLAYERS_FIELD_SCHEMA = DslSchema(
    name="players",
    description="玩家席位、初始属性和发牌策略声明。",
    fields=(
        DslField("count", "integer", True, "玩家数量。"),
        DslField("initial_attrs", "object", False, "写入每个玩家实体的初始属性。"),
        DslField("casting", "object", False, "角色分配策略，例如 shuffle 或 fixed。"),
    ),
)

ROLE_FIELD_SCHEMA = DslSchema(
    name="role",
    description="可分配给玩家的 DSL 身份，不等同于 runtime seat 或 actor。",
    fields=(
        DslField("name", "string", True, "角色唯一名称。"),
        DslField("display_name", "string", False, "角色展示名。"),
        DslField("faction", "string", False, "阵营名称。"),
        DslField("brief", "string", False, "私密身份说明。"),
        DslField("scopes", "list[string]", False, "角色默认订阅的可见域。"),
        DslField("abilities", "list[object]", False, "角色能力声明。"),
        DslField("inventory", "list[object]", False, "初始道具声明。"),
    ),
)

SCOPE_FIELD_SCHEMA = DslSchema(
    name="scope",
    description="可见域声明，只表达消息可见性，不等同于 IO channel。",
    fields=(
        DslField("name", "string", True, "可见域唯一名称。"),
        DslField("display_name", "string", False, "展示名称。"),
        DslField("members", "selector", True, "成员选择器，例如 all 或角色筛选。"),
        DslField("delivery", "string", False, "展示/投递提示。"),
    ),
)

FLOW_FIELD_SCHEMA = DslSchema(
    name="flow",
    description="脚本流程声明，支持 sequence 和 state_machine。",
    fields=(
        DslField("type", "string", False, "流程类型。", ("sequence", "state_machine")),
        DslField("loop", "boolean", False, "sequence 流程是否循环。"),
        DslField("scenes", "list[scene]", False, "sequence 流程场景列表。"),
        DslField("initial", "string", False, "state_machine 初始状态。"),
        DslField("states", "object", False, "state_machine 状态定义。"),
    ),
)

EXTENSIONS_FIELD_SCHEMA = DslSchema(
    name="extensions",
    description="领域扩展声明，例如 board、cards、dice、story、economy、avalon。",
    fields=(
        DslField("<extension_name>", "object", False, "扩展配置对象，名称必须来自 DomainExtensionRegistry。"),
        DslField("enabled", "boolean", False, "扩展是否启用。"),
        DslField("version", "string", False, "扩展版本约束。"),
        DslField("config", "object", False, "扩展私有配置。"),
    ),
)

GAME_PACK_FIELD_SCHEMA = DslSchema(
    name="game_pack",
    description="Game Pack 元数据声明，表示脚本依赖的游戏包。",
    fields=(
        DslField("plugin", "string", True, "game pack 插件 ID，必须来自 GamePackRegistry。"),
        DslField("version", "string", False, "game pack 版本。"),
        DslField("config", "object", False, "game pack 私有配置。"),
    ),
)

RULE_SET_FIELD_SCHEMA = DslSchema(
    name="rule_set",
    description="规则集声明，rule_set_apply effect 通过它找到领域规则处理器。",
    fields=(
        DslField("plugin", "string", True, "rule set 插件 ID，必须来自 RuleSetRegistry。"),
        DslField("version", "string", False, "rule set 版本。"),
        DslField("config", "object", False, "rule set 私有配置。"),
    ),
)

PUBLICATION_FIELD_SCHEMA = DslSchema(
    name="publication",
    description="scene 内发布声明，控制公告、结构化视图和披露时机。",
    fields=(
        DslField("cue", "string|object", False, "覆盖 scene cue 的发布提示。"),
        DslField("announce_cue", "boolean", False, "是否把 cue 投递给 scene.scope。"),
        DslField("messages", "list[object]", False, "公告消息列表。"),
        DslField("views", "list[object]", False, "ViewHost 结构化视图列表。"),
        DslField("disclosures", "list[object]", False, "延迟披露声明。"),
    ),
)


def build_core_dsl_schema() -> dict[str, Any]:
    """Return machine-readable schema sections for core DSL."""
    return {
        "schemas": {
            "meta": META_FIELD_SCHEMA.to_dict(),
            "runtime": RUNTIME_FIELD_SCHEMA.to_dict(),
            "players": PLAYERS_FIELD_SCHEMA.to_dict(),
            "role": ROLE_FIELD_SCHEMA.to_dict(),
            "scope": SCOPE_FIELD_SCHEMA.to_dict(),
            "flow": FLOW_FIELD_SCHEMA.to_dict(),
            "scene": SCENE_FIELD_SCHEMA.to_dict(),
            "publication": PUBLICATION_FIELD_SCHEMA.to_dict(),
            "extensions": EXTENSIONS_FIELD_SCHEMA.to_dict(),
            "game_pack": GAME_PACK_FIELD_SCHEMA.to_dict(),
            "rule_set": RULE_SET_FIELD_SCHEMA.to_dict(),
            "publish": PUBLISH_FIELD_SCHEMA.to_dict(),
        }
    }


def build_core_dsl_capabilities(
    dsl_registry: DslRegistry | None = None,
    runtime_registry: RuntimeRegistry | None = None,
    extension_registry: DomainExtensionRegistry | None = None,
    game_pack_registry: GamePackRegistry | None = None,
    rule_set_registry: RuleSetRegistry | None = None,
) -> dict[str, Any]:
    """Return machine-readable capabilities from current registries.

    Args:
        dsl_registry: Optional DSL registry. Defaults to built-in registry.
        runtime_registry: Optional runtime registry. Defaults to built-in registry.

    Returns:
        JSON-serializable capability document for UGC authoring tools.
    """
    dsl_registry = dsl_registry or build_default_dsl_registry()
    runtime_registry = runtime_registry or build_default_runtime_registry()
    extension_registry = extension_registry or build_default_domain_extension_registry()
    game_pack_registry = game_pack_registry or build_default_game_pack_registry()
    rule_set_registry = rule_set_registry or build_default_rule_set_registry()

    scene_types = []
    for name in dsl_registry.scene_type_names():
        scene_types.append(DslCapability(
            name=name,
            kind="scene_type",
            defaults={
                "dialogue_policy.mode": dsl_registry.default_dialogue_mode(name),
                "action_policy.kind": dsl_registry.default_action_kind(name),
                "response.mode": dsl_registry.default_response_mode(name),
            },
        ).to_dict())

    action_policies = []
    for name in dsl_registry.action_policy_names():
        action_policies.append(DslCapability(
            name=name,
            kind="action_policy",
            defaults={"response.schema": dsl_registry.default_response_schema(name)},
        ).to_dict())

    runtime_types = []
    for runtime_type in runtime_registry.names():
        description = runtime_registry.describe(runtime_type).get("description", "")
        runtime_types.append(DslCapability(
            name=runtime_type,
            kind="runtime",
            description=description,
        ).to_dict())

    return {
        "capabilities": {
            "runtime_types": runtime_types,
            "scene_types": scene_types,
            "dialogue_policies": dsl_registry.dialogue_policy_names(),
            "action_policies": action_policies,
            "response_modes": dsl_registry.response_mode_names(),
            "response_schemas": dsl_registry.response_schema_names(),
            "input_widgets": dsl_registry.input_widget_names(),
            "view_kinds": dsl_registry.view_kind_names(),
            "domain_extensions": extension_registry.describe_all(),
            "game_packs": game_pack_registry.describe_all(),
            "rule_sets": rule_set_registry.describe_all(),
            "authoring_templates": [
                {
                    "game_type": template.game_type,
                    "runtime_type": template.runtime_type,
                    "script_name": template.script_name,
                    "extensions": list(template.extensions),
                    "rule_set": template.rule_set,
                    "keywords": list(template.keywords),
                    "required_questions": list(template.required_questions),
                    "optional_questions": list(template.optional_questions),
                    "defaults": dict(template.defaults),
                    "risk_warnings": list(template.risk_warnings),
                }
                for template in build_default_authoring_templates()
            ],
        }
    }
