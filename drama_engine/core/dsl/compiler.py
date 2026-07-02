# drama_engine/core/dsl/compiler.py
"""
YamlCompiler — 把 YAML 剧本文件编译为 Script 对象。

职责（单一职责原则）：
  1. 读取 YAML 文件（compile）
  2. 展开参数模板（_expand_params）
  3. 校验剧本结构（validate）
  4. 编译为 Script 数据对象（compile_doc）

本模块只做「翻译」，不跑任何游戏逻辑。

依赖：
  - drama_engine.core.engine（Script、Role、Scope 等数据结构）
  - drama_engine.core.dsl.components（ConditionEvaluator、EffectExecutor 等）
  - pydantic（create_model，用于 collect 模型动态创建）
  - yaml

用法：
  compiler = YamlCompiler()
  script = compiler.compile("scripts/fixed_flow/deduction/werewolf_v1_guard.yaml", params={"total_players": 9})
"""

import re
import yaml
from typing import Any, Callable, Optional

from pydantic import Field, create_model

from drama_engine.core.engine import (
    Vocabulary,
    PlayerConfig,
    Role,
    Scope,
    Scene,
    Script,
    Sequence,
    StateMachineFlow,
    ShuffleDeal,
    FixedDeal,
    Sequential,
    Simultaneous,
    Single,
    RandomOrder,
    LoopUntil,
    OpenChat,
    Narration,
    State,
    StateWriter,
    SetAttr,
)
from drama_engine.core.dsl.components import (
    ConditionEvaluator,
    EffectExecutor,
    CandidateResolver,
    ValueResolver,
    make_self_scope_members,
    make_dynamic_whisper_members,
)
from drama_engine.core.dsl import build_default_dsl_registry
from drama_engine.core.dsl.plugins import build_default_plugin_registry
from drama_engine.core.runtime_spec import build_default_runtime_registry
from drama_engine.core.dsl.extensions import build_default_domain_extension_registry
from drama_engine.core.dsl.game_packs import (
    build_default_game_pack_registry,
    build_default_rule_set_registry,
)

# 默认 DSL 注册表用于导出旧常量，保持外部引用稳定。
_DEFAULT_DSL_REGISTRY = build_default_dsl_registry()

# 合法值集合来自注册表；后续扩展应注册到 DslRegistry。
VALID_SCENE_TYPES = frozenset(_DEFAULT_DSL_REGISTRY.scene_type_names())
VALID_DIALOGUE_POLICIES = frozenset(_DEFAULT_DSL_REGISTRY.dialogue_policy_names())
VALID_ACTION_KINDS = frozenset(_DEFAULT_DSL_REGISTRY.action_policy_names())
VALID_RESPONSE_MODES = frozenset(_DEFAULT_DSL_REGISTRY.response_mode_names())
VALID_RESPONSE_SCHEMAS = frozenset(_DEFAULT_DSL_REGISTRY.response_schema_names())

# 旧版 scene DSL 字段已删除。出现这些字段时直接报错，不做兼容。
REMOVED_SCENE_KEYS = frozenset({
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
})

# Core selector keys. Domain extensions should register explicit selector handlers later,
# instead of silently adding ad-hoc keys to the core YAML shape.
PARTICIPANTS_SELECTOR_KEYS = frozenset({
    "filter",
    "static",
    "from_state",
    "from_state_set",
    "ordered",
    "when",
    "min",
    # Planned generic selector keys. Current compiler validates their shape first;
    # runtime semantics can be added by selector components later.
    "source",
    "order_by",
    "limit",
})

CANDIDATES_SELECTOR_KEYS = frozenset({
    "filter",
    "static",
    "from_data",
    "from_state",
    "when",
    "extra",
    "count",
    "min",
    "max",
    "distinct",
    # Planned generic selector keys. Current compiler validates their shape first;
    # runtime semantics can be added by selector components later.
    "source",
    "exclude",
    "include_self",
    "sort",
    "limit",
})

SELECTION_KEYS = frozenset({
    # Current runtime fields.
    "tie_policy",
    "weight",
    # Planned generic selection fields. Current compiler validates shape first;
    # runtime semantics can be added by resolution components later.
    "type",
    "target_field",
    "threshold",
    "top_k",
    "weights",
})

SELECTION_TYPES = frozenset({"plurality", "majority", "top_k"})
SELECTION_TIE_POLICIES = frozenset({"alphabetical", "no_winner", "all_tied", "runoff"})


class YamlCompiler:
    """
    YAML 剧本编译器。

    把声明式的 YAML 剧本翻译成 Drama Engine 可执行的 Script 对象。

    使用方式：
      compiler = YamlCompiler()
      script = compiler.compile("/path/to/script.yaml")
      # 或者先 validate 再 compile
      errors = compiler.validate(doc)
      if not errors:
          script = compiler.compile_doc(doc)
    """

    def __init__(self):
        """初始化编译器，构建内部用到的组件。"""
        self._plugins = build_default_plugin_registry()
        self._dsl_registry = build_default_dsl_registry()
        # 条件求值器：用于解析 scene.when / participants.when / effects.when 等
        self._evaluator = ConditionEvaluator(self._plugins)
        # 效果执行器：用于执行 effects 列表
        self._executor = EffectExecutor(self._evaluator, self._plugins)
        # 候选集解析器：用于 candidates 字段
        self._candidate_resolver = CandidateResolver(self._evaluator)
        self._values = ValueResolver(self._plugins)
        self._runtime_registry = build_default_runtime_registry()
        self._extension_registry = build_default_domain_extension_registry()
        self._game_pack_registry = build_default_game_pack_registry()
        self._rule_set_registry = build_default_rule_set_registry()
        self._register_dialogue_policy_factories()
        self._register_response_schema_factories()


    def _register_dialogue_policy_factories(self) -> None:
        """注册内置 dialogue_policy factory。"""
        self._dsl_registry.set_dialogue_policy_factory("none", lambda spec: Narration())
        self._dsl_registry.set_dialogue_policy_factory("sequential", lambda spec: Sequential())
        self._dsl_registry.set_dialogue_policy_factory("simultaneous", lambda spec: Simultaneous())
        self._dsl_registry.set_dialogue_policy_factory("single", lambda spec: Single())
        self._dsl_registry.set_dialogue_policy_factory("random_order", lambda spec: RandomOrder())

        def make_openchat(spec: dict) -> OpenChat:
            rounds = spec.get("rounds", 1)
            speakers_per_round = spec.get("speakers_per_round")
            return OpenChat(rounds=rounds, speakers_per_round=speakers_per_round)

        self._dsl_registry.set_dialogue_policy_factory("openchat", make_openchat)

        def make_loop_until(spec: dict) -> LoopUntil:
            until_condition_spec = spec.get("until", {})
            evaluator = self._evaluator

            def loop_condition(responses: list, state: State) -> bool:
                if not until_condition_spec:
                    return True
                return evaluator.evaluate(until_condition_spec, state, actor=None)

            return LoopUntil(condition=loop_condition)

        self._dsl_registry.set_dialogue_policy_factory("loop_until", make_loop_until)


    def _register_response_schema_factories(self) -> None:
        """注册内置 response.schema factory。"""
        self._dsl_registry.set_response_schema_factory("vote", self._make_vote_model)
        self._dsl_registry.set_response_schema_factory("choose", self._make_choose_model)
        self._dsl_registry.set_response_schema_factory("action", self._make_action_model)
        self._dsl_registry.set_response_schema_factory("target", self._make_target_model)
        self._dsl_registry.set_response_schema_factory("targets", self._make_targets_model)
        self._dsl_registry.set_response_schema_factory("rating", self._make_rating_model)
        self._dsl_registry.set_response_schema_factory("move", self._make_move_model)
        self._dsl_registry.set_response_schema_factory("card_action", self._make_card_action_model)
        self._dsl_registry.set_response_schema_factory("custom", self._make_custom_model)

    # =========================================================================
    # 公共 API
    # =========================================================================

    def compile(self, yaml_path: str, params: dict = None) -> Script:
        """
        从 YAML 文件路径编译出 Script 对象。

        流程：
          1. 读取文件原始文本
          2. 展开 {{param}} 占位符
          3. 解析 YAML
          4. 编译为 Script

        参数：
          yaml_path — YAML 文件的绝对路径
          params    — 参数覆盖字典，如 {"total_players": 9}

        返回：
          Script 对象

        异常：
          FileNotFoundError — 文件不存在时
          yaml.YAMLError    — YAML 语法错误时
          ValueError        — 剧本结构不合法时
        """
        with open(yaml_path, "r", encoding="utf-8") as f:
            raw_text = f.read()

        print(f"[YamlCompiler] 读取剧本文件：{yaml_path}")

        # 先做一次初步解析，取出 params 声明（用于默认值合并）
        # 这里先只解析顶层，不展开（保持文本原样处理 params 声明）
        preliminary = yaml.safe_load(raw_text)
        resolved = self._resolve_params(preliminary, params or {})

        # 展开占位符
        expanded_text = self._expand_params(raw_text, resolved)
        doc = yaml.safe_load(expanded_text)

        print(f"[YamlCompiler] YAML 解析完成，开始编译...")
        return self.compile_doc(doc)

    def validate(self, doc: dict) -> list:
        """
        校验 YAML 文档结构，返回错误消息列表。

        空列表表示没有错误。
        校验不修改 doc，是幂等的只读操作。

        参数：
          doc — 已解析的 YAML 文档字典

        返回：
          错误消息字符串列表，空列表表示无错误
        """
        errors = []
        if doc is None:
            errors.append("文档为空")
            return errors

        # ── runtime 声明检查。未写时默认 game_session。 ───────────────────────
        runtime_errors = self._validate_runtime_spec(doc.get("runtime"))
        errors.extend(runtime_errors)

        # ── 必须字段检查 ──────────────────────────────────────────────────────
        required_top_keys = ["meta", "roles", "players", "scopes", "flow", "referee"]
        for key in required_top_keys:
            if key not in doc:
                errors.append(f"缺少必须字段: {key}")

        # 如果缺少核心字段，后续校验没有意义，提前返回
        if errors:
            return errors

        errors.extend(self._validate_meta_spec(doc.get("meta")))
        errors.extend(self._validate_publish_spec(doc.get("publish")))

        # ── roles 校验 ───────────────────────────────────────────────────────
        roles_spec = doc.get("roles", [])
        if not isinstance(roles_spec, list) or len(roles_spec) == 0:
            errors.append("roles 必须是非空列表")
            return errors

        role_names = set()
        for role in roles_spec:
            rname = role.get("name")
            if not rname:
                errors.append("roles 中有角色缺少 name 字段")
            else:
                role_names.add(rname)

            inventory = role.get("inventory", [])
            if inventory:
                if not isinstance(inventory, list):
                    errors.append(f"role '{rname}' 的 inventory 必须是列表")
                else:
                    for item in inventory:
                        item_name = item.get("item") if isinstance(item, dict) else None
                        if not item_name:
                            errors.append(f"role '{rname}' 的 inventory 中有道具缺少 item 字段")
                            continue
                        if not item.get("display_name"):
                            errors.append(
                                f"role '{rname}' 的 inventory 道具 '{item_name}' 缺少 display_name 字段"
                            )
                        if not item.get("description"):
                            errors.append(
                                f"role '{rname}' 的 inventory 道具 '{item_name}' 缺少 description 字段"
                            )

        # ── vocab / concepts 校验 ───────────────────────────────────────────
        vocab_spec = doc.get("vocab", {}) or {}
        if vocab_spec and not isinstance(vocab_spec, dict):
            errors.append("vocab 必须是字典")
            vocab_spec = {}

        ability_names = set()
        item_names = set()
        for role in roles_spec:
            rname = role.get("name")
            for ability in role.get("abilities", []) or []:
                ability_names.add(ability)
            for item in role.get("inventory", []) or []:
                if isinstance(item, dict) and item.get("item"):
                    item_names.add(item["item"])

        concepts_spec = doc.get("concepts", {}) or {}
        if concepts_spec and not isinstance(concepts_spec, dict):
            errors.append("concepts 必须是字典")
            concepts_spec = {}

        errors.extend(
            self._validate_concepts(
                concepts_spec=concepts_spec,
                expected={
                    "roles": role_names,
                    "abilities": ability_names,
                    "items": item_names,
                },
                required_prompts={"abilities"},
            )
        )

        plugins_spec = doc.get("plugins", []) or []
        if plugins_spec and not isinstance(plugins_spec, list):
            errors.append("plugins 必须是列表")
        elif isinstance(plugins_spec, list):
            for index, plugin_item in enumerate(plugins_spec):
                if not isinstance(plugin_item, dict):
                    errors.append(f"plugins[{index}] 必须是字典")
                    continue
                if not plugin_item.get("id"):
                    errors.append(f"plugins[{index}] 缺少 id 字段")

        extensions_spec = doc.get("extensions", {}) or {}
        errors.extend(self._validate_extensions_spec(extensions_spec))
        errors.extend(self._validate_game_pack_spec(doc.get("game_pack")))
        errors.extend(self._validate_rule_set_spec(doc.get("rule_set"), extensions_spec))

        # ── players.casting.distribution 总数校验 ────────────────────────────
        players_spec = doc.get("players", {})
        # player_count 可能是字符串（来自 YAML 模板展开后的引号值），统一转 int
        player_count = int(players_spec.get("count", 0))
        casting_spec = players_spec.get("casting", {})
        player_initial_attrs = players_spec.get("initial_attrs", {})
        if player_initial_attrs and not isinstance(player_initial_attrs, dict):
            errors.append("players.initial_attrs 必须是字典")

        player_ids = players_spec.get("ids")
        if player_ids is not None:
            if not isinstance(player_ids, list) or not all(isinstance(x, str) for x in player_ids):
                errors.append("players.ids 必须是字符串列表")
            elif len(player_ids) != player_count:
                errors.append(
                    f"players.ids 数量 {len(player_ids)} 与 players.count {player_count} 不符"
                )

        if casting_spec.get("type") == "shuffle":
            distribution = casting_spec.get("distribution", {})
            # distribution 中的 value 可能是字符串（同上），统一转 int 后再求和
            total = sum(int(v) for v in distribution.values()) if distribution else 0
            if total != player_count:
                errors.append(
                    f"角色总数 {total} 与 players.count {player_count} 不符"
                    f"（distribution 合计应等于 players.count）"
                )
            # 检查 distribution 中的角色名是否都在 roles 里定义
            for role_name in distribution:
                if role_name not in role_names:
                    errors.append(
                        f"distribution 中的角色 '{role_name}' 未在 roles 中定义"
                    )

        # ── scopes 校验 ──────────────────────────────────────────────────────
        scopes_spec = doc.get("scopes", [])
        scope_names = set()
        if not isinstance(scopes_spec, list):
            errors.append("scopes 必须是列表")
        else:
            for scope in scopes_spec:
                sname = scope.get("name")
                if not sname:
                    errors.append("scopes 中有 scope 缺少 name 字段")
                else:
                    scope_names.add(sname)

        if isinstance(concepts_spec, dict):
            errors.extend(
                self._validate_concepts(
                    concepts_spec=concepts_spec,
                    expected={
                        "factions": {r.get("faction") for r in roles_spec if r.get("faction")},
                        "scopes": scope_names,
                    },
                    required_prompts=set(),
                )
            )

        # ── flow 校验 ──────────────────────────────────────────────────────
        flow_spec = doc.get("flow", {})
        flow_type = flow_spec.get("type", "sequence")
        if flow_type not in ("sequence", "state_machine"):
            errors.append("flow.type 必须是 sequence 或 state_machine")
        if flow_type == "state_machine":
            states_spec = flow_spec.get("states", {})
            if not isinstance(states_spec, dict) or not states_spec:
                errors.append("flow.type=state_machine 时 states 必须是非空字典")
            initial = flow_spec.get("initial")
            if not initial:
                errors.append("flow.type=state_machine 时必须提供 initial")
            elif isinstance(states_spec, dict) and initial not in states_spec:
                errors.append(f"flow.initial '{initial}' 不在 flow.states 中")

            if isinstance(states_spec, dict):
                for state_name, state_spec in states_spec.items():
                    if not isinstance(state_spec, dict):
                        errors.append(f"flow.states.{state_name} 必须是字典")
                        continue
                    scenes = state_spec.get("scenes", [])
                    if not isinstance(scenes, list):
                        errors.append(f"flow.states.{state_name}.scenes 必须是列表")
                    else:
                        for scene in scenes:
                            errors.extend(
                                self._validate_scene_spec(scene, scope_names, state_name)
                            )
                    transitions = state_spec.get("transitions", [])
                    if transitions and not isinstance(transitions, list):
                        errors.append(f"flow.states.{state_name}.transitions 必须是列表")
                    elif isinstance(transitions, list):
                        for transition in transitions:
                            if not isinstance(transition, dict):
                                errors.append(
                                    f"flow.states.{state_name}.transitions 条目必须是字典"
                                )
                                continue
                            target = transition.get("to")
                            if not target:
                                errors.append(
                                    f"flow.states.{state_name}.transitions 条目缺少 to"
                                )
                            elif isinstance(states_spec, dict) and target not in states_spec:
                                errors.append(
                                    f"flow.states.{state_name}.transition.to '{target}' 不在 flow.states 中"
                                )
                            if "when" in transition and not isinstance(transition.get("when"), dict):
                                errors.append(
                                    f"flow.states.{state_name}.transition.when 必须是条件字典"
                                )
                    for effects_key in ("entry_effects", "exit_effects"):
                        effects_spec = state_spec.get(effects_key, [])
                        if effects_spec and not isinstance(effects_spec, list):
                            errors.append(
                                f"flow.states.{state_name}.{effects_key} 必须是列表"
                            )
                        else:
                            errors.extend(
                                self._validate_effect_specs(
                                    effects_spec,
                                    f"flow.states.{state_name}.{effects_key}",
                                )
                            )
        else:
            scenes = flow_spec.get("scenes", [])
            if not isinstance(scenes, list):
                errors.append("flow.scenes 必须是列表")
            else:
                for scene in scenes:
                    errors.extend(self._validate_scene_spec(scene, scope_names))

        return errors

    def _validate_scene_spec(
        self,
        scene: dict,
        scope_names: set,
        state_name: str | None = None,
    ) -> list:
        """
        校验单个 scene 规格。

        新版 scene DSL 不兼容旧字段。Scene 必须使用 scene_type、
        participants、dialogue_policy、action_policy、response、resolution、publication 等字段。
        """
        errors = []
        location = f"flow.states.{state_name}." if state_name else ""
        if not isinstance(scene, dict):
            return [f"{location}scene 条目必须是字典"]

        sname = scene.get("name", "<无名>")
        name = scene.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{location}scene.name 必须是非空字符串")

        removed = sorted(key for key in REMOVED_SCENE_KEYS if key in scene)
        if removed:
            errors.append(
                f"scene '{sname}' 使用了已删除的旧 scene 字段 {removed}；"
                "请改用 scene_type/dialogue_policy/action_policy/response/resolution/publication"
            )

        # 校验 scene_type。scene_type 是业务场景类型，不是响应 schema。
        scene_type = scene.get("scene_type")
        if not self._dsl_registry.has_scene_type(scene_type):
            errors.append(
                f"scene '{sname}' 的 scene_type '{scene_type}' 不合法，"
                f"合法值：{self._dsl_registry.scene_type_names()}"
            )

        # 校验 scope 引用存在。
        scene_scope = scene.get("scope")
        if scene_scope and scene_scope not in scope_names:
            errors.append(f"scene '{sname}' 引用了未定义的 scope '{scene_scope}'")

        # 校验整幕执行条件。
        if "when" in scene and not isinstance(scene.get("when"), dict):
            errors.append(f"scene '{sname}' 的 when 必须是条件字典")

        # participants / candidates 是 selector，负责任务参与者和候选目标选择。
        if "participants" in scene:
            errors.extend(self._validate_participants_selector(scene.get("participants"), sname))
        candidates_spec = scene.get("candidates")
        if candidates_spec is not None:
            errors.extend(self._validate_candidates_selector(candidates_spec, sname))

        # dialogue_policy 控制参与者如何发言/提交。
        dialogue_policy = scene.get("dialogue_policy", {}) or {}
        if not isinstance(dialogue_policy, dict):
            errors.append(f"scene '{sname}' 的 dialogue_policy 必须是字典")
            dialogue_policy = {}
        dialogue_mode = dialogue_policy.get("mode", self._default_dialogue_mode(scene_type))
        if not self._dsl_registry.has_dialogue_policy(dialogue_mode):
            errors.append(
                f"scene '{sname}' 的 dialogue_policy.mode '{dialogue_mode}' 不合法，"
                f"合法值：{self._dsl_registry.dialogue_policy_names()}"
            )
        if scene_type == "narration" and dialogue_mode not in (None, "none"):
            errors.append(f"scene '{sname}' 是 narration 类型，dialogue_policy.mode 必须为 none")
        if dialogue_mode == "loop_until" and "until" in dialogue_policy and not isinstance(dialogue_policy.get("until"), dict):
            errors.append(f"scene '{sname}' 的 dialogue_policy.until 必须是条件字典")
        if dialogue_mode == "openchat":
            rounds = dialogue_policy.get("rounds")
            if rounds is not None and not self._is_positive_int(rounds):
                errors.append(f"scene '{sname}' 的 dialogue_policy.rounds 必须是正整数")
            speakers_per_round = dialogue_policy.get("speakers_per_round")
            if speakers_per_round is not None and not self._is_positive_int(speakers_per_round):
                errors.append(f"scene '{sname}' 的 dialogue_policy.speakers_per_round 必须是正整数")

        # action_policy 控制提交动作的业务语义。
        action_policy = scene.get("action_policy", {}) or {}
        if not isinstance(action_policy, dict):
            errors.append(f"scene '{sname}' 的 action_policy 必须是字典")
            action_policy = {}
        action_kind = action_policy.get("kind", self._default_action_kind(scene_type))
        if not self._dsl_registry.has_action_policy(action_kind):
            errors.append(
                f"scene '{sname}' 的 action_policy.kind '{action_kind}' 不合法，"
                f"合法值：{self._dsl_registry.action_policy_names()}"
            )
        target_mode = action_policy.get("target")
        if target_mode is not None and target_mode not in ("none", "optional", "required"):
            errors.append(f"scene '{sname}' 的 action_policy.target 必须是 none、optional 或 required")
        for key in ("min_choices", "max_choices"):
            value = action_policy.get(key)
            if value is not None and (not isinstance(value, int) or value < 0):
                errors.append(f"scene '{sname}' 的 action_policy.{key} 必须是非负整数")
        if "allow_skip" in action_policy and not isinstance(action_policy.get("allow_skip"), bool):
            errors.append(f"scene '{sname}' 的 action_policy.allow_skip 必须是布尔值")
        if "validate_candidates" in action_policy and not isinstance(action_policy.get("validate_candidates"), bool):
            errors.append(f"scene '{sname}' 的 action_policy.validate_candidates 必须是布尔值")
        if "input" in action_policy:
            errors.extend(self._validate_action_input(action_policy.get("input"), sname))

        # response 控制参与者响应数据协议。
        response_spec = scene.get("response", {}) or {}
        if not isinstance(response_spec, dict):
            errors.append(f"scene '{sname}' 的 response 必须是字典")
            response_spec = {}
        response_mode = response_spec.get("mode", self._default_response_mode(scene_type))
        if not self._dsl_registry.has_response_mode(response_mode):
            errors.append(
                f"scene '{sname}' 的 response.mode '{response_mode}' 不合法，"
                f"合法值：{self._dsl_registry.response_mode_names()}"
            )
        response_schema = response_spec.get("schema", self._default_response_schema(scene_type, action_kind))
        if not self._dsl_registry.has_response_schema(response_schema) and not isinstance(response_schema, dict):
            errors.append(
                f"scene '{sname}' 的 response.schema '{response_schema}' 不合法，"
                f"合法值：{self._dsl_registry.response_schema_names()} 或自定义字段字典"
            )
        if "include_reason" in response_spec and not isinstance(response_spec.get("include_reason"), bool):
            errors.append(f"scene '{sname}' 的 response.include_reason 必须是布尔值")
        if "prompt" in response_spec and not isinstance(response_spec.get("prompt"), str):
            errors.append(f"scene '{sname}' 的 response.prompt 必须是字符串")

        # resolution 负责 selection/effects。
        resolution = scene.get("resolution", {}) or {}
        if not isinstance(resolution, dict):
            errors.append(f"scene '{sname}' 的 resolution 必须是字典")
            resolution = {}
        selection_spec = resolution.get("selection")
        if selection_spec is not None:
            errors.extend(self._validate_selection_spec(selection_spec, sname))
        effects_spec = resolution.get("effects", []) or []
        if not isinstance(effects_spec, list):
            errors.append(f"scene '{sname}' 的 resolution.effects 必须是列表")
        else:
            errors.extend(self._validate_effect_specs(effects_spec, f"scene '{sname}' 的 resolution.effects"))

        # publication 负责 cue/messages/views/disclosures。
        publication = scene.get("publication", {}) or {}
        if not isinstance(publication, dict):
            errors.append(f"scene '{sname}' 的 publication 必须是字典")
            publication = {}
        if "announce_cue" in publication and not isinstance(publication.get("announce_cue"), bool):
            errors.append(f"scene '{sname}' 的 publication.announce_cue 必须是布尔值")
        if "messages" in publication:
            errors.extend(self._validate_publication_messages(publication.get("messages"), scope_names, sname))
        if "views" in publication:
            errors.extend(self._validate_publication_views(publication.get("views"), scope_names, sname))
        errors.extend(self._validate_cue(self._scene_cue_spec(scene), sname))
        return errors

    def _is_non_negative_int(self, value: Any) -> bool:
        """判断 value 是否为非负整数；bool 不视为整数。"""
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _is_positive_int(self, value: Any) -> bool:
        """判断 value 是否为正整数；bool 不视为整数。"""
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    def _validate_condition_or_list(self, value: Any, label: str) -> list:
        """校验条件字段：允许单个条件 dict 或条件列表。"""
        if isinstance(value, dict):
            return []
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return []
        return [f"{label} 必须是条件字典或条件列表"]

    def _validate_selector_common(self, selector: dict, allowed_keys: set, label: str) -> list:
        """校验 selector 通用字段。"""
        errors = []
        unknown = sorted(key for key in selector.keys() if key not in allowed_keys)
        if unknown:
            errors.append(f"{label} 包含未知字段 {unknown}")
        if "filter" in selector and not isinstance(selector.get("filter"), dict):
            errors.append(f"{label}.filter 必须是字典")
        if "when" in selector:
            errors.extend(self._validate_condition_or_list(selector.get("when"), f"{label}.when"))
        if "from_state" in selector and not isinstance(selector.get("from_state"), str):
            errors.append(f"{label}.from_state 必须是字符串路径")
        if "from_state_set" in selector and not isinstance(selector.get("from_state_set"), str):
            errors.append(f"{label}.from_state_set 必须是字符串路径")
        if "static" in selector and not isinstance(selector.get("static"), list):
            errors.append(f"{label}.static 必须是列表")
        if "source" in selector and not isinstance(selector.get("source"), str):
            errors.append(f"{label}.source 必须是字符串")
        if "limit" in selector and not self._is_positive_int(selector.get("limit")):
            errors.append(f"{label}.limit 必须是正整数")
        return errors

    def _validate_participants_selector(self, selector_spec: Any, scene_name: str) -> list:
        """校验 participants selector。"""
        label = f"scene '{scene_name}' 的 participants"
        if selector_spec == "all":
            return []
        if isinstance(selector_spec, list):
            if all(isinstance(item, str) for item in selector_spec):
                return []
            return [f"{label} 列表写法必须只包含字符串"]
        if isinstance(selector_spec, str):
            return [f"{label} 字符串写法只支持 all"]
        if not isinstance(selector_spec, dict):
            return [f"{label} 必须是字典、字符串 all 或字符串列表"]

        errors = self._validate_selector_common(selector_spec, PARTICIPANTS_SELECTOR_KEYS, label)
        if "ordered" in selector_spec and not isinstance(selector_spec.get("ordered"), bool):
            errors.append(f"{label}.ordered 必须是布尔值")
        if "min" in selector_spec and not self._is_non_negative_int(selector_spec.get("min")):
            errors.append(f"{label}.min 必须是非负整数")
        order_by = selector_spec.get("order_by")
        if order_by is not None and not isinstance(order_by, (str, dict)):
            errors.append(f"{label}.order_by 必须是字符串或字典")
        return errors

    def _validate_candidates_selector(self, selector_spec: Any, scene_name: str) -> list:
        """校验 candidates selector。"""
        label = f"scene '{scene_name}' 的 candidates"
        if not isinstance(selector_spec, dict):
            return [f"{label} 必须是字典"]

        errors = self._validate_selector_common(selector_spec, CANDIDATES_SELECTOR_KEYS, label)
        if "from_data" in selector_spec and not isinstance(selector_spec.get("from_data"), str):
            errors.append(f"{label}.from_data 必须是字符串字段名")
        if "extra" in selector_spec:
            extra = selector_spec.get("extra")
            if not isinstance(extra, (list, dict, str)):
                errors.append(f"{label}.extra 必须是列表、字典或字符串")
        for key in ("count", "min", "max"):
            value = selector_spec.get(key)
            if value is None:
                continue
            if isinstance(value, dict):
                continue
            if key == "count" and value == "all_candidates":
                continue
            if not self._is_positive_int(value):
                errors.append(f"{label}.{key} 必须是正整数、动态值字典或 all_candidates")
        if "distinct" in selector_spec and not isinstance(selector_spec.get("distinct"), bool):
            errors.append(f"{label}.distinct 必须是布尔值")
        if "include_self" in selector_spec and not isinstance(selector_spec.get("include_self"), bool):
            errors.append(f"{label}.include_self 必须是布尔值")
        exclude = selector_spec.get("exclude")
        if exclude is not None and not isinstance(exclude, (list, dict, str)):
            errors.append(f"{label}.exclude 必须是列表、字典或字符串")
        sort = selector_spec.get("sort")
        if sort is not None and not isinstance(sort, (str, dict)):
            errors.append(f"{label}.sort 必须是字符串或字典")
        return errors

    def _validate_selection_spec(self, selection_spec: Any, scene_name: str) -> list:
        """校验 resolution.selection 统计协议。"""
        label = f"scene '{scene_name}' 的 resolution.selection"
        if not isinstance(selection_spec, dict):
            return [f"{label} 必须是字典"]

        errors = []
        unknown = sorted(key for key in selection_spec.keys() if key not in SELECTION_KEYS)
        if unknown:
            errors.append(f"{label} 包含未知字段 {unknown}")

        selection_type = selection_spec.get("type")
        if selection_type is not None and selection_type not in SELECTION_TYPES:
            errors.append(f"{label}.type 必须是 plurality、majority 或 top_k")

        tie_policy = selection_spec.get("tie_policy")
        if tie_policy is not None and tie_policy not in SELECTION_TIE_POLICIES:
            errors.append(
                f"{label}.tie_policy 必须是 alphabetical、no_winner、all_tied 或 runoff"
            )

        target_field = selection_spec.get("target_field")
        if target_field is not None and not isinstance(target_field, str):
            errors.append(f"{label}.target_field 必须是字符串")

        top_k = selection_spec.get("top_k")
        if top_k is not None and not self._is_positive_int(top_k):
            errors.append(f"{label}.top_k 必须是正整数")

        threshold = selection_spec.get("threshold")
        if threshold is not None and not isinstance(threshold, (int, float, dict)):
            errors.append(f"{label}.threshold 必须是数字或字典")

        weight = selection_spec.get("weight")
        if weight is not None and not isinstance(weight, (int, float, dict, str)):
            errors.append(f"{label}.weight 必须是数字、字典或字符串")

        weights = selection_spec.get("weights")
        if weights is not None and not isinstance(weights, dict):
            errors.append(f"{label}.weights 必须是字典")

        return errors


    def _validate_action_input(self, input_spec: Any, scene_name: str) -> list:
        """校验 action_policy.input 前端输入协议。"""
        errors = []
        if not isinstance(input_spec, dict):
            return [f"scene '{scene_name}' 的 action_policy.input 必须是字典"]
        widget = input_spec.get("widget")
        if widget is not None and not isinstance(widget, str):
            errors.append(f"scene '{scene_name}' 的 action_policy.input.widget 必须是字符串")
        elif widget is not None and not self._dsl_registry.has_input_widget(widget):
            errors.append(
                f"scene '{scene_name}' 的 action_policy.input.widget '{widget}' 不合法，"
                f"合法值：{self._dsl_registry.input_widget_names()}"
            )
        timeout = input_spec.get("timeout_seconds")
        if timeout is not None and (not isinstance(timeout, int) or timeout < 0):
            errors.append(f"scene '{scene_name}' 的 action_policy.input.timeout_seconds 必须是非负整数")
        for key in ("allow_change", "reveal_progress"):
            value = input_spec.get(key)
            if value is not None and not isinstance(value, bool):
                errors.append(f"scene '{scene_name}' 的 action_policy.input.{key} 必须是布尔值")
        return errors

    def _validate_publication_messages(
        self,
        messages_spec: Any,
        scope_names: set,
        scene_name: str,
    ) -> list:
        """校验 publication.messages。"""
        errors = []
        messages = self._normalize_publication_messages(messages_spec)
        for index, item in enumerate(messages):
            audience = None
            if isinstance(item, dict):
                audience = item.get("audience") or item.get("scope")
            if audience and audience not in scope_names:
                errors.append(
                    f"scene '{scene_name}' 的 publication.messages[{index}] "
                    f"引用了未定义的 audience '{audience}'"
                )
            errors.extend(
                self._validate_cue(
                    self._publication_message_to_cue(item),
                    f"{scene_name}.publication.messages[{index}]",
                )
            )
        return errors

    def _validate_publication_views(
        self,
        views_spec: Any,
        scope_names: set,
        scene_name: str,
    ) -> list:
        """校验 publication.views。"""
        errors = []
        if views_spec is None:
            return errors
        if not isinstance(views_spec, list):
            return [f"scene '{scene_name}' 的 publication.views 必须是列表"]
        for index, item in enumerate(views_spec):
            if not isinstance(item, dict):
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}] 必须是字典"
                )
                continue
            if not (item.get("id") or item.get("view_id")):
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}] 缺少 id"
                )
            view_kind = item.get("kind") or item.get("view_kind")
            if not view_kind:
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}] 缺少 kind"
                )
            elif not isinstance(view_kind, str):
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}].kind 必须是字符串"
                )
            elif not self._dsl_registry.has_view_kind(view_kind):
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}].kind '{view_kind}' 不合法，"
                    f"合法值：{self._dsl_registry.view_kind_names()}"
                )
            audience = item.get("audience") or item.get("scope")
            if audience and audience not in scope_names:
                errors.append(
                    f"scene '{scene_name}' 的 publication.views[{index}] "
                    f"引用了未定义的 audience '{audience}'"
                )
        return errors

    def _default_dialogue_mode(self, scene_type: str | None) -> str:
        """根据场景类型推导默认对话策略。"""
        return self._dsl_registry.default_dialogue_mode(scene_type)

    def _default_action_kind(self, scene_type: str | None) -> str:
        """根据场景类型推导默认动作语义。"""
        return self._dsl_registry.default_action_kind(scene_type)

    def _default_response_mode(self, scene_type: str | None) -> str:
        """根据场景类型推导默认响应模式。"""
        return self._dsl_registry.default_response_mode(scene_type)

    def _default_response_schema(self, scene_type: str | None, action_kind: str | None) -> str:
        """根据场景类型和动作语义推导默认响应 schema。"""
        if scene_type in ("speak", "story") and action_kind in (None, "none"):
            return "text"
        return self._dsl_registry.default_response_schema(action_kind)

    def _scene_cue_spec(self, scene: dict) -> Any:
        """读取新版 scene 中的任务提示 cue。

        response.prompt 只表示输出格式要求，不再兼任任务提示。
        """
        response = scene.get("response", {}) if isinstance(scene.get("response"), dict) else {}
        publication = scene.get("publication", {}) if isinstance(scene.get("publication"), dict) else {}
        if "cue" in scene:
            return scene["cue"]
        if "cue" in response:
            return response["cue"]
        if "cue" in publication:
            return publication["cue"]
        return ""

    def _scene_publication_messages(self, scene: dict) -> Any:
        """读取新版 scene 的 publication.messages。"""
        publication = scene.get("publication", {}) if isinstance(scene.get("publication"), dict) else {}
        return publication.get("messages")

    def _scene_publication_views(self, scene: dict) -> Any:
        """读取新版 scene 的 publication.views。"""
        publication = scene.get("publication", {}) if isinstance(scene.get("publication"), dict) else {}
        return publication.get("views")

    def _scene_publication_disclosures(self, scene: dict) -> Any:
        """读取新版 scene 的 publication.disclosures。"""
        publication = scene.get("publication", {}) if isinstance(scene.get("publication"), dict) else {}
        return publication.get("disclosures")

    def _scene_announce_cue(self, scene: dict) -> bool:
        """读取新版 scene 的 publication.announce_cue。"""
        publication = scene.get("publication", {}) if isinstance(scene.get("publication"), dict) else {}
        return bool(publication.get("announce_cue", True))

    def _normalize_publication_messages(self, messages_spec: Any) -> list:
        """
        把 publication.messages 统一成列表。

        支持：
          - "文本"
          - {audience: town, text: "文本"}
          - [{audience: town, text: "文本"}, ...]
        """
        if messages_spec is None:
            return []
        if isinstance(messages_spec, list):
            return [item for item in messages_spec if isinstance(item, (dict, str))]
        if isinstance(messages_spec, (dict, str)):
            return [messages_spec]
        return []

    def _publication_message_to_cue(self, item: Any) -> Any:
        """把单条 publication message 转为 cue 规格，复用 cue 编译/校验。"""
        if isinstance(item, str):
            return item
        if not isinstance(item, dict):
            return ""
        if "cue" in item:
            return item["cue"]
        cue_spec = {"text": item.get("text", "")}
        if "vars" in item:
            cue_spec["vars"] = item.get("vars")
        return cue_spec

    def _validate_effect_specs(self, effects_spec: list, label: str) -> list:
        """校验 effects 列表。"""
        errors = []
        for effect in effects_spec:
            if not isinstance(effect, dict):
                errors.append(f"{label} 条目必须是字典")
                continue
            if "condition" in effect:
                errors.append(
                    f"{label}[].condition 已删除，请改用 effects[].when"
                )
            if "when" in effect and not isinstance(effect.get("when"), dict):
                errors.append(f"{label}[].when 必须是条件字典")
        return errors


    def _validate_concepts(
        self,
        concepts_spec: dict,
        expected: dict,
        required_prompts: set,
    ) -> list:
        """
        校验 concepts 是否覆盖关键概念。

        参数：
          concepts_spec    — YAML 顶层 concepts 字典
          expected         — group -> 必须解释的概念名集合
          required_prompts — 哪些 group 的条目必须含 prompt

        返回：
          错误消息列表
        """
        errors = []
        for group_name, names in expected.items():
            if not names:
                continue

            group = concepts_spec.get(group_name, {}) or {}
            if not isinstance(group, dict):
                errors.append(f"concepts.{group_name} 必须是字典")
                continue

            for name in sorted(names):
                concept = group.get(name)
                if not isinstance(concept, dict):
                    errors.append(f"concepts.{group_name}.{name} 缺少概念解释")
                    continue
                if not concept.get("display_name"):
                    errors.append(f"concepts.{group_name}.{name} 缺少 display_name")
                if not concept.get("description"):
                    errors.append(f"concepts.{group_name}.{name} 缺少 description")
                if group_name in required_prompts and not concept.get("prompt"):
                    errors.append(f"concepts.{group_name}.{name} 缺少 prompt")
        return errors

    def _validate_cue(self, cue_spec: Any, scene_name: str) -> list:
        """校验 cue 模板变量是否显式声明。"""
        errors = []
        if cue_spec is None:
            return errors

        if isinstance(cue_spec, dict):
            text = cue_spec.get("text", "")
            vars_spec = cue_spec.get("vars", {}) or {}
            if not isinstance(vars_spec, dict):
                return [f"scene '{scene_name}' 的 cue.vars 必须是字典"]
        else:
            text = str(cue_spec)
            vars_spec = {}

        placeholders = set(re.findall(r"\{([^}]+)\}", text))
        for name in sorted(placeholders):
            if name.startswith("GAME."):
                continue
            if name not in vars_spec:
                errors.append(
                    f"scene '{scene_name}' 的 cue 占位符 '{{{name}}}' 必须在 cue.vars 中声明"
                )
        for name, var_spec in vars_spec.items():
            if not isinstance(var_spec, dict):
                continue
            format_spec = var_spec.get("format")
            if isinstance(format_spec, dict):
                fmt_type = format_spec.get("type")
                if (fmt_type == "expr" or "expr" in format_spec) and "default" not in format_spec:
                    errors.append(
                        f"scene '{scene_name}' 的 cue.vars.{name}.format 使用 expr 时必须提供 default"
                    )
        return errors
    def _validate_meta_spec(self, meta_spec: Any) -> list:
        """校验顶层 meta。

        当前保持宽松：只要求 meta 是 dict、title/name/display_name 至少有一个，
        其他发布字段若存在则校验类型。
        """
        if not isinstance(meta_spec, dict):
            return ["meta 必须是字典"]
        errors = []
        if not (meta_spec.get("title") or meta_spec.get("name") or meta_spec.get("display_name")):
            errors.append("meta 必须至少包含 title、name 或 display_name")
        for key in ("id", "name", "display_name", "title", "version", "author", "description", "locale", "license"):
            value = meta_spec.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"meta.{key} 必须是字符串")
        tags = meta_spec.get("tags")
        if tags is not None and (not isinstance(tags, list) or not all(isinstance(item, str) for item in tags)):
            errors.append("meta.tags 必须是字符串列表")
        return errors

    def _validate_publish_spec(self, publish_spec: Any) -> list:
        """校验顶层 publish 发布信息。"""
        if publish_spec is None:
            return []
        if not isinstance(publish_spec, dict):
            return ["publish 必须是字典"]

        errors = []
        for key in ("id", "version", "visibility", "license", "homepage", "repository"):
            value = publish_spec.get(key)
            if value is not None and not isinstance(value, str):
                errors.append(f"publish.{key} 必须是字符串")
        visibility = publish_spec.get("visibility")
        if visibility is not None and visibility not in ("private", "unlisted", "public"):
            errors.append("publish.visibility 必须是 private、unlisted 或 public")
        tags = publish_spec.get("tags")
        if tags is not None and (not isinstance(tags, list) or not all(isinstance(item, str) for item in tags)):
            errors.append("publish.tags 必须是字符串列表")
        required_extensions = publish_spec.get("required_extensions")
        if required_extensions is not None and (
            not isinstance(required_extensions, list)
            or not all(isinstance(item, str) for item in required_extensions)
        ):
            errors.append("publish.required_extensions 必须是字符串列表")
        return errors


    def _validate_extensions_spec(self, extensions_spec: Any) -> list:
        """校验顶层 extensions 声明。"""
        if not extensions_spec:
            return []
        if not isinstance(extensions_spec, dict):
            return ["extensions 必须是字典"]

        errors = []
        for name, config in extensions_spec.items():
            if not isinstance(name, str) or not name.strip():
                errors.append("extensions 的 key 必须是非空字符串")
                continue
            if not self._extension_registry.has(name):
                errors.append(
                    f"extensions.{name} 未注册；合法值：{self._extension_registry.names()}"
                )
            if config is not None and not isinstance(config, dict):
                errors.append(f"extensions.{name} 必须是字典配置")
                continue
            config = config or {}
            if "enabled" in config and not isinstance(config.get("enabled"), bool):
                errors.append(f"extensions.{name}.enabled 必须是布尔值")
            if "version" in config and not isinstance(config.get("version"), str):
                errors.append(f"extensions.{name}.version 必须是字符串")
            if "config" in config and not isinstance(config.get("config"), dict):
                errors.append(f"extensions.{name}.config 必须是字典")
        return errors

    def _validate_game_pack_spec(self, game_pack_spec: Any) -> list:
        """校验顶层 game_pack 声明。"""
        if not game_pack_spec:
            return []
        if not isinstance(game_pack_spec, dict):
            return ["game_pack 必须是字典"]

        errors = []
        plugin = game_pack_spec.get("plugin")
        if not isinstance(plugin, str) or not plugin.strip():
            errors.append("game_pack.plugin 必须是非空字符串")
        elif not self._game_pack_registry.has(plugin):
            errors.append(
                f"game_pack.plugin '{plugin}' 未注册；"
                f"合法值：{self._game_pack_registry.names()}"
            )

        if "version" in game_pack_spec and not isinstance(game_pack_spec.get("version"), str):
            errors.append("game_pack.version 必须是字符串")
        if "config" in game_pack_spec and not isinstance(game_pack_spec.get("config"), dict):
            errors.append("game_pack.config 必须是字典")
        return errors

    def _validate_rule_set_spec(self, rule_set_spec: Any, extensions_spec: Any) -> list:
        """校验顶层 rule_set 声明。"""
        if not rule_set_spec:
            return []
        if not isinstance(rule_set_spec, dict):
            return ["rule_set 必须是字典"]

        errors = []
        plugin = rule_set_spec.get("plugin")
        if not isinstance(plugin, str) or not plugin.strip():
            errors.append("rule_set.plugin 必须是非空字符串")
            return errors
        if not self._rule_set_registry.has(plugin):
            errors.append(
                f"rule_set.plugin '{plugin}' 未注册；"
                f"合法值：{self._rule_set_registry.names()}"
            )
        else:
            required = self._rule_set_registry.describe(plugin).get("required_extensions", [])
            enabled_extensions = set(extensions_spec.keys()) if isinstance(extensions_spec, dict) else set()
            missing = sorted(name for name in required if name not in enabled_extensions)
            if missing:
                errors.append(f"rule_set.plugin '{plugin}' 缺少 extensions 声明：{missing}")

        if "version" in rule_set_spec and not isinstance(rule_set_spec.get("version"), str):
            errors.append("rule_set.version 必须是字符串")
        if "config" in rule_set_spec and not isinstance(rule_set_spec.get("config"), dict):
            errors.append("rule_set.config 必须是字典")
        return errors


    def _validate_runtime_spec(self, runtime_spec: Any) -> list:
        """校验顶层 runtime 声明。"""
        try:
            self._runtime_registry.parse_declaration(runtime_spec)
        except (AssertionError, ValueError) as exc:
            return [str(exc)]
        return []

    def compile_doc(self, doc: dict) -> Script:
        """
        把已解析的 YAML 文档字典编译为 Script 对象。

        参数：
          doc — 已解析的 YAML 文档

        返回：
          Script 对象

        异常：
          ValueError — 剧本结构不合法时（建议先调用 validate）
        """
        assert isinstance(doc, dict), "doc 必须是 dict"

        # 按依赖顺序编译各部分
        runtime_declaration = self._runtime_registry.parse_declaration(doc.get("runtime"))
        roles_spec = doc.get("roles", [])
        scopes_spec = doc.get("scopes", [])
        players_spec = doc.get("players", {})
        flow_spec = doc.get("flow", {})
        referee_spec = doc.get("referee", {})
        triggers_spec = doc.get("triggers", [])
        plugins_spec = doc.get("plugins", []) or []
        extensions_spec = doc.get("extensions", {}) or {}
        game_pack_spec = doc.get("game_pack")
        rule_set_spec = doc.get("rule_set")
        publish_spec = doc.get("publish")
        self._current_extensions = extensions_spec
        self._current_rule_set = rule_set_spec

        # 编译各部分
        roles_dict = self._compile_roles(roles_spec)     # name -> Role
        scopes_dict = self._compile_scopes(scopes_spec)  # name -> Scope

        # 词汇表从编译结果中自动生成
        vocab = self._compile_vocab(roles_dict, scopes_dict, doc.get("vocab", {}) or {})
        concepts = doc.get("concepts", {}) or {}

        casting = self._compile_casting(players_spec, list(roles_dict.values()))
        player_config = self._compile_player_config(players_spec)
        flow = self._compile_flow(flow_spec)
        referee = self._compile_referee(referee_spec)
        triggers = self._compile_triggers(triggers_spec)

        script = Script(
            vocab=vocab,
            roles=list(roles_dict.values()),
            casting=casting,
            scopes=list(scopes_dict.values()),
            flow=flow,
            referee=referee,
            player_config=player_config,
            concepts=concepts,
            triggers=triggers,
            plugins=plugins_spec,
            extensions=extensions_spec,
            plugin_registry=self._plugins,
            runtime=runtime_declaration,
            game_pack=game_pack_spec,
            rule_set=rule_set_spec,
            publish=publish_spec,
        )

        print(
            f"[YamlCompiler] 编译完成："
            f"roles={len(script.roles)}，scopes={len(script.scopes)}，"
            f"scenes={len(script.flow.scenes)}"
        )
        return script

    # =========================================================================
    # 参数处理
    # =========================================================================

    def _resolve_params(self, doc: dict, override: dict) -> dict:
        """
        合并 YAML 的 params 声明与外部覆盖字典，生成最终参数字典。

        优先级：override > YAML params.default

        参数：
          doc      — 已解析的 YAML 文档（可含 params 字段）
          override — 外部传入的参数覆盖字典

        返回：
          合并后的参数字典，如 {"total_players": 9, "wolf_count": 3}
        """
        result = {}

        # 读取 YAML 里声明的参数默认值
        params_spec = doc.get("params", []) if doc else []
        for param_def in params_spec:
            name = param_def.get("name")
            default = param_def.get("default")
            if name:
                result[name] = default

        # 外部覆盖具有更高优先级
        result.update(override)

        return result

    def _expand_params(self, raw_text: str, params: dict) -> str:
        """
        把 YAML 原始文本中的 {{param_name}} 占位符替换为实际值。

        参数：
          raw_text — 含占位符的 YAML 原始文本
          params   — 参数字典，如 {"total_players": 9}

        返回：
          替换后的文本

        说明：
          - int/float/bool 类型直接转字符串替换
          - list 类型展开为 YAML 行内序列（如 [a, b, c]）
          - str 类型直接替换
        """
        result = raw_text

        for key, value in params.items():
            placeholder = "{{" + key + "}}"
            if isinstance(value, list):
                # 列表展开为 YAML inline 格式：[a, b, c]
                items_str = ", ".join(str(v) for v in value)
                replacement = f"[{items_str}]"
            else:
                replacement = str(value)

            result = result.replace(placeholder, replacement)

        return result

    # =========================================================================
    # 内部编译方法：按「编译顺序」排列
    # =========================================================================

    def _compile_vocab(self, roles_dict: dict, scopes_dict: dict, vocab_spec: dict = None) -> Vocabulary:
        """
        从已编译的 roles 和 scopes 中自动生成 Vocabulary。

        参数：
          roles_dict  — name -> Role 字典
          scopes_dict — name -> Scope 字典
          vocab_spec   — YAML 顶层 vocab，可补充 statuses/items 等概念名

        返回：
          Vocabulary 实例
        """
        role_names = frozenset(roles_dict.keys())
        faction_names = frozenset(r.faction for r in roles_dict.values() if r.faction)
        scope_names = frozenset(scopes_dict.keys())
        ability_names = frozenset(
            ability
            for role in roles_dict.values()
            for ability in role.abilities
        )
        item_names = frozenset(
            item_spec["item"]
            for role in roles_dict.values()
            for item_spec in (role.inventory or [])
            if isinstance(item_spec, dict) and item_spec.get("item")
        )
        vocab_spec = vocab_spec or {}
        status_names = frozenset(vocab_spec.get("statuses", []) or [])

        return Vocabulary(
            roles=role_names,
            factions=faction_names,
            scopes=scope_names,
            abilities=ability_names,
            items=item_names,
            statuses=status_names,
        )

    def _compile_roles(self, roles_spec: list) -> dict:
        """
        编译 roles 声明列表，返回 name -> Role 字典。

        brief 支持：
          - str：直接用
          - dict：取 "normal" 键，若无则取第一个值

        参数：
          roles_spec — roles YAML 列表

        返回：
          dict，key 为角色名，value 为 Role 对象
        """
        roles_dict = {}
        for role_spec in roles_spec:
            name = role_spec["name"]

            # brief 可以是字符串或字典（按语境选择不同版本）
            brief_raw = role_spec.get("brief", "")
            if isinstance(brief_raw, dict):
                # 取 "normal" 键，没有则取第一个
                brief = brief_raw.get("normal") or next(iter(brief_raw.values()), "")
            else:
                brief = str(brief_raw)

            faction = role_spec.get("faction", "")
            scopes = role_spec.get("scopes", [])
            abilities = role_spec.get("abilities", [])
            display_name = role_spec.get("display_name", name)
            inventory = role_spec.get("inventory", [])

            roles_dict[name] = Role(
                name=name,
                brief=brief,
                scopes=scopes,
                abilities=abilities,
                faction=faction,
                display_name=display_name,
                inventory=inventory,
            )

            print(f"[YamlCompiler] 编译角色：{name}（faction={faction}）")

        return roles_dict

    def _compile_scopes(self, scopes_spec: list) -> dict:
        """
        编译 scopes 声明列表，返回 name -> Scope 字典。

        members 支持的写法：
          - "all"    — 所有已注册实体（含死者）
          - "alive"  — 所有存活实体
          - "dead"   — 所有死亡实体
          - "self"   — 动态 self scope（见 scope_types.py）
          - "dynamic_whisper" — 双人私语 scope
          - {filter: {attr: value, ...}} — 属性过滤
          - {type: "self"} / {type: "dynamic_whisper"} — 类型化 scope

        参数：
          scopes_spec — scopes YAML 列表

        返回：
          dict，key 为 scope 名，value 为 Scope 对象
        """
        scopes_dict = {}
        for scope_spec in scopes_spec:
            name = scope_spec["name"]
            members_spec = scope_spec.get("members", "all")
            delivery = scope_spec.get("delivery", "immediate")

            members_fn = self._compile_scope_members(members_spec)

            scopes_dict[name] = Scope(
                name=name,
                members=members_fn,
                delivery=delivery,
            )

            print(f"[YamlCompiler] 编译 scope：{name}（delivery={delivery}）")

        return scopes_dict

    def _compile_scope_members(self, members_spec: Any) -> Callable:
        """
        编译 scope 的 members 字段，返回 Callable[[State], set[str]]。

        参数：
          members_spec — members 字段值（str 关键字或 dict 过滤器）

        返回：
          接受 State 返回成员集合的函数
        """
        # 字符串关键字
        if members_spec == "all":
            def members_all(state: State) -> set:
                """返回所有已注册实体（含死者，排除 GAME）。"""
                return {e for e in state.all_entities() if e != "GAME"}
            return members_all

        if members_spec == "alive":
            def members_alive(state: State) -> set:
                """返回所有存活实体（alive=True）。"""
                return state.having(alive=True)
            return members_alive

        if members_spec == "dead":
            def members_dead(state: State) -> set:
                """返回所有死亡实体（alive=False）。"""
                return state.having(alive=False)
            return members_dead

        if members_spec == "self":
            return make_self_scope_members()

        if members_spec == "dynamic_whisper":
            return make_dynamic_whisper_members()

        # dict 形式
        if isinstance(members_spec, dict):
            # {type: "self"} 或 {type: "dynamic_whisper"}
            spec_type = members_spec.get("type")
            if spec_type == "self":
                return make_self_scope_members()
            if spec_type == "dynamic_whisper":
                return make_dynamic_whisper_members()

            # {filter: {attr: value, ...}}
            if "filter" in members_spec:
                filter_spec = members_spec["filter"]
                evaluator = self._evaluator

                def members_filter(state: State) -> set:
                    """按 filter 规格过滤成员。"""
                    return evaluator.filter_entities(filter_spec, state)

                return members_filter

        # 兜底：全员（含 GAME 之外）
        print(f"[YamlCompiler] 警告：无法识别的 members 规格 {members_spec!r}，使用 all")

        def members_fallback(state: State) -> set:
            """兜底：返回全部非 GAME 实体。"""
            return {e for e in state.all_entities() if e != "GAME"}

        return members_fallback

    def _compile_casting(self, players_spec: dict, roles: list) -> Any:
        """
        编译 players.casting 字段，返回 ShuffleDeal 或 FixedDeal 实例。

        参数：
          players_spec — players YAML 字典
          roles        — Role 对象列表（用于 FixedDeal 可以通过名字查角色）

        返回：
          ShuffleDeal 或 FixedDeal 实例
        """
        casting_spec = players_spec.get("casting", {})
        cast_type = casting_spec.get("type", "shuffle")

        if cast_type == "shuffle":
            distribution = casting_spec.get("distribution", {})
            # distribution 值可能是字符串（来自 YAML 模板展开后的引号值，如 "3"），统一转 int
            distribution = {k: int(v) for k, v in distribution.items()}
            return ShuffleDeal(role_counts=distribution)

        if cast_type == "fixed":
            assignment = casting_spec.get("assignment", {})
            return FixedDeal(assignment=assignment)

        raise ValueError(f"未知的 casting 类型：{cast_type}")

    def _compile_player_config(self, players_spec: dict) -> PlayerConfig:
        """
        编译 players 字段中与席位资料相关的配置。

        规则：
          - ids 不写时按 Player_1..Player_N 自动生成
          - display_names 不写时生成 "1号玩家" 这类默认显示名
          - initial_attrs 不写时为空字典；淘汰制剧本应显式写 alive: true
        """
        count = int(players_spec.get("count", 0))
        ids = players_spec.get("ids")
        if ids is None:
            ids = [f"Player_{i}" for i in range(1, count + 1)]

        display_names = dict(players_spec.get("display_names") or {})
        for index, player_id in enumerate(ids, start=1):
            display_names.setdefault(player_id, f"{index}号玩家")

        nicknames = dict(players_spec.get("nicknames") or {})
        initial_attrs = dict(players_spec.get("initial_attrs") or {})

        return PlayerConfig(
            count=count,
            ids=list(ids),
            display_names=display_names,
            nicknames=nicknames,
            initial_attrs=initial_attrs,
        )

    def _compile_flow(self, flow_spec: dict) -> Any:
        """
        编译 flow 字段，返回流程对象。

        参数：
          flow_spec — flow YAML 字典

        返回：
          Sequence 或 StateMachineFlow 对象
        """
        flow_type = flow_spec.get("type", "sequence")
        if flow_type == "state_machine":
            return self._compile_state_machine_flow(flow_spec)

        loop = flow_spec.get("loop", True)
        scenes_spec = flow_spec.get("scenes", [])

        scenes = []
        for scene_spec in scenes_spec:
            scene = self._compile_scene(scene_spec)
            scenes.append(scene)

        return Sequence(scenes=scenes, loop=loop)

    def _compile_state_machine_flow(self, flow_spec: dict) -> StateMachineFlow:
        """
        编译 state_machine flow。

        YAML 形态：
          flow:
            type: state_machine
            initial: night
            states:
              night:
                scenes: [...]
                transitions:
                  - to: day
                    when: {...}
        """
        initial = flow_spec["initial"]
        states_spec = flow_spec.get("states", {})
        states = {}
        for state_name, state_spec in states_spec.items():
            scene_specs = state_spec.get("scenes", [])
            scenes = [self._compile_scene(scene_spec) for scene_spec in scene_specs]
            transitions = []
            for transition_spec in state_spec.get("transitions", []) or []:
                when_spec = transition_spec.get("when")
                when_fn = self._compile_when(when_spec) if when_spec else None
                transitions.append({
                    "to": transition_spec.get("to"),
                    "when": when_fn,
                })
            states[state_name] = {
                "scenes": scenes,
                "entry": self._compile_flow_effects(state_spec.get("entry_effects", [])),
                "exit": self._compile_flow_effects(state_spec.get("exit_effects", [])),
                "transitions": transitions,
                "terminal": bool(state_spec.get("terminal", False)),
            }
        return StateMachineFlow(initial=initial, states=states)

    def _compile_triggers(self, triggers_spec: list) -> list:
        """
        编译脚本级 triggers。

        YAML 示例：
          triggers:
            - on: death
              effects:
                - type: for_each
                  items: {state: item.entity}
        """
        if not triggers_spec:
            return []
        assert isinstance(triggers_spec, list), "triggers 必须是列表"
        compiled = []
        for trigger_spec in triggers_spec:
            compiled.append(self._compile_trigger(trigger_spec))
        return compiled

    def _compile_trigger(self, trigger_spec: dict) -> Callable:
        """编译单个 trigger。"""
        assert isinstance(trigger_spec, dict), "trigger 条目必须是字典"
        # PyYAML 的 YAML 1.1 解析会把裸 key `on:` 当成布尔 True。
        # 这里兼容两种形态，让剧本作者可以自然书写 `on: death`。
        trigger_type = trigger_spec.get("on", trigger_spec.get(True))
        effects_spec = trigger_spec.get("effects", [])
        executor = self._executor

        def trigger_fn(mutations: list, state: State, writer: StateWriter) -> None:
            """对一批 mutation 执行当前 trigger。"""
            for mutation in mutations:
                event = _mutation_to_trigger_event(mutation)
                if event is None:
                    continue
                if not _trigger_event_matches(trigger_spec, trigger_type, event):
                    continue
                executor.execute_all(
                    effects=effects_spec,
                    state=state,
                    writer=writer,
                    responses=[],
                    actor=event.get("entity"),
                    extra={"item": event, "__state": state, "script_extensions": getattr(self, "_current_extensions", {}), "script_rule_set": getattr(self, "_current_rule_set", None)},
                )

        return trigger_fn

    def _compile_flow_effects(self, effects_spec: list) -> Any:
        """
        编译 flow 节点 entry_effects / exit_effects。

        返回 Callable[[State, StateWriter], None] 或 None。
        """
        if not effects_spec:
            return None
        executor = self._executor

        def effect_fn(state: State, writer: StateWriter) -> None:
            """执行流程节点 effects。"""
            executor.execute_all(
                effects=effects_spec,
                state=state,
                writer=writer,
                responses=[],
                actor=None,
                extra={"__state": state, "script_extensions": getattr(self, "_current_extensions", {}), "script_rule_set": getattr(self, "_current_rule_set", None)},
            )

        return effect_fn

    def _compile_scene(self, spec: dict) -> Scene:
        """
        编译新版 scene 字典，返回运行时 Scene 对象。

        编译入口只接受新版 DSL 字段，不做旧语法兼容。
        运行时对象字段负责调度执行，不代表 YAML 语法入口。
        """
        name = spec["name"]
        scope = spec.get("scope", "public")
        scene_type = spec["scene_type"]
        display_name = spec.get("display_name", name)

        dialogue_policy = spec.get("dialogue_policy", {}) or {}
        action_policy = spec.get("action_policy", {}) or {}
        response_spec = spec.get("response", {}) or {}
        resolution = spec.get("resolution", {}) or {}

        dialogue_mode = dialogue_policy.get("mode", self._default_dialogue_mode(scene_type))
        action_kind = action_policy.get("kind", self._default_action_kind(scene_type))

        publication_message_specs = self._compile_publication_messages(
            self._scene_publication_messages(spec),
            scope,
        )
        publication_view_specs = self._compile_publication_views(
            self._scene_publication_views(spec),
            scope,
        )
        publication_spec = dict(spec.get("publication", {}) or {})
        publication_spec["messages"] = publication_message_specs
        publication_spec["views"] = publication_view_specs
        publication_spec["disclosures"] = self._scene_publication_disclosures(spec)

        # 编译参与者。新版 DSL 使用 participants，运行时仍复用 Scene.participants。
        participants_spec = spec.get("participants", {"filter": {"alive": True}})
        participants_fn = self._compile_participants(participants_spec)

        # 编译 cue 函数或字符串。response.cue 是任务提示；response.prompt 仅是输出格式要求。
        cue_spec = self._scene_cue_spec(spec)
        cue = self._compile_cue(cue_spec)

        # 编译响应模型。response 决定结构化数据协议，action_policy 提供动作语义默认值。
        collect_model = self._build_collect_model(
            scene_type=scene_type,
            action_policy=action_policy,
            response_spec=response_spec,
        )
        response_prompt = self._compile_response_prompt(response_spec)
        candidates_spec = spec.get("candidates")
        candidates_fn = self._compile_candidates(candidates_spec)
        candidate_constraints = self._compile_candidate_constraints(candidates_spec)

        # 编译本幕结算。
        effects_spec = resolution.get("effects", []) or []
        selection_spec = resolution.get("selection", {}) or {}
        on_result_fn = self._compile_on_result(
            effects_spec=effects_spec,
            action_kind=action_kind,
            selection_spec=selection_spec,
        )

        # 编译场景触发条件。
        when_spec = spec.get("when")
        when_fn = self._compile_when(when_spec) if when_spec else None

        # 编译对话/行动调度策略。
        turn = self._compile_dialogue_policy(dialogue_policy, scene_type)

        # 编译 until 条件。新版 until 放在 dialogue_policy.until。
        until_spec = dialogue_policy.get("until")
        until_fn = self._compile_until(until_spec) if until_spec else None

        return Scene(
            name=name,
            scope=scope,
            participants=participants_fn,
            cue=cue,
            dialogue_policy=turn,
            response_model=collect_model,
            response_prompt=response_prompt,
            candidates=candidates_fn,
            candidate_constraints=candidate_constraints,
            on_result=on_result_fn,
            when=when_fn,
            until=until_fn,
            display_name=display_name,
            announce_response_cue=self._scene_announce_cue(spec),
            response_messages=None,
            publication=publication_spec,
        )

    def _compile_publication_views(self, views_spec: Any, default_scope: str) -> list:
        """编译 publication.views，保留 projector/data 规格供运行时按最新 State 投影。"""
        if not views_spec:
            return []
        assert isinstance(views_spec, list), "publication.views 必须是列表"
        views = []
        for item in views_spec:
            if not isinstance(item, dict):
                continue
            view_spec = dict(item)
            view_spec.setdefault("audience", default_scope)
            view_spec.setdefault("projector", "core.views.inline")
            views.append(view_spec)
        return views

    def _compile_publication_messages(self, messages_spec: Any, default_scope: str) -> list:
        """
        编译 publication.messages。

        返回值中 text 是已编译 cue（字符串或 Callable），Director 运行时再按
        当前 State 解析，保证公告可以读取最新结算结果。
        """
        messages = []
        for item in self._normalize_publication_messages(messages_spec):
            if isinstance(item, str):
                messages.append({
                    "audience": default_scope,
                    "text": self._compile_cue(item),
                })
                continue
            if not isinstance(item, dict):
                continue
            audience = item.get("audience") or item.get("scope") or default_scope
            messages.append({
                "audience": audience,
                "text": self._compile_cue(self._publication_message_to_cue(item)),
            })
        return messages

    def _compile_candidate_constraints(self, candidates_spec: Any) -> dict:
        """提取 candidates 上的通用校验约束。"""
        if not isinstance(candidates_spec, dict):
            return {}
        constraints = {}
        if "count" in candidates_spec:
            count_spec = candidates_spec["count"]
            if count_spec == "all_candidates" or isinstance(count_spec, dict):
                constraints["count"] = count_spec
            else:
                constraints["count"] = int(count_spec)
        if "min" in candidates_spec:
            min_spec = candidates_spec["min"]
            constraints["min"] = min_spec if isinstance(min_spec, dict) else int(min_spec)
        if "max" in candidates_spec:
            max_spec = candidates_spec["max"]
            constraints["max"] = max_spec if isinstance(max_spec, dict) else int(max_spec)
        if "distinct" in candidates_spec:
            constraints["distinct"] = bool(candidates_spec["distinct"])
        return constraints

    def _compile_candidates(self, candidates_spec: Any) -> Callable | None:
        """
        编译 candidates 字段，返回 Callable[[State, actor], list[str]]。

        当前用于给 LLM/MockActor 提供候选目标提示；规则校验仍由 effects/state 兜底。
        actor 参数用于 candidates.when 的逐候选条件，例如 candidate != actor。
        """
        if not candidates_spec:
            return None
        resolver = self._candidate_resolver

        def candidates_fn(state: State, actor: str | None = None) -> list:
            """根据当前 state 解析候选集。"""
            return resolver.resolve(
                candidates_spec,
                state,
                last_responses=[],
                actor=actor,
            )

        return candidates_fn

    def _compile_when(self, when_spec: Any) -> Callable:
        """
        编译 scene.when 字段，返回 Callable[[State], bool]。

        when 是整幕级别的触发条件；条件不满足时，本幕在 participants/cue 之前跳过。
        """
        evaluator = self._evaluator

        def when_fn(state: State) -> bool:
            """检查当前幕是否应触发。"""
            try:
                return evaluator.evaluate(when_spec, state, actor=None)
            except Exception as exc:
                print(f"[YamlCompiler] scene.when 条件求值失败：{exc}")
                return False

        return when_fn

    def _compile_participants(self, spec: Any) -> Callable:
        """
        编译 participants 字段，返回 Callable[[State], set[str] | list[str]]。

        支持：
          - {filter: {attr: value, ...}} — 属性过滤
          - {filter: {...}, when: [cond_list]} — 带条件的过滤
          - {filter: {...}, min: N} — 至少 N 人时才执行
          - {from_state: GAME.xxx, ordered: true} — 从状态列表读取并保留顺序
          - "all" — 全员

        参数：
          spec — participants YAML 字段值

        返回：
          接受 State 返回演员名集合的函数
        """
        # 字符串 "all"
        if spec == "all":
            def participants_all(state: State) -> set:
                """返回所有非 GAME 实体。"""
                return {e for e in state.all_entities() if e != "GAME"}
            return participants_all

        # 列表简写：participants: [Player_1, Player_2]
        if isinstance(spec, list):
            names = list(spec)

            def participants_static_list(state: State) -> list:
                """返回 DSL 中声明的固定参与者列表。"""
                return list(names)

            return participants_static_list

        if not isinstance(spec, dict):
            # 无法识别，返回空集（空场跳幕）
            def participants_empty(state: State) -> set:
                """无法识别的 participants 规格，返回空集（跳幕）。"""
                return set()
            return participants_empty

        static_spec = spec.get("static")
        if static_spec is not None:
            names = list(static_spec) if isinstance(static_spec, list) else []

            def participants_static(state: State) -> list:
                """返回 participants.static 声明的固定参与者列表。"""
                return list(names)

            return participants_static

        from_state_path = spec.get("from_state") or spec.get("from_state_set")
        ordered = bool(spec.get("ordered", False))
        filter_spec = spec.get("filter", {})
        # when 支持两种写法：
        #   列表写法（旧）：when: [{cond1}, {cond2}]  → 所有条件 AND
        #   单个条件写法（新）：when: {all: [...]}     → 单个复合条件
        # 统一规范化为列表，方便后续统一迭代处理。
        when_raw = spec.get("when")
        if when_raw is None:
            when_conditions = None
        elif isinstance(when_raw, list):
            when_conditions = when_raw
        else:
            # 单个条件 dict，包装成单元素列表
            when_conditions = [when_raw]
        min_count = spec.get("min")
        evaluator = self._evaluator

        def participants_fn(state: State):
            """
            按 filter / from_state 规格过滤演员，可选 when 条件和 min 人数检查。
            """
            # 来源选择：from_state 优先，否则用属性过滤。
            if from_state_path:
                value = self._values.resolve({"state": from_state_path}, state=state)
                if ordered:
                    if value is None:
                        candidates = []
                    elif isinstance(value, (list, tuple)):
                        candidates = list(value)
                    elif isinstance(value, set):
                        candidates = sorted(value)
                    else:
                        candidates = [value]
                else:
                    if value is None:
                        candidates = set()
                    elif isinstance(value, (list, tuple, set)):
                        candidates = set(value)
                    else:
                        candidates = {value}
            else:
                candidates = evaluator.filter_entities(filter_spec, state)

            # when 条件过滤：每个候选都要通过所有 when 条件
            if when_conditions:
                qualified = [] if ordered else set()
                for entity in candidates:
                    all_pass = True
                    for cond in when_conditions:
                        if not evaluator.evaluate(cond, state, actor=entity, entity=entity):
                            all_pass = False
                            break
                    if all_pass:
                        if ordered:
                            qualified.append(entity)
                        else:
                            qualified.add(entity)
                candidates = qualified

            # min 人数检查：人数不足时返回空集（触发空场跳幕）
            if min_count is not None and len(candidates) < min_count:
                if ordered:
                    return []
                return set()

            return candidates

        return participants_fn

    def _compile_cue(self, template: Any) -> Any:
        """
        编译 cue 字段。

        支持：
          - str：直接返回（含插值占位符时返回函数）
          - dict with "text" 和 "vars"：按 vars 解析脚本自定义变量
          - None/空：返回空字符串

        插值支持：
          - {GAME.xxx}      → state.get_attr("GAME", "xxx")
          - {var_name}      → 从 cue.vars 中显式声明的变量

        参数：
          template — cue YAML 字段值

        返回：
          str 或 Callable[[State], str]
        """
        # None 或空
        if not template:
            return ""

        vars_spec = {}
        if isinstance(template, dict):
            vars_spec = template.get("vars", {}) or {}
            template = template.get("text", "")

        if not isinstance(template, str):
            return str(template)

        # 检查是否含插值占位符
        has_game = bool(re.search(r"\{GAME\.[^}]+\}", template))
        has_vars = bool(vars_spec)

        if not has_game and not has_vars:
            # 纯字符串，直接返回
            return template

        # 含插值，返回函数
        # 用局部变量捕获 template，避免闭包捕获外层变量的常见问题
        cue_template = template

        def cue_fn(state: State) -> str:
            """根据当前 state 插值生成旁白词。"""
            text = cue_template

            for name, spec in vars_spec.items():
                text = text.replace("{" + name + "}", self._render_cue_var(spec, state))

            # 替换 {GAME.xxx}
            def replace_game_attr(match: re.Match) -> str:
                """把 GAME.attr 替换为 state 中的值。"""
                attr = match.group(1)
                value = state.get_attr("GAME", attr)
                return str(value) if value is not None else ""

            text = re.sub(r"\{GAME\.([^}]+)\}", replace_game_attr, text)
            return text

        return cue_fn

    def _render_cue_var(self, spec: Any, state: State) -> str:
        """渲染 cue.vars 中声明的单个变量。"""
        if isinstance(spec, str):
            return str(state.get_attr("GAME", spec, ""))
        if not isinstance(spec, dict):
            return str(spec)

        if "state" in spec:
            value = self._evaluator._resolve_path(spec["state"], state, actor=None)
            return "" if value is None else str(value)

        entity_spec = spec.get("entities")
        if entity_spec is not None:
            entities = self._resolve_cue_entities(entity_spec, state)
            return self._format_cue_entities(entities, spec.get("format", "names"), state)

        return ""

    def _resolve_cue_entities(self, entity_spec: Any, state: State) -> list:
        """按 cue.vars[].entities 查询实体列表。"""
        if entity_spec == "all":
            return sorted(e for e in state.all_entities() if e != "GAME")
        if not isinstance(entity_spec, dict):
            return []

        filter_spec = entity_spec.get("filter", {})
        names = sorted(self._evaluator.filter_entities(filter_spec, state))

        exclude = set(entity_spec.get("exclude", []) or [])
        if exclude:
            names = [name for name in names if name not in exclude]

        sort_by = entity_spec.get("sort_by")
        if sort_by:
            names = sorted(names, key=lambda name: (state.get_attr(name, sort_by, ""), name))
        return names

    def _format_cue_entities(self, entities: list, format_spec: Any, state: State) -> str:
        """格式化实体列表。"""
        if isinstance(format_spec, str):
            if format_spec == "count":
                return str(len(entities))
            return "、".join(entities)

        if not isinstance(format_spec, dict):
            return "、".join(entities)

        fmt_type = format_spec.get("type", "names")
        item_separator = format_spec.get("item_separator", "、")
        empty = format_spec.get("empty", "无")

        if fmt_type == "count":
            return str(len(entities))
        if fmt_type == "names":
            return item_separator.join(entities) if entities else empty
        if fmt_type == "grouped_names":
            group_by = format_spec["group_by"]
            labels = format_spec.get("labels", {}) or {}
            separator = format_spec.get("separator", "；")
            template = format_spec.get("template", "{label}：{names}")

            grouped = {}
            for name in entities:
                key = state.get_attr(name, group_by, "")
                grouped.setdefault(key, []).append(name)

            ordered_keys = list(labels.keys())
            ordered_keys.extend(sorted(k for k in grouped if k not in labels))

            parts = []
            for key in ordered_keys:
                names = grouped.get(key, [])
                if not names and not format_spec.get("show_empty_groups", False):
                    continue
                label = labels.get(key, str(key))
                names_text = item_separator.join(names) if names else empty
                parts.append(template.format(key=key, label=label, names=names_text, count=len(names)))
            return separator.join(parts) if parts else empty
        if fmt_type == "python" or "python" in format_spec or "code" in format_spec:
            return self._format_cue_entities_python(entities, format_spec, state)
        if fmt_type == "expr" or "expr" in format_spec:
            return self._format_cue_entities_expr(entities, format_spec, state)

        return item_separator.join(entities) if entities else empty

    def _format_cue_entities_python(self, entities: list, format_spec: dict, state: State) -> str:
        """
        用受限 Python 格式化 cue 实体列表。

        支持：
          format:
            type: python
            expr: "'、'.join(entities)"

          format:
            type: python
            code: |
              result = "、".join(entities)
        """
        python_spec = format_spec.get("python")
        if isinstance(python_spec, str):
            expr = python_spec
            code = None
        elif isinstance(python_spec, dict):
            expr = python_spec.get("expr")
            code = python_spec.get("code")
        else:
            expr = format_spec.get("expr")
            code = format_spec.get("code")

        labels = format_spec.get("labels", {}) or {}

        def attr(entity: str, key: str, default: Any = None) -> Any:
            value = state.get_attr(entity, key)
            return default if value is None else value

        def group_by(key: str) -> dict:
            grouped = {}
            for entity in entities:
                group_key = attr(entity, key, "")
                grouped.setdefault(group_key, []).append(entity)
            return grouped

        safe_builtins = {
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "range": range,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
        }
        env = {
            "attr": attr,
            "count": len(entities),
            "entities": list(entities),
            "format": format_spec,
            "group_by": group_by,
            "labels": labels,
        }
        globals_env = {"__builtins__": safe_builtins, **env}

        if expr is not None:
            return str(eval(expr, globals_env, env))
        if code is not None:
            exec(code, globals_env, env)
            if "result" not in env:
                raise ValueError("cue.vars.format.code 必须设置 result 变量")
            return str(env["result"])
        return ""

    def _format_cue_entities_expr(self, entities: list, format_spec: dict, state: State) -> str:
        """
        LLM 格式化兜底占位。

        当前运行时未接入 LLM；返回 default，避免把自然语言条件误当确定性逻辑。
        """
        import logging
        logging.getLogger(__name__).warning(
            "cue.vars.format.expr 尚未接入 LLM 格式化，返回 default=%s: %s",
            format_spec.get("default", ""),
            format_spec.get("expr"),
        )
        return str(format_spec.get("default", ""))

    def _compile_response_prompt(self, response_spec: Any) -> str:
        """
        提取 response.prompt，作为只发给 Actor 的输出要求。

        Extract response.prompt as an actor-only output instruction.
        """
        if not isinstance(response_spec, dict):
            return ""
        prompt = response_spec.get("prompt", "")
        assert prompt is None or isinstance(prompt, str), "response.prompt 必须是字符串"
        return prompt or ""

    def _response_includes_reason(self, response_spec: Any, schema: str) -> bool:
        """根据 response.include_reason 决定是否追加 reason 字段。"""
        default_by_schema = {
            "vote": True,
            "choose": True,
            "action": False,
            "target": True,
            "targets": True,
            "rating": True,
            "move": False,
            "card_action": True,
        }
        include_reason = default_by_schema.get(schema, False)
        if isinstance(response_spec, dict) and "include_reason" in response_spec:
            override = response_spec["include_reason"]
            assert isinstance(override, bool), "response.include_reason 必须是布尔值"
            include_reason = override
        return include_reason

    def _action_target_mode(self, action_policy: Any) -> str:
        """读取 action_policy.target 配置。"""
        if not isinstance(action_policy, dict):
            return "none"
        target_mode = action_policy.get("target", "none")
        assert target_mode in ("none", "optional", "required"), (
            "action_policy.target 必须是 none、optional 或 required"
        )
        return target_mode

    def _with_optional_reason(self, response_spec: Any, schema: str, fields: dict) -> dict:
        """按 response.include_reason 追加 reason 字段。"""
        if self._response_includes_reason(response_spec, schema):
            fields["reason"] = (str, Field(..., description="你的选择理由，一两句话"))
        return fields

    def _make_vote_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建投票响应模型。"""
        return create_model(
            "VoteModel",
            **self._with_optional_reason(
                response_spec,
                "vote",
                {"vote": (str, Field(..., description="你选择的投票目标"))},
            ),
        )

    def _make_choose_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建互选响应模型。"""
        return create_model(
            "MutualVoteModel",
            **self._with_optional_reason(
                response_spec,
                "choose",
                {"choose": (str, Field(..., description="你选择的对象"))},
            ),
        )

    def _make_action_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建是否行动响应模型。"""
        target_mode = self._action_target_mode(action_policy)
        fields = {"action": (bool, Field(..., description="是否执行该行动"))}
        if target_mode == "optional":
            fields["target"] = (Optional[str], Field(None, description="行动目标；无需目标时填 null"))
        elif target_mode == "required":
            fields["target"] = (str, Field(..., description="行动目标"))
        return create_model(
            "ActionModel",
            **self._with_optional_reason(response_spec, "action", fields),
        )

    def _make_target_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建单目标选择响应模型。"""
        return create_model(
            "ChooseTargetModel",
            **self._with_optional_reason(
                response_spec,
                "target",
                {"target": (str, Field(..., description="你选择的目标"))},
            ),
        )

    def _make_targets_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建多目标选择响应模型。"""
        return create_model(
            "ChooseManyModel",
            **self._with_optional_reason(
                response_spec,
                "targets",
                {"targets": (list[str], Field(..., description="你选择的多个目标"))},
            ),
        )

    def _make_rating_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建评分响应模型。"""
        return create_model(
            "RatingModel",
            **self._with_optional_reason(
                response_spec,
                "rating",
                {"rating": (int, Field(..., description="评分整数"))},
            ),
        )

    def _make_move_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建棋盘动作响应模型。"""
        return create_model(
            "MoveModel",
            move=(dict, Field(..., description="棋盘动作，例如 position 或 from/to")),
        )

    def _make_card_action_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建卡牌动作响应模型。"""
        return create_model(
            "CardActionModel",
            **self._with_optional_reason(
                response_spec,
                "card_action",
                {"card_action": (dict, Field(..., description="卡牌动作数据"))},
            ),
        )

    def _make_custom_model(self, response_spec: dict, action_policy: dict) -> Any:
        """创建自定义响应模型。"""
        return self._build_custom_response_model(response_spec.get("fields", {}), response_spec)

    def _build_collect_model(
        self,
        scene_type: str,
        action_policy: Any,
        response_spec: Any,
    ) -> Any:
        """
        根据新版 response/action_policy 动态创建 Pydantic 响应模型。

        scene_type 只提供默认值；真正的数据协议由 response.schema 决定。
        """
        action_policy = action_policy if isinstance(action_policy, dict) else {}
        response_spec = response_spec if isinstance(response_spec, dict) else {}
        action_kind = action_policy.get("kind", self._default_action_kind(scene_type))
        response_mode = response_spec.get("mode", self._default_response_mode(scene_type))
        response_schema = response_spec.get("schema", self._default_response_schema(scene_type, action_kind))

        if response_mode in ("none", "text") or response_schema in (None, "none", "text"):
            return None

        if isinstance(response_schema, dict):
            return self._build_custom_response_model(response_schema, response_spec)

        return self._dsl_registry.create_response_model(
            response_schema,
            response_spec,
            action_policy,
        )

    def _build_custom_response_model(self, fields_spec: Any, response_spec: dict) -> Any:
        """构建简单自定义响应模型。当前支持 string/int/bool/list/dict 类型声明。"""
        if not isinstance(fields_spec, dict) or not fields_spec:
            return None
        type_map = {
            "string": str,
            "str": str,
            "int": int,
            "integer": int,
            "bool": bool,
            "boolean": bool,
            "list": list,
            "dict": dict,
        }
        fields = {}
        for field_name, field_spec in fields_spec.items():
            if isinstance(field_spec, str):
                field_type = type_map.get(field_spec, str)
                required = True
                description = field_name
            elif isinstance(field_spec, dict):
                field_type = type_map.get(str(field_spec.get("type", "string")), str)
                required = bool(field_spec.get("required", True))
                description = field_spec.get("description", field_name)
            else:
                field_type = str
                required = True
                description = field_name
            default = ... if required else None
            fields[field_name] = (field_type, Field(default, description=description))
        return create_model("CustomResponseModel", **fields)

    def _compile_on_result(
        self,
        effects_spec: list,
        action_kind: str,
        selection_spec: dict | None = None,
    ) -> Any:
        """
        编译 effects 列表 + action_kind，返回 on_result 回调函数或 None。

        vote / mutual_vote 动作会自动进行选择统计，然后再执行 effects。
        """
        executor = self._executor
        selection_spec = selection_spec or {}
        needs_tally = action_kind in ("vote", "mutual_vote")
        if not needs_tally and not effects_spec:
            return None

        def on_result_fn(responses: list, state: State, writer: StateWriter) -> None:
            winner = None
            selection_result = None
            if needs_tally and responses:
                legacy_tally_type = "MutualVote" if action_kind == "mutual_vote" else "Vote"
                selection_result = _tally_votes(
                    responses,
                    legacy_tally_type,
                    selection_spec,
                    state,
                    self._values,
                )
                winner = selection_result.get("winner")
                print(
                    "[YamlCompiler] 选择统计结果："
                    f"winner={winner}, is_tie={selection_result.get('is_tie')}, "
                    f"counts={selection_result.get('counts')}"
                )
            if effects_spec:
                executor.execute_all(
                    effects=effects_spec,
                    state=state,
                    writer=writer,
                    responses=responses,
                    actor=None,
                    extra={
                        "winner": winner,
                        "selection_result": selection_result,
                        "__state": state,
                        "script_extensions": getattr(self, "_current_extensions", {}),
                        "script_rule_set": getattr(self, "_current_rule_set", None),
                    },
                )

        return on_result_fn

    def _compile_dialogue_policy(self, dialogue_policy: dict, scene_type: str) -> Any:
        """编译新版 dialogue_policy，返回运行时策略对象。"""
        assert isinstance(dialogue_policy, dict), "dialogue_policy 必须是字典"
        mode = dialogue_policy.get("mode", self._default_dialogue_mode(scene_type))
        return self._dsl_registry.create_dialogue_policy(mode, dialogue_policy)

    def _compile_referee(self, referee_spec: dict) -> Callable:
        """
        编译 referee 字段，返回裁判函数。

        裁判函数签名：def fn(state: State) -> str | None
        返回 None 表示未分胜负，返回字符串表示胜负公告。

        参数：
          referee_spec — referee YAML 字典，含 win_conditions 列表

        返回：
          Callable[[State], str | None]
        """
        win_conditions = referee_spec.get("win_conditions", [])
        evaluator = self._evaluator

        def referee_fn(state: State) -> Any:
            """
            检查所有胜利条件，返回第一个满足条件的公告文本，否则返回 None。

            参数：
              state — 当前游戏状态

            返回：
              str 或 None
            """
            for win_cond in win_conditions:
                # 支持两种字段名风格：
                #   旧风格：condition / announcement
                #   YAML 剧本风格：when / message
                if "condition" in win_cond:
                    raise ValueError("referee.win_conditions[].condition 已删除，请改用 when")
                condition = win_cond.get("when")
                announcement = (
                    win_cond.get("announcement")
                    or win_cond.get("message")
                    or "游戏结束"
                )

                if condition is None:
                    continue

                try:
                    if evaluator.evaluate(condition, state, actor=None):
                        print(f"[Referee] 胜利条件满足：{announcement}")
                        return announcement
                except Exception as exc:
                    # 裁判条件求值失败，记录日志但不崩溃
                    print(f"[Referee] 条件求值失败：{exc}，条件：{condition}")

            return None

        return referee_fn

    def _compile_until(self, until_spec: Any) -> Callable:
        """
        编译 until 字段，返回 Callable[[State], bool]。

        参数：
          until_spec — until YAML 字段值（条件字典）

        返回：
          Callable[[State], bool]
        """
        evaluator = self._evaluator

        def until_fn(state: State) -> bool:
            """检查 until 条件是否满足。"""
            try:
                return evaluator.evaluate(until_spec, state, actor=None)
            except Exception as exc:
                print(f"[YamlCompiler] until 条件求值失败：{exc}")
                return False

        return until_fn

    def validate_file(self, yaml_path: str, params: dict = None) -> list:
        """
        读取文件并展开 params 后做 validate。
        便捷方法，供 run.py 调用。

        参数：
          yaml_path — YAML 文件路径
          params    — 运行时参数（覆盖 YAML 内 defaults）

        返回：
          错误列表，空列表 = 合法
        """
        with open(yaml_path, encoding="utf-8") as f:
            raw_text = f.read()
        doc = yaml.safe_load(raw_text)
        resolved_params = self._resolve_params(doc, params or {})
        if resolved_params:
            raw_text = self._expand_params(raw_text, resolved_params)
            doc = yaml.safe_load(raw_text)
        return self.validate(doc)


# =============================================================================
# 内部辅助函数（模块级，不属于 YamlCompiler 类）
# =============================================================================


def _tally_votes(
    responses: list,
    scene_type: str,
    selection_spec: dict | None = None,
    state: State | None = None,
    value_resolver: ValueResolver | None = None,
) -> dict:
    """
    统计投票响应，返回完整选择结果对象。

    默认兼容旧行为：得票相同时按字母顺序取第一个。
    新脚本可通过 selection.tie_policy 改成 no_winner / all_tied / runoff。

    参数：
      responses  — 本幕所有响应字典列表，每个 response["data"] 含投票字段
      scene_type — "Vote"（用 "vote" 字段）或 "MutualVote"（用 "choose" 字段）

    返回：
      dict — selection_result，含 winner/counts/is_tie/tied_candidates/max_score 等
    """
    selection_spec = selection_spec or {}
    value_resolver = value_resolver or ValueResolver()

    # 根据 scene_type 选择票字段名
    if scene_type == "Vote":
        vote_field = "vote"
    else:
        vote_field = "choose"

    # 统计票数
    tally: dict = {}
    for response in responses:
        data = response.get("data") or {}
        target = data.get(vote_field)
        if target:
            weight = _resolve_vote_weight(
                selection_spec.get("weight"),
                response,
                state,
                value_resolver,
            )
            tally[target] = tally.get(target, 0) + weight

    if not tally:
        return {
            "winner": None,
            "has_winner": False,
            "is_tie": False,
            "tied_candidates": [],
            "counts": {},
            "max_score": 0,
            "tie_policy": selection_spec.get("tie_policy", "alphabetical"),
        }

    # 取最多票的目标（相同票数按字母顺序取第一个）
    max_votes = max(tally.values())
    candidates = sorted(k for k, v in tally.items() if v == max_votes)
    is_tie = len(candidates) > 1
    tie_policy = selection_spec.get("tie_policy", "alphabetical")
    allowed_tie_policies = {"alphabetical", "no_winner", "all_tied", "runoff"}
    if tie_policy not in allowed_tie_policies:
        raise ValueError(f"未知 tie_policy: {tie_policy}")
    winner = candidates[0]

    if is_tie and tie_policy in ("no_winner", "runoff"):
        winner = None
    elif is_tie and tie_policy == "all_tied":
        winner = list(candidates)

    return {
        "winner": winner,
        "has_winner": winner is not None,
        "is_tie": is_tie,
        "tied_candidates": candidates if is_tie else [],
        "counts": tally,
        "max_score": max_votes,
        "tie_policy": tie_policy,
    }


def _resolve_vote_weight(
    weight_spec: Any,
    response: dict,
    state: State | None,
    value_resolver: ValueResolver,
) -> float:
    """
    解析单票权重。

    支持：
      selection:
        weight:
          state: actor.vote_weight
          default: 1
    """
    if not weight_spec:
        return 1
    if isinstance(weight_spec, (int, float)):
        return weight_spec
    if isinstance(weight_spec, dict):
        default = weight_spec.get("default", 1)
        value = value_resolver.resolve(
            weight_spec,
            state=state,
            responses=[response],
            actor=response.get("actor"),
            extra={"__state": state},
        )
        if value is None:
            value = default
        return float(value)
    raise ValueError(f"selection.weight 必须是数字或字典，收到 {type(weight_spec)}")


def _mutation_to_trigger_event(mutation: Any) -> dict | None:
    """
    把底层 Mutation 转成 trigger 可读的事件字典。

    当前支持 SetAttr。关系类 Mutation 后续也可以在这里扩展，不影响 trigger DSL。
    """
    if isinstance(mutation, SetAttr):
        return {
            "type": "attr_changed",
            "entity": mutation.entity,
            "attr": mutation.key,
            "value": mutation.value,
        }
    return None


def _trigger_event_matches(trigger_spec: dict, trigger_type: str, event: dict) -> bool:
    """
    判断事件是否命中 trigger。
    """
    if trigger_type == "death":
        if not (
            event.get("type") == "attr_changed"
            and event.get("attr") == "alive"
            and event.get("value") is False
        ):
            return False
    elif trigger_type == "attr_changed":
        if event.get("type") != "attr_changed":
            return False
    else:
        raise ValueError(f"未知 trigger on 类型: {trigger_type}")

    if "entity" in trigger_spec and event.get("entity") != trigger_spec["entity"]:
        return False
    if "attr" in trigger_spec and event.get("attr") != trigger_spec["attr"]:
        return False
    if "equals" in trigger_spec and event.get("value") != trigger_spec["equals"]:
        return False
    if "not_equals" in trigger_spec and event.get("value") == trigger_spec["not_equals"]:
        return False
    return True
