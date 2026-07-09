"""Script DSL 插件注册表测试。"""

from drama_engine.core.components.conditions import ConditionEvaluator
from drama_engine.core.components.effects import EffectExecutor
from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.plugins import EffectContext, PluginApi, PluginRegistry, ViewContext


def _state() -> State:
    vocab = Vocabulary(
        roles=frozenset(),
        factions=frozenset(),
        scopes=frozenset(),
        abilities=frozenset(),
    )
    state = State(vocab)
    state.register_entity("GAME", {"score": 3})
    return state


def test_plugin_effect_runs_through_state_writer():
    """插件 effect 应通过 StateWriter 改状态。"""
    registry = PluginRegistry()
    api = PluginApi(registry)

    def add_score(effect: dict, context: EffectContext) -> None:
        current = context.state.get_attr("GAME", "score", 0)
        context.writer.apply(SetAttr("GAME", "score", current + effect["amount"]))

    api.register_effect("demo.add_score", add_score)

    state = _state()
    writer = StateWriter(state)
    executor = EffectExecutor(ConditionEvaluator(registry), registry)
    executor.execute_all(
        effects=[{"type": "demo.add_score", "amount": 2}],
        state=state,
        writer=writer,
        responses=[],
        actor=None,
        extra={"scene_name": "score"},
    )

    assert state.get_attr("GAME", "score") == 5
    assert len(state.mutation_log()) == 1


def test_plugin_condition_and_value_resolver_are_registered_interfaces():
    """插件 condition/value resolver 应能通过注册表调用。"""
    registry = PluginRegistry()
    api = PluginApi(registry)
    api.register_condition(
        "demo.score_at_least",
        lambda spec, context: context["state"].get_attr("GAME", "score", 0) >= spec["min"],
    )
    api.register_value_resolver(
        "demo",
        lambda path, context: context["state"].get_attr("GAME", path),
    )

    evaluator = ConditionEvaluator(registry)
    state = _state()

    assert evaluator.evaluate({"plugin": "demo.score_at_least", "min": 3}, state, actor=None)
    assert evaluator.evaluate(
        {"value": {"ref": "demo:score"}, "equal": 3},
        state,
        actor=None,
    )


def test_core_views_inline_projector_resolves_only_explicit_refs():
    """core.views.inline 只解析显式 ref，不误吞普通 value 字段。"""
    registry = PluginRegistry()
    from drama_engine.core.plugins import CoreViewsPlugin

    CoreViewsPlugin().register(PluginApi(registry))
    state = _state()
    event = registry.project_view(
        {
            "id": "score",
            "kind": "key-value",
            "title": "分数",
            "audience": "public",
            "data": {
                "rows": [
                    {"label": "固定文本", "value": "不解析"},
                    {"label": "当前分数", "value": {"ref": "GAME.score"}},
                ]
            },
        },
        ViewContext(
            state=state,
            scene_name="score",
            audience="public",
            mutation_log=[],
            script_extensions={},
        ),
    )

    assert event["data"]["rows"][0]["value"] == "不解析"
    assert event["data"]["rows"][1]["value"] == 3
