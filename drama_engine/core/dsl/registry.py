"""Small registries for core DSL names and defaults.

本模块只管理 DSL 名称、默认值和可注册集合，不执行业务逻辑。
后续 domain extension 可以基于这些 registry 注册自己的 scene/action/response。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SceneTypeSpec:
    """scene_type 的默认策略。"""

    name: str
    default_dialogue: str
    default_action: str
    default_response_mode: str


@dataclass(frozen=True, slots=True)
class ActionPolicySpec:
    """action_policy.kind 的默认响应 schema。"""

    kind: str
    default_response_schema: str


class DslRegistry:
    """核心 DSL 注册表。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._scene_types: dict[str, SceneTypeSpec] = {}
        self._dialogue_policies: set[str] = set()
        self._dialogue_factories: dict[str, object] = {}
        self._action_policies: dict[str, ActionPolicySpec] = {}
        self._response_modes: set[str] = set()
        self._response_schemas: set[str] = set()
        self._response_factories: dict[str, object] = {}
        self._input_widgets: set[str] = set()
        self._view_kinds: set[str] = set()

    def register_scene_type(self, spec: SceneTypeSpec) -> None:
        """注册 scene_type。"""
        assert isinstance(spec, SceneTypeSpec), "spec 必须是 SceneTypeSpec"
        assert spec.name, "scene_type 名称不能为空"
        self._scene_types[spec.name] = spec

    def register_dialogue_policy(self, name: str, factory: object | None = None) -> None:
        """注册 dialogue_policy.mode。

        factory 可选；编译器可以后续补充，用于把 DSL spec 编译成运行时 policy。
        """
        assert isinstance(name, str) and name.strip(), "dialogue policy 名称不能为空"
        key = name.strip()
        self._dialogue_policies.add(key)
        if factory is not None:
            self._dialogue_factories[key] = factory

    def set_dialogue_policy_factory(self, name: str, factory: object) -> None:
        """为已注册 dialogue_policy.mode 设置运行时 factory。"""
        assert self.has_dialogue_policy(name), f"dialogue policy 未注册: {name}"
        assert callable(factory), f"dialogue policy factory 不可调用: {name}"
        self._dialogue_factories[name] = factory

    def create_dialogue_policy(self, name: str, spec: dict) -> object:
        """创建 dialogue policy 运行时对象。"""
        factory = self._dialogue_factories.get(name)
        if factory is None:
            raise ValueError(f"dialogue policy 尚未设置 factory: {name}")
        return factory(spec)

    def register_action_policy(self, spec: ActionPolicySpec) -> None:
        """注册 action_policy.kind。"""
        assert isinstance(spec, ActionPolicySpec), "spec 必须是 ActionPolicySpec"
        assert spec.kind, "action kind 不能为空"
        self._action_policies[spec.kind] = spec

    def register_response_mode(self, name: str) -> None:
        """注册 response.mode。"""
        assert isinstance(name, str) and name.strip(), "response mode 名称不能为空"
        self._response_modes.add(name.strip())

    def register_response_schema(self, name: str, factory: object | None = None) -> None:
        """注册 response.schema。

        factory 可选；编译器可后续补充，用于创建 Pydantic 响应模型。
        """
        assert isinstance(name, str) and name.strip(), "response schema 名称不能为空"
        key = name.strip()
        self._response_schemas.add(key)
        if factory is not None:
            self._response_factories[key] = factory

    def set_response_schema_factory(self, name: str, factory: object) -> None:
        """为已注册 response.schema 设置模型 factory。"""
        assert self.has_response_schema(name), f"response schema 未注册: {name}"
        assert callable(factory), f"response schema factory 不可调用: {name}"
        self._response_factories[name] = factory

    def create_response_model(self, name: str, response_spec: dict, action_policy: dict) -> object:
        """创建 response schema 对应的 Pydantic 模型。"""
        factory = self._response_factories.get(name)
        if factory is None:
            raise ValueError(f"response schema 尚未设置 factory: {name}")
        return factory(response_spec, action_policy)

    def register_input_widget(self, name: str) -> None:
        """注册 action_policy.input.widget 名称。"""
        assert isinstance(name, str) and name.strip(), "input widget 名称不能为空"
        self._input_widgets.add(name.strip())

    def register_view_kind(self, name: str) -> None:
        """注册 publication.views[].kind 名称。"""
        assert isinstance(name, str) and name.strip(), "view kind 名称不能为空"
        self._view_kinds.add(name.strip())

    def scene_type_names(self) -> list[str]:
        """返回 scene_type 名称列表。"""
        return sorted(self._scene_types.keys())

    def dialogue_policy_names(self) -> list[str]:
        """返回 dialogue_policy.mode 名称列表。"""
        return sorted(self._dialogue_policies)

    def action_policy_names(self) -> list[str]:
        """返回 action_policy.kind 名称列表。"""
        return sorted(self._action_policies.keys())

    def response_mode_names(self) -> list[str]:
        """返回 response.mode 名称列表。"""
        return sorted(self._response_modes)

    def response_schema_names(self) -> list[str]:
        """返回 response.schema 名称列表。"""
        return sorted(self._response_schemas)

    def input_widget_names(self) -> list[str]:
        """返回 action_policy.input.widget 名称列表。"""
        return sorted(self._input_widgets)

    def view_kind_names(self) -> list[str]:
        """返回 publication.views[].kind 名称列表。"""
        return sorted(self._view_kinds)

    def has_scene_type(self, name: str | None) -> bool:
        """检查 scene_type 是否已注册。"""
        return isinstance(name, str) and name in self._scene_types

    def has_dialogue_policy(self, name: str | None) -> bool:
        """检查 dialogue_policy.mode 是否已注册。"""
        return isinstance(name, str) and name in self._dialogue_policies

    def has_action_policy(self, name: str | None) -> bool:
        """检查 action_policy.kind 是否已注册。"""
        return isinstance(name, str) and name in self._action_policies

    def has_response_mode(self, name: str | None) -> bool:
        """检查 response.mode 是否已注册。"""
        return isinstance(name, str) and name in self._response_modes

    def has_response_schema(self, name: str | None) -> bool:
        """检查 response.schema 是否已注册。"""
        return isinstance(name, str) and name in self._response_schemas

    def has_input_widget(self, name: str | None) -> bool:
        """检查 action_policy.input.widget 是否已注册。"""
        return isinstance(name, str) and name in self._input_widgets

    def has_view_kind(self, name: str | None) -> bool:
        """检查 publication.views[].kind 是否已注册。"""
        return isinstance(name, str) and name in self._view_kinds

    def default_dialogue_mode(self, scene_type: str | None) -> str:
        """返回 scene_type 默认 dialogue policy。"""
        spec = self._scene_types.get(scene_type or "")
        return spec.default_dialogue if spec else "sequential"

    def default_action_kind(self, scene_type: str | None) -> str:
        """返回 scene_type 默认 action kind。"""
        spec = self._scene_types.get(scene_type or "")
        return spec.default_action if spec else "none"

    def default_response_mode(self, scene_type: str | None) -> str:
        """返回 scene_type 默认 response mode。"""
        spec = self._scene_types.get(scene_type or "")
        return spec.default_response_mode if spec else "text"

    def default_response_schema(self, action_kind: str | None) -> str:
        """返回 action kind 默认 response schema。"""
        spec = self._action_policies.get(action_kind or "none")
        return spec.default_response_schema if spec else "none"


def build_default_dsl_registry() -> DslRegistry:
    """构建内置 DSL 注册表。"""
    registry = DslRegistry()

    for name in ["none", "sequential", "simultaneous", "single", "random_order", "loop_until", "openchat"]:
        registry.register_dialogue_policy(name)

    for name in ["none", "text", "structured", "mixed"]:
        registry.register_response_mode(name)

    for name in ["none", "text", "vote", "choose", "action", "target", "targets", "rating", "move", "card_action", "custom"]:
        registry.register_response_schema(name)

    scene_specs = [
        SceneTypeSpec("narration", "none", "none", "none"),
        SceneTypeSpec("speak", "sequential", "none", "text"),
        SceneTypeSpec("action", "single", "yes_no", "structured"),
        SceneTypeSpec("vote", "simultaneous", "vote", "structured"),
        SceneTypeSpec("choose", "single", "choose_one", "structured"),
        SceneTypeSpec("board", "single", "board_move", "structured"),
        SceneTypeSpec("card", "single", "card_action", "structured"),
        SceneTypeSpec("story", "sequential", "none", "text"),
    ]
    for spec in scene_specs:
        registry.register_scene_type(spec)

    action_specs = [
        ActionPolicySpec("none", "none"),
        ActionPolicySpec("vote", "vote"),
        ActionPolicySpec("mutual_vote", "choose"),
        ActionPolicySpec("choose_one", "target"),
        ActionPolicySpec("choose_many", "targets"),
        ActionPolicySpec("yes_no", "action"),
        ActionPolicySpec("confirm", "action"),
        ActionPolicySpec("rating", "rating"),
        ActionPolicySpec("board_move", "move"),
        ActionPolicySpec("card_action", "card_action"),
        ActionPolicySpec("form", "custom"),
    ]
    for spec in action_specs:
        registry.register_action_policy(spec)

    for name in ["text", "textarea", "player_select", "multi_player_select", "choice", "card_select", "board_move", "confirm"]:
        registry.register_input_widget(name)

    for name in ["key-value", "text", "markdown", "table", "list", "board", "cards", "vote-summary"]:
        registry.register_view_kind(name)

    return registry
