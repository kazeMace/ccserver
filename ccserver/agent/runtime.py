"""
agent.runtime — AgentRuntime 运行时契约（Protocol）。

背景：
  Agent 拆分后,各协作者(LimitPolicy / LLMCaller / ToolDispatcher /
  SpawnManager / CompactCoordinator)需要访问 Agent 的部分状态与能力。
  若直接传入整个 Agent,会让协作者彼此可见、耦合度高(违背 LOD)。

设计：
  定义一个最小契约 AgentRuntime(Protocol),只声明协作者真正需要的属性与方法。
  - 协作者只依赖 AgentRuntime,看不到彼此,符合迪米特法则(最少知识原则)。
  - Agent 自然满足该 Protocol(拥有这些属性/方法),无需显式继承(structural typing)。
  - 渐进迁移友好:Agent 把 self 传给协作者即可。

注意：
  Protocol 仅用于类型标注与契约说明,运行时不强制检查。
  这是纯加法,不改变任何运行时行为。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..managers.hooks import HookContext


@runtime_checkable
class AgentRuntime(Protocol):
    """
    Agent 协作者所依赖的最小运行时契约。

    任何实现了以下属性与方法的对象都可作为 AgentRuntime 使用(structural typing)。
    Agent 类天然满足此契约。

    属性(协作者读取 Agent 的运行时状态):
        context   AgentContext  — 身份/消息/depth/env_vars/inbox
        session   Session       — hooks / event_bus / 消息持久化
        emitter   BaseEmitter   — 向外推送 token / done / ask_user 等事件
        adapter   ModelAdapter  — LLM 调用适配器
        model     str           — 当前模型名
        system    list          — system prompt 块
        state     AgentState    — phase / round_num / current_tool 等运行时状态

    方法(协作者复用 Agent 的通用能力):
        build_hook_ctx()  构造 hook 执行上下文
        set_phase()       变更 phase 并发布 PHASE_CHANGED 事件

    说明:
        - `schemas` 在 Agent 上是私有属性 `_schemas`,运行期会被 MCP/PromptEngine
          追加修改,故此处以 Any 形式通过 `_schemas` 访问,不在 Protocol 中重命名,
          避免误导。协作者需要时直接读 rt._schemas。
    """

    # ── 运行时状态属性 ──────────────────────────────────────────────────────────
    context: Any
    session: Any
    emitter: Any
    adapter: Any
    model: str
    system: Any
    state: Any
    _schemas: list

    # ── 标识与限流相关(LimitPolicy 等协作者使用)──────────────────────────────────
    aid_label: str          # 统一日志标签 "id(name)"
    round_limit: int        # 当前轮次上限
    limit_strategy: str     # 限流策略名
    on_limit_callback: Any  # 自定义接管回调(可为 None)

    # ── 复用能力方法 ────────────────────────────────────────────────────────────
    def _build_hook_ctx(self) -> HookContext:
        """构造 hook 执行上下文(agent_id / session / depth 等)。"""
        ...

    async def _set_phase(self, new_phase: str) -> None:
        """变更 Agent phase 并发布 PHASE_CHANGED 事件到 EventBus。"""
        ...
