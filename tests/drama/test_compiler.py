# tests/drama/test_compiler.py
"""YamlCompiler 测试。"""
import sys, os, textwrap
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import yaml
from drama_engine.core.dsl.compiler import YamlCompiler

compiler = YamlCompiler()

def _yaml(text: str) -> dict:
    return yaml.safe_load(textwrap.dedent(text))

def test_validate_minimal_valid():
    doc = _yaml("""
        meta:
          title: 测试
        roles:
          - name: villager
            display_name: 村民
            faction: good
            brief: 你是村民
        concepts:
          roles:
            villager:
              display_name: 村民
              description: 普通玩家
          factions:
            good:
              display_name: 好人
              description: 好人阵营
          scopes:
            public:
              display_name: 公开
              description: 全员可见
        players:
          count: 2
          casting:
            type: shuffle
            distribution:
              villager: 2
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: chat
              display_name: 聊天
              scene_type: speak
              scope: public
              dialogue_policy: {mode: sequential}
              participants:
                filter: {alive: true}
        referee:
          win_conditions: []
    """)
    errors = compiler.validate(doc)
    assert errors == [], f"期望无错误，实际: {errors}"

def test_validate_missing_roles():
    doc = _yaml("""
        meta: {title: 测试}
        players: {count: 2, casting: {type: shuffle, distribution: {villager: 2}}}
        scopes: [{name: public, members: all}]
        flow: {loop: false, scenes: []}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("roles" in e for e in errors), f"期望包含 roles 错误，实际: {errors}"

def test_validate_unknown_scene_type():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: unknown_type
              scope: public
              dialogue_policy: {mode: sequential}
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("unknown_type" in e for e in errors)


def test_validate_response_options_type_errors():
    """response/action_policy 配置字段类型错误时，应给出明确校验错误。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        concepts:
          roles: {v: {display_name: v, description: b}}
          factions: {good: {display_name: good, description: b}}
          scopes: {public: {display_name: public, description: b}}
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: action
              scope: public
              dialogue_policy: {mode: single}
              response:
                include_reason: "no"
                prompt: ["bad"]
              action_policy:
                target: bad
                input:
                  widget: [bad]
                  timeout_seconds: -1
                  allow_change: "yes"
                  reveal_progress: "no"
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("response.include_reason" in e for e in errors)
    assert any("response.prompt" in e for e in errors)
    assert any("action_policy.target" in e for e in errors)
    assert any("action_policy.input.widget" in e for e in errors)
    assert any("action_policy.input.timeout_seconds" in e for e in errors)
    assert any("action_policy.input.allow_change" in e for e in errors)
    assert any("action_policy.input.reveal_progress" in e for e in errors)








def test_validate_game_pack_and_rule_set_contract():
    """顶层 game_pack/rule_set 应只做声明校验，不承载具体游戏原语。"""
    valid_doc = _yaml("""
        meta: {title: 测试}
        game_pack:
          plugin: builtin.party.free_discussion
          version: "0.1"
          config: {}
        extensions:
          board: {enabled: true}
        rule_set:
          plugin: builtin.board.generic
          version: "0.1"
          config: {}
        concepts:
          roles: {v: {display_name: 村民, description: 普通玩家}}
          factions: {good: {display_name: 好人, description: 好人阵营}}
          scopes: {public: {display_name: 公开, description: 全员可见}}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(valid_doc)
    assert errors == []

    script = compiler.compile_doc(valid_doc)
    assert script.game_pack["plugin"] == "builtin.party.free_discussion"
    assert script.rule_set["plugin"] == "builtin.board.generic"


def test_validate_game_pack_and_rule_set_errors():
    """game_pack/rule_set 非法声明应给出清晰错误。"""
    invalid_doc = _yaml("""
        meta: {title: 测试}
        game_pack:
          plugin: marketplace.unknown
          version: 1
          config: bad
        rule_set:
          plugin: builtin.cards.generic
          version: 1
          config: bad
        concepts:
          roles: {v: {display_name: 村民, description: 普通玩家}}
          factions: {good: {display_name: 好人, description: 好人阵营}}
          scopes: {public: {display_name: 公开, description: 全员可见}}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(invalid_doc)

    assert any("game_pack.plugin 'marketplace.unknown' 未注册" in e for e in errors)
    assert any("game_pack.version" in e for e in errors)
    assert any("game_pack.config" in e for e in errors)
    assert any("rule_set.plugin 'builtin.cards.generic' 缺少 extensions" in e for e in errors)
    assert any("rule_set.version" in e for e in errors)
    assert any("rule_set.config" in e for e in errors)

def test_validate_extensions_contract():
    """顶层 extensions 应识别通用 domain extension 声明。"""
    valid_doc = _yaml("""
        meta: {title: 测试}
        extensions:
          board:
            enabled: true
            version: "0.1"
            config: {}
          cards: {}
        concepts:
          roles: {v: {display_name: 村民, description: 普通玩家}}
          factions: {good: {display_name: 好人, description: 好人阵营}}
          scopes: {public: {display_name: 公开, description: 全员可见}}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    assert compiler.validate(valid_doc) == []

    invalid_doc = _yaml("""
        meta: {title: 测试}
        extensions:
          unknown_domain:
            enabled: true
          board:
            enabled: yes please
            version: 1
            config: bad
        concepts:
          roles: {v: {display_name: 村民, description: 普通玩家}}
          factions: {good: {display_name: 好人, description: 好人阵营}}
          scopes: {public: {display_name: 公开, description: 全员可见}}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(invalid_doc)

    assert any("extensions.unknown_domain 未注册" in e for e in errors)
    assert any("extensions.board.enabled" in e for e in errors)
    assert any("extensions.board.version" in e for e in errors)
    assert any("extensions.board.config" in e for e in errors)

def test_compile_openchat_dialogue_policy():
    """dialogue_policy.mode=openchat 应编译为 PartySessionRuntime scene 对话策略。"""
    from drama_engine.core.engine import OpenChat

    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 2, casting: {type: shuffle, distribution: {v: 2}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: open-chat
              scene_type: speak
              scope: public
              dialogue_policy:
                mode: openchat
                rounds: 2
                speakers_per_round: 1
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)

    script = compiler.compile_doc(doc)
    policy = script.flow.scenes[0].dialogue_policy

    assert isinstance(policy, OpenChat)
    assert policy.rounds == 2
    assert policy.speakers_per_round == 1


def test_validate_openchat_dialogue_policy_shape():
    """openchat 的轮数参数应做 shape 校验。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: bad-open-chat
              scene_type: speak
              scope: public
              dialogue_policy:
                mode: openchat
                rounds: 0
                speakers_per_round: false
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("dialogue_policy.rounds" in e for e in errors)
    assert any("dialogue_policy.speakers_per_round" in e for e in errors)

def test_compile_action_response_can_omit_reason_and_add_prompt():
    """Action 可通过 response 配置关闭 reason，并追加 Actor 输出提示。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: confirm-action
              display_name: 确认行动
              scene_type: action
              scope: public
              dialogue_policy: {mode: single}
              participants:
                from_state: GAME.players
              response:
                include_reason: false
                prompt: "只判断是否执行，不要补充理由。"
              cue: 是否执行？
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    scene = script.flow.scenes[0]
    assert set(scene.response_model.model_fields) == {"action"}
    assert scene.response_model(action=True).action is True
    assert scene.response_prompt == "只判断是否执行，不要补充理由。"

    from drama_engine.core.engine import State, _resolve_action_cue
    state = State(script.vocab)
    state.register_entity("GAME", {"players": ["P1"]})
    cue_text = _resolve_action_cue(scene, state, actor_name="P1")

    assert "是否执行？" in cue_text
    assert "【输出要求】" in cue_text
    assert "只判断是否执行，不要补充理由。" in cue_text


def test_compile_new_scene_fields_to_runtime_scene():
    """新版 scene 字段应编译为运行时 Scene 字段。"""
    doc = _yaml("""
        meta:
          title: 测试
        roles:
          - name: villager
            faction: good
            brief: 村民
        concepts:
          roles:
            villager:
              display_name: 村民
              description: 普通玩家
          factions:
            good:
              display_name: 好人
              description: 好人阵营
          scopes:
            town:
              display_name: 城镇
              description: 场上频道
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: town
            members: all
        flow:
          loop: false
          scenes:
            - name: report
              scene_type: narration
              scope: town
              participants:
                filter:
                  value: alive
                  equal: true
              dialogue_policy: {mode: none}
              response: {mode: none}
              resolution:
                effects:
                  - type: set_state
                    entity: GAME
                    attr: notice
                    value: done
              publication:
                messages:
                  - audience: town
                    text: "结果：{notice}"
                    vars:
                      notice:
                        entities: all
                        format:
                          type: python
                          code: |
                            result = attr("GAME", "notice")
                disclosures:
                  - timing: after_messages
                    audience: town
                    targets:
                      ref: GAME.players
                    fields: [notice]
        referee:
          win_conditions: []
    """)
    errors = compiler.validate(doc)
    assert errors == []

    script = compiler.compile_doc(doc)
    scene = script.flow.scenes[0]
    assert scene.on_result is not None
    assert scene.publication["messages"][0]["audience"] == "town"
    assert scene.publication["disclosures"][0]["timing"] == "after_messages"

    from drama_engine.core.engine import State, StateWriter
    state = State(script.vocab)
    state.register_entity("GAME", {"notice": "old", "players": ["P1"]})
    writer = StateWriter(state)
    scene.on_result([], state, writer)

    assert state.get_attr("GAME", "notice") == "done"
    assert scene.publication["messages"][0]["text"](state) == "结果：done"




def test_validate_registered_input_widget_and_view_kind():
    """input.widget 与 publication.views.kind 应由 DSL registry 校验。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: bad-ui-contract
              scene_type: action
              scope: public
              participants: {filter: {alive: true}}
              action_policy:
                kind: yes_no
                input:
                  widget: unsupported_widget
              publication:
                views:
                  - id: bad-view
                    kind: unsupported_view
                    audience: public
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("action_policy.input.widget 'unsupported_widget'" in e for e in errors)
    assert any("publication.views[0].kind 'unsupported_view'" in e for e in errors)

def test_compile_publication_views_and_project_view_event():
    """publication.views 应编译为运行时 ViewEvent 投影规格。"""
    doc = _yaml("""
        meta:
          title: 测试
        roles:
          - name: villager
            faction: good
            brief: 村民
        concepts:
          roles:
            villager:
              display_name: 村民
              description: 普通玩家
          factions:
            good:
              display_name: 好人
              description: 好人阵营
          scopes:
            public:
              display_name: 公开
              description: 全员可见
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: report
              scene_type: narration
              scope: public
              dialogue_policy: {mode: none}
              response: {mode: none}
              publication:
                views:
                  - id: phase-info
                    kind: key-value
                    title: 阶段信息
                    audience: public
                    data:
                      rows:
                        - label: 当前阶段
                          value:
                            ref: GAME.phase
        referee:
          win_conditions: []
    """)
    errors = compiler.validate(doc)
    assert errors == []

    script = compiler.compile_doc(doc)
    scene = script.flow.scenes[0]
    assert scene.publication["views"][0]["projector"] == "core.views.inline"

    from drama_engine.core.engine import SetAttr, State, StateWriter
    from drama_engine.core.dsl.plugins import ViewContext

    state = State(script.vocab)
    state.register_entity("GAME", {"phase": "白天"})
    event = script.plugin_registry.project_view(
        scene.publication["views"][0],
        ViewContext(
            state=state,
            scene_name=scene.name,
            audience="public",
            mutation_log=[],
            script_extensions={},
        ),
    )

    assert event["kind"] == "__view__"
    assert event["view_id"] == "phase-info"
    assert event["view_kind"] == "key-value"
    assert event["data"]["rows"][0]["value"] == "白天"


def test_compile_action_can_request_reason_when_needed():
    """Action 默认无 reason，但可显式开启 reason 字段。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: join
              scene_type: action
              scope: public
              dialogue_policy: {mode: single}
              participants:
                from_state: GAME.players
              response:
                include_reason: true
              cue: 是否参加？
        referee:
          win_conditions: []
    """)
    scene = compiler.compile_doc(doc).flow.scenes[0]
    assert set(scene.response_model.model_fields) == {"action", "reason"}
    assert scene.response_model(action=True, reason="想竞选").reason == "想竞选"


def test_compile_action_can_request_optional_target():
    """Action 可通过 action_policy.target=optional 表达可选带目标行动。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: shoot
              scene_type: action
              scope: public
              dialogue_policy: {mode: single}
              participants:
                from_state: GAME.players
              response:
                include_reason: true
              action_policy:
                kind: yes_no
                target: optional
              cue: 是否行动？
        referee:
          win_conditions: []
    """)
    scene = compiler.compile_doc(doc).flow.scenes[0]
    assert set(scene.response_model.model_fields) == {"action", "target", "reason"}
    data = scene.response_model(action=False, target=None, reason="不行动")
    assert data.action is False
    assert data.target is None

def test_validate_scene_references_undefined_scope():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: speak
              scope: nonexistent_scope
              dialogue_policy: {mode: sequential}
              participants: {filter: {alive: true}}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("nonexistent_scope" in e for e in errors)

def test_validate_casting_distribution_sum_matches_player_count():
    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - {name: wolf, display_name: 狼, faction: wolf, brief: b}
          - {name: villager, display_name: 村, faction: good, brief: b}
        players:
          count: 5
          casting:
            type: shuffle
            distribution:
              wolf: 2
              villager: 2
        scopes: [{name: public, members: all}]
        flow: {loop: false, scenes: []}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("角色总数" in e or "distribution" in e for e in errors)

def test_validate_inventory_item_requires_description():
    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - name: witch
            display_name: 女巫
            faction: good
            brief: b
            inventory:
              - item: heal_potion
                display_name: 解药
                count: 1
        players:
          count: 1
          casting: {type: shuffle, distribution: {witch: 1}}
        scopes: [{name: public, members: all}]
        flow: {loop: false, scenes: []}
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("heal_potion" in e and "description" in e for e in errors)

def test_validate_narration_requires_none_dialogue_policy():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: announce
              scene_type: narration
              scope: public
              dialogue_policy: {mode: sequential}
              cue: "公告"
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("narration" in e and "dialogue_policy.mode" in e for e in errors)

def test_validate_scene_when_must_be_dict():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: first-night-only
              scene_type: narration
              scope: public
              dialogue_policy: {mode: none}
              response: {mode: none}
              when: first_round
              cue: "公告"
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("when" in e for e in errors)

def test_compile_scene_when_condition():
    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        players:
          count: 1
          casting: {type: shuffle, distribution: {v: 1}}
        scopes:
          - {name: public, members: all}
        flow:
          loop: false
          scenes:
            - name: first-night-only
              scene_type: narration
              scope: public
              dialogue_policy: {mode: none}
              response: {mode: none}
              when:
                state: GAME.round
                equals: 1
              cue: "首夜公告"
        referee:
          win_conditions: []
    """)
    from drama_engine.core.engine import SetAttr, State, StateWriter

    script = compiler.compile_doc(doc)
    state = State(script.vocab)
    state.register_entity("GAME", {"round": 1})
    assert script.flow.scenes[0].when(state) is True
    StateWriter(state).apply(SetAttr("GAME", "round", 2))
    assert script.flow.scenes[0].when(state) is False

def test_compile_candidates_when_receives_actor_and_candidate():
    from drama_engine.core.engine import State

    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        players:
          count: 2
          casting: {type: shuffle, distribution: {v: 2}}
        scopes:
          - {name: public, members: all}
        flow:
          loop: false
          scenes:
            - name: choose-other
              scene_type: choose
              scope: public
              dialogue_policy: {mode: single}
              participants: {filter: {alive: true}}
              candidates:
                filter: {alive: true}
                when:
                  state: candidate
                  not_equals_state: actor
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    state = State(script.vocab)
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True})
    state.register_entity("P2", {"alive": True})

    assert script.flow.scenes[0].candidates(state, "P1") == ["P2"]


def test_compile_state_machine_flow_transitions():
    """flow.type=state_machine 应编译为状态机流程，并支持 transition.when。"""
    doc = _yaml("""
        meta: {title: 状态机测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        concepts:
          roles:
            v:
              display_name: 村民
              description: 普通玩家
          factions:
            good:
              display_name: 好人
              description: 好人阵营
          scopes:
            public:
              display_name: 公开
              description: 全员可见
        players:
          count: 1
          casting: {type: shuffle, distribution: {v: 1}}
        scopes:
          - {name: public, members: all}
        flow:
          type: state_machine
          initial: day
          states:
            day:
              scenes:
                - name: day-vote
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  cue: "白天"
              transitions:
                - to: pk
                  when:
                    state: GAME.need_pk
                    equals: true
                - to: night
            pk:
              scenes:
                - name: pk-vote
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  cue: "PK"
              transitions:
                - to: night
            night:
              scenes:
                - name: night-fall
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  response:
                    cue: "夜晚"
              transitions:
                - to: day
        referee:
          win_conditions: []
    """)
    from drama_engine.core.engine import SetAttr, State, StateMachineFlow, StateWriter

    errors = compiler.validate(doc)
    assert errors == []

    script = compiler.compile_doc(doc)
    assert isinstance(script.flow, StateMachineFlow)

    state = State(script.vocab)
    state.register_entity("GAME", {"need_pk": False})
    writer = StateWriter(state)
    assert [scene.name for scene in script.flow.next_scenes(state)] == ["day-vote"]
    script.flow.after_batch(state, writer)
    assert [scene.name for scene in script.flow.next_scenes(state)] == ["night-fall"]

    script = compiler.compile_doc(doc)
    state = State(script.vocab)
    state.register_entity("GAME", {"need_pk": False})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "need_pk", True))
    assert [scene.name for scene in script.flow.next_scenes(state)] == ["day-vote"]
    script.flow.after_batch(state, writer)
    assert [scene.name for scene in script.flow.next_scenes(state)] == ["pk-vote"]


def test_compile_referee_conditions_with_check_on_and_include():
    """新版 referee.conditions 支持 check_on/include，并保留 callable 兼容。"""
    doc = _yaml("""
        meta: {title: 裁判 Hook 测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        concepts:
          roles: {v: {display_name: 村民, description: 普通玩家}}
          factions: {good: {display_name: 好人, description: 好人阵营}}
          scopes: {public: {display_name: 公开, description: 全员可见}}
        players:
          count: 1
          casting: {type: shuffle, distribution: {v: 1}}
        scopes:
          - {name: public, members: all}
        flow:
          scenes:
            - name: only-check-here
              scene_type: narration
              scope: public
              dialogue_policy: {mode: none}
              response: {mode: none}
              cue: "检查"
            - name: skip-here
              scene_type: narration
              scope: public
              dialogue_policy: {mode: none}
              response: {mode: none}
              cue: "跳过"
        referee:
          check_on: [after_scene]
          include:
            scenes: [only-check-here]
          conditions:
            - id: done
              message: 结束
              when:
                ref: GAME.done
                op: equals
                value: true
    """)
    from drama_engine.core.engine import State, StateWriter, SetAttr

    errors = compiler.validate(doc)
    assert errors == []

    script = compiler.compile_doc(doc)
    state = State(script.vocab)
    state.register_entity("GAME", {"done": True})
    scene = script.flow.scenes[0]
    skipped_scene = script.flow.scenes[1]

    assert script.referee(state, hook="before_scene", scene=scene) is None
    assert script.referee(state, hook="after_scene", scene=skipped_scene) is None
    assert script.referee(state, hook="after_scene", scene=scene) == "结束"


def test_validate_state_machine_requires_initial_state():
    """state_machine flow 缺 initial 时应报错。"""
    doc = _yaml("""
        meta: {title: 状态机测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          type: state_machine
          states:
            day:
              scenes: []
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("initial" in error for error in errors)


def test_compile_state_machine_entry_exit_effects_and_flow_set_next():
    """state_machine 应支持 entry_effects、exit_effects 和 flow_set_next。"""
    doc = _yaml("""
        meta: {title: 状态机效果测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        concepts:
          roles:
            v:
              display_name: 村民
              description: 普通玩家
          factions:
            good:
              display_name: 好人
              description: 好人阵营
          scopes:
            public:
              display_name: 公开
              description: 全员可见
        players:
          count: 1
          casting: {type: shuffle, distribution: {v: 1}}
        scopes:
          - {name: public, members: all}
        flow:
          type: state_machine
          initial: day
          states:
            day:
              entry_effects:
                - type: increment_state
                  entity: GAME
                  attr: day_entries
                  value: 1
              exit_effects:
                - type: set_state
                  entity: GAME
                  attr: left_day
                  value: true
              scenes:
                - name: boom
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  response:
                    cue: "自爆"
                  resolution:
                    effects:
                      - type: flow_set_next
                        state: night
                - name: should-skip
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  response:
                    cue: "不应执行"
              transitions:
                - to: day
            night:
              scenes:
                - name: night-fall
                  scene_type: narration
                  scope: public
                  dialogue_policy: {mode: none}
                  response: {mode: none}
                  response:
                    cue: "夜晚"
              transitions:
                - to: day
        referee:
          win_conditions: []
    """)
    from drama_engine.core.engine import State, StateWriter

    errors = compiler.validate(doc)
    assert errors == []

    script = compiler.compile_doc(doc)
    state = State(script.vocab)
    state.register_entity("GAME", {})
    writer = StateWriter(state)

    scenes = script.flow.next_scenes(state)
    script.flow.on_batch_start(state, writer)
    assert state.get_attr("GAME", "day_entries") == 1

    scenes[0].on_result([], state, writer)
    should_continue = script.flow.after_scene(scenes[0], state, writer)

    assert should_continue is False
    assert script.flow.current == "night"
    assert state.get_attr("GAME", "left_day") is True
    assert [scene.name for scene in script.flow.next_scenes(state)] == ["night-fall"]


def test_compile_from_state_participants_candidates_and_choose_many():
    """participants/candidates 应支持 from_state，ChooseMany 应生成 targets 模型。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 2
          casting:
            type: fixed
            assignments:
              P1: villager
              P2: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: choose-pair
              scene_type: choose
              scope: public
              dialogue_policy: {mode: single}
              action_policy: {kind: choose_many, target: required}
              response: {mode: structured, schema: targets}
              participants:
                from_state: GAME.active_choosers
              candidates:
                from_state: GAME.pair_candidates
                extra: ["@NO_TARGET"]
                count: all_candidates
                distinct: true
              cue: 请选择两人
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    from drama_engine.core.engine import State
    state = State(script.vocab)
    state.register_entity("GAME", {
        "active_choosers": ["P1"],
        "pair_candidates": ["P1", "P2"],
    })
    state.register_entity("P1", {"alive": True})
    state.register_entity("P2", {"alive": True})
    scene = script.flow.scenes[0]
    assert scene.participants(state) == {"P1"}
    assert scene.candidates(state, "P1") == ["P1", "P2", "NO_TARGET"]
    assert scene.candidate_constraints == {"count": "all_candidates", "distinct": True}
    model = scene.response_model
    assert model(targets=["P1", "P2"], reason="test").targets == ["P1", "P2"]


def test_compile_ordered_from_state_participants_preserves_order():
    """participants.from_state 设置 ordered 时，应保留状态列表顺序。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 3
          casting:
            type: fixed
            assignments:
              P1: villager
              P2: villager
              P3: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: ordered-speech
              scene_type: speak
              scope: public
              dialogue_policy: {mode: sequential}
              participants:
                from_state: GAME.speech_order
                ordered: true
                when:
                  state: actor.alive
                  equals: true
              cue: 请发言
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    from drama_engine.core.engine import State
    state = State(script.vocab)
    state.register_entity("GAME", {"speech_order": ["P3", "P1", "P2"]})
    state.register_entity("P1", {"alive": True})
    state.register_entity("P2", {"alive": False})
    state.register_entity("P3", {"alive": True})
    scene = script.flow.scenes[0]

    assert scene.participants(state) == ["P3", "P1"]




def test_validate_resolution_selection_shape():
    """resolution.selection 应校验当前字段和规划字段的 shape。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: bad-selection
              scene_type: vote
              scope: public
              participants: {filter: {alive: true}}
              candidates: {filter: {alive: true}}
              resolution:
                selection:
                  type: first
                  tie_policy: random
                  target_field: [vote]
                  top_k: 0
                  threshold: high
                  weight: [bad]
                  weights: [bad]
                  unknown_key: true
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("resolution.selection.type" in e for e in errors)
    assert any("resolution.selection.tie_policy" in e for e in errors)
    assert any("resolution.selection.target_field" in e for e in errors)
    assert any("resolution.selection.top_k" in e for e in errors)
    assert any("resolution.selection.threshold" in e for e in errors)
    assert any("resolution.selection.weight" in e for e in errors)
    assert any("resolution.selection.weights" in e for e in errors)
    assert any("resolution.selection 包含未知字段" in e for e in errors)


def test_validate_resolution_selection_accepts_current_runtime_fields():
    """当前运行时支持的 tie_policy/weight 不应被误报。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: good-selection
              scene_type: vote
              scope: public
              participants: {filter: {alive: true}}
              candidates: {filter: {alive: true}}
              resolution:
                selection:
                  tie_policy: no_winner
                  weight:
                    state: actor.vote_weight
                    default: 1
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert not any("resolution.selection" in e for e in errors)

def test_compile_selection_result_tie_policy_and_weight():
    """Vote 应产生完整 selection_result，并支持权重和平票策略。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 3
          casting:
            type: fixed
            assignments:
              P1: villager
              P2: villager
              P3: villager
        scopes:
          - name: public
            members: all
        flow:
          loop: false
          scenes:
            - name: sheriff-vote
              scene_type: vote
              scope: public
              dialogue_policy: {mode: simultaneous}
              participants: {filter: {alive: true}}
              candidates: {filter: {alive: true}}
              response:
                cue: 投票
              resolution:
                selection:
                  tie_policy: no_winner
                  weight:
                    state: actor.vote_weight
                    default: 1
                effects:
                  - type: set_state
                    entity: GAME
                    attr: vote_counts
                    value: selection_result.counts
                  - type: set_state
                    entity: GAME
                    attr: vote_tied
                    value: selection_result.tied_candidates
                  - type: set_state
                    entity: GAME
                    attr: vote_winner
                    value: selection_result.winner
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    from drama_engine.core.engine import State, StateWriter
    state = State(script.vocab)
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True, "vote_weight": 1.5})
    state.register_entity("P2", {"alive": True, "vote_weight": 1})
    state.register_entity("P3", {"alive": True, "vote_weight": 0.5})
    writer = StateWriter(state)
    responses = [
        {"actor": "P1", "data": {"vote": "P2", "reason": "a"}},
        {"actor": "P2", "data": {"vote": "P3", "reason": "b"}},
        {"actor": "P3", "data": {"vote": "P3", "reason": "c"}},
    ]
    script.flow.scenes[0].on_result(responses, state, writer)
    assert state.get_attr("GAME", "vote_counts") == {"P2": 1.5, "P3": 1.5}
    assert state.get_attr("GAME", "vote_tied") == ["P2", "P3"]
    assert state.get_attr("GAME", "vote_winner") is None


def test_compile_triggers_can_react_to_death_events():
    """脚本级 death trigger 应能执行通用 effects。"""
    doc = _yaml("""
        roles:
          - name: villager
            faction: good
            brief: 村民
        players:
          count: 1
          casting:
            type: fixed
            assignments:
              P1: villager
        scopes:
          - name: public
            members: all
        triggers:
          - on: death
            effects:
              - type: set_state
                entity: GAME
                attr: last_dead
                value: item.entity
        flow:
          loop: false
          scenes: []
        referee:
          win_conditions: []
    """)
    script = compiler.compile_doc(doc)
    from drama_engine.core.engine import State, StateWriter, SetAttr
    state = State(script.vocab)
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True})
    writer = StateWriter(state)
    start = len(state.mutation_log())
    writer.apply(SetAttr("P1", "alive", False))
    for trigger in script.triggers:
        trigger(state.mutation_log()[start:], state, writer)
    assert state.get_attr("GAME", "last_dead") == "P1"


def test_validate_candidates_when_must_be_dict_or_list():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: choose
              scene_type: choose
              scope: public
              dialogue_policy: {mode: single}
              participants: {filter: {alive: true}}
              candidates:
                filter: {alive: true}
                when: not-self
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("candidates.when" in e for e in errors)



def test_validate_participants_selector_shape():
    """participants selector 应校验核心字段形状。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: bad-participants
              scene_type: speak
              scope: public
              participants:
                filter: alive
                ordered: 123
                min: -1
                limit: 0
                unknown_key: true
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("participants.filter" in e for e in errors)
    assert any("participants.ordered" in e for e in errors)
    assert any("participants.min" in e for e in errors)
    assert any("participants.limit" in e for e in errors)
    assert any("participants 包含未知字段" in e for e in errors)


def test_validate_candidates_selector_shape():
    """candidates selector 应校验核心字段形状。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: bad-candidates
              scene_type: choose
              scope: public
              participants: {filter: {alive: true}}
              candidates:
                static: yes
                from_data: 123
                count: 0
                min: -1
                max: false
                distinct: "yes"
                include_self: maybe
                exclude: 123
                sort: [seat]
                unknown_key: true
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("candidates.static" in e for e in errors)
    assert any("candidates.from_data" in e for e in errors)
    assert any("candidates.count" in e for e in errors)
    assert any("candidates.min" in e for e in errors)
    assert any("candidates.max" in e for e in errors)
    assert any("candidates.distinct" in e for e in errors)
    assert any("candidates.include_self" in e for e in errors)
    assert any("candidates.exclude" in e for e in errors)
    assert any("candidates.sort" in e for e in errors)
    assert any("candidates 包含未知字段" in e for e in errors)


def test_compile_static_participants_selector():
    """participants.static 和列表简写应返回固定参与者顺序。"""
    from drama_engine.core.engine import State

    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 2, casting: {type: shuffle, distribution: {v: 2}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: static-list
              scene_type: speak
              scope: public
              participants: [P2, P1]
            - name: static-dict
              scene_type: speak
              scope: public
              participants:
                static: [P1, P2]
        referee: {win_conditions: []}
    """)
    script = compiler.compile_doc(doc)
    state = State(script.vocab)

    assert script.flow.scenes[0].participants(state) == ["P2", "P1"]
    assert script.flow.scenes[1].participants(state) == ["P1", "P2"]

def test_validate_cue_placeholder_requires_vars():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              dialogue_policy: {mode: sequential}
              participants: {filter: {alive: true}}
              cue: "当前可行动：{active_players}"
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("active_players" in e and "cue.vars" in e for e in errors)

def test_compile_cue_vars_grouped_names():
    from drama_engine.core.engine import State

    cue = compiler._compile_cue({
        "text": "当前存活阵营：{alive_by_faction}。第 {GAME.round} 轮",
        "vars": {
            "alive_by_faction": {
                "entities": {"filter": {"alive": True}},
                "format": {
                    "type": "grouped_names",
                    "group_by": "faction",
                    "labels": {"good": "好人", "wolf": "狼人"},
                    "template": "{label}有{names}",
                    "separator": "；",
                },
            }
        },
    })
    state = State(compiler._compile_vocab({}, {}))
    state.register_entity("GAME", {"round": 2})
    state.register_entity("P1", {"alive": True, "faction": "good"})
    state.register_entity("P2", {"alive": True, "faction": "wolf"})
    state.register_entity("P3", {"alive": False, "faction": "good"})

    assert cue(state) == "当前存活阵营：好人有P1；狼人有P2。第 2 轮"

def test_compile_cue_vars_python_expr_format():
    from drama_engine.core.engine import State

    cue = compiler._compile_cue({
        "text": "排序：{ordered}",
        "vars": {
            "ordered": {
                "entities": {"filter": {"alive": True}},
                "format": {
                    "type": "python",
                    "expr": "' / '.join(name + ':' + attr(name, 'faction') for name in entities)",
                },
            }
        },
    })
    state = State(compiler._compile_vocab({}, {}))
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True, "faction": "good"})
    state.register_entity("P2", {"alive": True, "faction": "wolf"})

    assert cue(state) == "排序：P1:good / P2:wolf"

def test_compile_cue_vars_python_code_format():
    from drama_engine.core.engine import State

    cue = compiler._compile_cue({
        "text": "分组：{groups}",
        "vars": {
            "groups": {
                "entities": {"filter": {"alive": True}},
                "format": {
                    "type": "python",
                    "code": """
parts = []
for key, names in sorted(group_by('faction').items()):
    parts.append(labels.get(key, key) + '=' + ','.join(names))
result = ';'.join(parts)
""",
                    "labels": {"good": "好人", "wolf": "狼人"},
                },
            }
        },
    })
    state = State(compiler._compile_vocab({}, {}))
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True, "faction": "good"})
    state.register_entity("P2", {"alive": True, "faction": "wolf"})

    assert cue(state) == "分组：好人=P1;狼人=P2"

def test_compile_cue_vars_expr_format_returns_default():
    from drama_engine.core.engine import State

    cue = compiler._compile_cue({
        "text": "摘要：{summary}",
        "vars": {
            "summary": {
                "entities": {"filter": {"alive": True}},
                "format": {
                    "type": "expr",
                    "expr": "用自然语言总结当前局势",
                    "default": "暂无摘要",
                },
            }
        },
    })
    state = State(compiler._compile_vocab({}, {}))
    state.register_entity("GAME", {})
    state.register_entity("P1", {"alive": True})

    assert cue(state) == "摘要：暂无摘要"

def test_validate_cue_vars_expr_format_requires_default():
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: 村民, faction: good, brief: b}]
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: chat
              scene_type: speak
              scope: public
              dialogue_policy: {mode: sequential}
              participants: {filter: {alive: true}}
              cue:
                text: "摘要：{summary}"
                vars:
                  summary:
                    entities: {filter: {alive: true}}
                    format:
                      type: expr
                      expr: "总结当前局势"
        referee: {win_conditions: []}
    """)
    errors = compiler.validate(doc)
    assert any("format" in e and "default" in e for e in errors)

def test_compile_player_config_from_yaml():
    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        players:
          count: 2
          ids: [A, B]
          display_names: {A: "一号", B: "二号"}
          nicknames: {A: "小A"}
          initial_attrs: {active: true}
          casting: {type: shuffle, distribution: {v: 2}}
        scopes: [{name: public, members: all}]
        flow: {loop: false, scenes: []}
        referee: {win_conditions: []}
    """)
    script = compiler.compile_doc(doc)
    assert script.player_config.ids == ["A", "B"]
    assert script.player_config.display_names["A"] == "一号"
    assert script.player_config.nicknames["A"] == "小A"
    assert script.player_config.initial_attrs == {"active": True}

def test_compile_minimal_script():
    """最简 YAML 应能编译为合法的 Script 对象。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles:
          - {name: v, display_name: 村民, faction: good, brief: 你是村民}
        players:
          count: 2
          casting: {type: shuffle, distribution: {v: 2}}
        scopes:
          - {name: public, members: all}
        flow:
          loop: false
          scenes:
            - name: chat
              display_name: 聊天
              scene_type: speak
              scope: public
              dialogue_policy: {mode: sequential}
              participants: {filter: {alive: true}}
        referee:
          win_conditions: []
    """)
    from drama_engine.core.engine import Script
    script = compiler.compile_doc(doc)
    assert isinstance(script, Script)
    assert len(script.roles) == 1
    assert len(script.scopes) == 1
    assert len(script.flow.scenes) == 1

def test_params_expansion_basic():
    raw = "players:\\n  count: '{{total_players}}'\\n"
    result = compiler._expand_params(raw, {"total_players": 9})
    assert "9" in result
    assert "{{total_players}}" not in result

def test_params_expansion_list():
    raw = "human_players: '{{human_players}}'\\n"
    result = compiler._expand_params(raw, {"human_players": ["Player_1", "Player_2"]})
    assert "Player_1" in result

def test_params_override_default():
    doc = {"params": [{"name": "count", "default": 9}]}
    resolved = compiler._resolve_params(doc, {"count": 12})
    assert resolved["count"] == 12

def test_params_uses_default_when_not_overridden():
    doc = {"params": [{"name": "count", "default": 9}]}
    resolved = compiler._resolve_params(doc, {})
    assert resolved["count"] == 9


def test_compile_default_runtime_is_game_session():
    """未声明 runtime 时，默认使用 game_session，保持现有脚本行为。"""
    doc = _yaml("""
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        concepts:
          roles: {v: {display_name: v, description: b}}
          factions: {good: {display_name: good, description: b}}
          scopes: {public: {display_name: public, description: b}}
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: narration
              scope: public
        referee: {win_conditions: []}
    """)

    script = compiler.compile_doc(doc)

    assert script.runtime.type == "game_session"
    assert script.runtime.config == {}


def test_compile_explicit_runtime_declaration():
    """显式 runtime 声明会被编译到 Script 上。"""
    doc = _yaml("""
        runtime:
          type: game_session
          config:
            dry_run: true
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        concepts:
          roles: {v: {display_name: v, description: b}}
          factions: {good: {display_name: good, description: b}}
          scopes: {public: {display_name: public, description: b}}
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: narration
              scope: public
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)
    script = compiler.compile_doc(doc)

    assert errors == []
    assert script.runtime.type == "game_session"
    assert script.runtime.config == {"dry_run": True}


def test_validate_unknown_runtime_type():
    """未知 runtime.type 应在 validate 阶段报错。"""
    doc = _yaml("""
        runtime:
          type: unknown_runtime
        meta: {title: 测试}
        roles: [{name: v, display_name: v, faction: good, brief: b}]
        concepts:
          roles: {v: {display_name: v, description: b}}
          factions: {good: {display_name: good, description: b}}
          scopes: {public: {display_name: public, description: b}}
        players: {count: 1, casting: {type: shuffle, distribution: {v: 1}}}
        scopes: [{name: public, members: all}]
        flow:
          loop: false
          scenes:
            - name: s1
              display_name: s1
              scene_type: narration
              scope: public
        referee: {win_conditions: []}
    """)

    errors = compiler.validate(doc)

    assert any("unknown_runtime" in error for error in errors)
