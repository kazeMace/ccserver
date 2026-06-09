"""
agent.limit_policy — 轮次上限(round limit)兜底策略。

背景：
  Agent._loop() 跑满 round_limit 仍未结束时,需要一个兜底策略决定如何收尾。
  原实现把 5 种策略(last_text / ask_user / graceful / summarize / report)
  以 `if strategy == ...` 写死在 Agent 内部,新增策略要改分派逻辑(违背 OCP),
  且 ask_user 直接回写 agent.round_limit / agent._continue_loop,与 _loop 双向耦合。

本模块改造：
  1. 策略模式 + 注册表(register_limit_strategy 装饰器,与 model/factory 同风格):
     新增策略只需注册一个类,无需修改分派代码(OCP)。
  2. 返回 LimitOutcome(纯数据)而非直接回写 Agent 状态:
     是否继续循环、追加多少轮、最终文本,全部由 LimitOutcome 表达,
     _loop 读取后自行决定,消除"策略回写 _loop 内部变量"的隐式耦合(LOD)。

副作用约定：
  策略仍负责"对外 emit"(emit_done / emit_subagent_done / emit_error /
  emit_ask_user)与 hook 触发,以保持与重构前完全一致的可观测行为。
  LimitOutcome 只承载「_loop 需要据此做的控制流决策」。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from ..config import MAIN_ROUND_LIMIT
from .runtime import AgentRuntime


# ── 策略返回的纯数据结果 ────────────────────────────────────────────────────────


@dataclass
class LimitOutcome:
    """
    限流策略的执行结果,供 Agent._loop() 解读控制流。

    字段：
        final_text    最终要返回给调用方的文本(策略已完成对应 emit)。
        continue_loop 是否让 _loop 重置计数继续执行(仅 ask_user 选择"继续"时为 True)。
        extra_rounds  continue_loop 为 True 时,要给 round_limit 增加的轮次额度。
    """
    final_text: str = ""
    continue_loop: bool = False
    extra_rounds: int = 0


# ── 策略接口与注册表 ────────────────────────────────────────────────────────────


class LimitStrategy(Protocol):
    """限流策略契约:输入运行时与当前 last_text,产出 LimitOutcome。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        ...


# 策略名 → 策略实例 的注册表
_LIMIT_STRATEGIES: dict[str, LimitStrategy] = {}


def register_limit_strategy(name: str):
    """
    装饰器:把一个策略类注册到 _LIMIT_STRATEGIES(以无参实例化)。

    Args:
        name: 策略名,如 "ask_user"。

    用法:
        @register_limit_strategy("ask_user")
        class AskUserStrategy: ...
    """
    assert name, "limit strategy name must not be empty"
    assert name not in _LIMIT_STRATEGIES, f"limit strategy {name!r} already registered"

    def _decorator(cls):
        _LIMIT_STRATEGIES[name] = cls()
        logger.debug("register_limit_strategy | name={} cls={}", name, cls.__name__)
        return cls

    return _decorator


def get_limit_strategy(name: str) -> LimitStrategy | None:
    """按名取策略实例,未注册返回 None。"""
    return _LIMIT_STRATEGIES.get(name)


# ── 通用兜底:输出 last_text ────────────────────────────────────────────────────


async def finish_with_last_text(rt: AgentRuntime, last_text: str) -> LimitOutcome:
    """
    兜底输出 last_text,走正常结束流程。无 last_text 时 emit_error。

    与原 Agent._finish_with_last_text 行为一致:
      - 有文本:根代理 emit_done(并触发 agent:stop hook),子代理 emit_subagent_done
      - 无文本:emit_error,返回空串
    """
    if last_text:
        if rt.context.is_orchestrator:
            await rt.session.hooks.emit_void(
                "agent:stop",
                {"reply": last_text},
                rt._build_hook_ctx(),
            )
            await rt.emitter.emit_done(last_text)
        else:
            await rt.emitter.emit_subagent_done(last_text)
        return LimitOutcome(final_text=last_text)
    else:
        await rt.emitter.emit_error("Round limit reached with no output")
        return LimitOutcome(final_text="")


# ── 具体策略 ────────────────────────────────────────────────────────────────────


@register_limit_strategy("last_text")
class LastTextStrategy:
    """默认策略:直接输出最近一次 last_text。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        return await finish_with_last_text(rt, last_text)


@register_limit_strategy("ask_user")
class AskUserStrategy:
    """向用户询问是否继续(仅主 agent 有意义)。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        if not rt.context.is_orchestrator:
            return await finish_with_last_text(rt, last_text)
        answer = await rt.emitter.emit_ask_user([{
            "question": f"已执行 {rt.round_limit} 轮仍未完成，是否继续？",
            "header": "继续运行",
            "options": [
                {"label": "继续", "description": "重置轮次计数，继续执行"},
                {"label": "停止", "description": "输出当前结果并结束"},
            ],
            "multiSelect": False,
        }])
        if answer and "继续" in answer:
            # 追加一条 user 消息触发下一轮;通过 LimitOutcome 告知 _loop 继续 + 加额度。
            rt.context.messages.append({"role": "user", "content": "继续执行未完成的任务。"})
            logger.info("User chose to continue | agent={} extra_rounds={}", rt.aid_label, MAIN_ROUND_LIMIT)
            return LimitOutcome(final_text="", continue_loop=True, extra_rounds=MAIN_ROUND_LIMIT)
        return await finish_with_last_text(rt, last_text)


@register_limit_strategy("graceful")
class GracefulStrategy:
    """向用户输出固定提示后优雅结束。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        graceful_msg = "处理步骤超出限制，请重新提问或简化需求。"
        if last_text:
            graceful_msg = f"{graceful_msg}\n\n目前结果：{last_text}"
        if rt.context.is_orchestrator:
            await rt.session.hooks.emit_void(
                "agent:stop",
                {"reply": graceful_msg},
                rt._build_hook_ctx(),
            )
            await rt.emitter.emit_done(graceful_msg)
        else:
            await rt.emitter.emit_subagent_done(graceful_msg)
        return LimitOutcome(final_text=graceful_msg)


@register_limit_strategy("summarize")
class SummarizeStrategy:
    """调用 LLM 对当前消息做摘要,以摘要作为最终回复。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        try:
            import json as _json
            conversation = _json.dumps(rt.context.messages, default=str, ensure_ascii=False)[:20000]
            response = await rt.adapter.create(
                model=rt.model,
                messages=[{"role": "user", "content": (
                    "请对以下对话做简洁总结，说明已完成了什么、当前状态是什么：\n\n" + conversation
                )}],
                max_tokens=1000,
            )
            assert response.content, f"LLM returned empty content in summarize for {rt.aid_label}"
            # 跳过 ThinkingBlock，取第一个 TextBlock（deepseek 等端点默认开启 thinking）
            text_block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
            assert text_block is not None, f"summarize: no TextBlock in response, types={[getattr(b,'type',None) for b in response.content]}"
            summary = text_block.text
        except Exception as e:
            logger.error("summarize strategy failed | agent={} error={}", rt.aid_label, e)
            return await finish_with_last_text(rt, last_text)

        result = f"（步骤超限，以下为当前进度摘要）\n\n{summary}"
        if rt.context.is_orchestrator:
            await rt.session.hooks.emit_void(
                "agent:stop",
                {"reply": result},
                rt._build_hook_ctx(),
            )
            await rt.emitter.emit_done(result)
        else:
            await rt.emitter.emit_subagent_done(result)
        return LimitOutcome(final_text=result)


@register_limit_strategy("report")
class ReportStrategy:
    """子 agent 专属:返回格式化报告给父 agent(不 emit)。主 agent 用则 fallback。"""

    async def handle(self, rt: AgentRuntime, last_text: str) -> LimitOutcome:
        if rt.context.is_orchestrator:
            # 主 agent 不支持 report,回退到 last_text
            return await finish_with_last_text(rt, last_text)
        report = f"[LIMIT_REACHED] 已执行 {rt.round_limit} 轮，部分结果：{last_text or '（无输出）'}"
        return LimitOutcome(final_text=report)


# ── 策略协调器:被 Agent 持有 ──────────────────────────────────────────────────


class LimitPolicy:
    """
    限流策略协调器,被 Agent 持有(组合)。

    职责:
      1. 触发 agent:limit hook(observing)
      2. on_limit_callback 优先(失败/空则 fallback)
      3. 按 rt.limit_strategy 查注册表分派到对应策略
      4. 返回 LimitOutcome 供 _loop 解读
    """

    def __init__(self, rt: AgentRuntime):
        self._rt = rt

    async def handle(self, last_text: str) -> LimitOutcome:
        rt = self._rt
        # Step 1: 触发 hook(observing,不影响后续)
        await rt.session.hooks.emit_void(
            "agent:limit",
            {"last_text": last_text},
            rt._build_hook_ctx(),
        )

        # Step 2: callback 优先
        if rt.on_limit_callback is not None:
            try:
                result = await rt.on_limit_callback(rt, last_text)
                if result:
                    return await finish_with_last_text(rt, result)
            except Exception as e:
                logger.error("on_limit_callback failed | agent={} error={}", rt.aid_label, e)
            # 回调失败或返回空,fallback 到下面的配置策略

        # Step 3: 按策略名分派(未注册或子 agent 用了主 agent 策略时,fallback 到 last_text)
        strategy = get_limit_strategy(rt.limit_strategy)
        if strategy is None:
            return await finish_with_last_text(rt, last_text)
        return await strategy.handle(rt, last_text)
