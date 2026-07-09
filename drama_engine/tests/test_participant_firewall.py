"""模块3：firewall 接入 agent prompt 链路测试。

验证 participant 构建 prompt 时，通过 KnowledgeFirewall 只注入该 actor
「自己的属性 + 已被披露的事实」，绝不泄露他人秘密属性。
"""

from __future__ import annotations

from drama_engine.core.engine import State, Vocabulary
from drama_engine.core.runtime.interactive_session.actions.participant import (
    ParticipantActionExecutor,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import (
    InteractiveScript,
    FlowSpec,
    FlowStateSpec,
    SceneSpec,
    VisibilityPolicy,
)
from drama_engine.core.visibility.disclosure_ledger import DisclosureLedger


def _build_ctx(secret_attrs: list[str]) -> InteractiveExecutionContext:
    """构造一个最小 ctx：带秘密声明的 state + 披露账本。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"players": ["Player_1", "Player_2"], "round": 1})
    state.register_entity("Player_1", {"alive": True, "role": "seer"})
    state.register_entity("Player_2", {"alive": True, "role": "werewolf"})

    script = InteractiveScript(
        meta={},
        runtime=None,
        flow=FlowSpec(states={"main": FlowStateSpec(id="main", scenes=["s1"])}),
        scenes={"s1": SceneSpec(id="s1")},
        visibility=VisibilityPolicy(secret_attrs=secret_attrs),
    )
    # ctx 只需要 state / script / disclosure_ledger 三者即可测投影，其余依赖用 None 占位。
    from drama_engine.core.runtime.interactive_session.context import RuntimeServices, RuntimeEmitters
    services = RuntimeServices(
        condition_evaluator=None,
        effect_executor=None,
        candidate_resolver=None,
        value_resolver=None,
        executor_registry=None,
        plugin_registry=None,
    )
    emitters = RuntimeEmitters(emit_public=None, emit_host=None, emit_private=None)
    return InteractiveExecutionContext(
        script=script,
        services=services,
        emitters=emitters,
        state=state,
        writer=None,
        cast=None,
        patch_journal=None,
        session_metadata={},
        disclosure_ledger=DisclosureLedger(),
    )


def test_prompt_injects_own_attrs_only() -> None:
    """actor 的 prompt 注入自己的 role，但绝不含他人 role。"""
    ctx = _build_ctx(secret_attrs=["role"])
    executor = ParticipantActionExecutor()
    knowledge = executor._build_actor_knowledge(ctx, "Player_1")
    assert "role=seer" in knowledge          # 自己的身份可见
    assert "werewolf" not in knowledge        # 他人秘密不出现在自己 prompt


def test_prompt_injects_disclosed_facts() -> None:
    """被披露的验人结果出现在 actor 的 prompt。"""
    ctx = _build_ctx(secret_attrs=["role"])
    ctx.disclosure_ledger.record("Player_1", "GAME.last_inspection_result", "Player_2 是狼人")
    executor = ParticipantActionExecutor()
    knowledge = executor._build_actor_knowledge(ctx, "Player_1")
    assert "你已获知的信息" in knowledge
    assert "Player_2 是狼人" in knowledge


def test_prompt_knowledge_empty_without_ctx() -> None:
    """无 ctx 时返回空串，不影响原有 prompt。"""
    executor = ParticipantActionExecutor()
    assert executor._build_actor_knowledge(None, "Player_1") == ""
