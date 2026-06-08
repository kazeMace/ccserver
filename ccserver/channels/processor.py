"""
channels/processor.py — Processor 接口定义及默认实现。

Processor 是出站事件流的处理单元，每个 OutputTarget 持有一个 Processor 实例。
核心设计思路：
  - Processor 是有状态的（状态机），每轮 Agent run 通过 on_turn_start/on_turn_end 重置。
  - 不同 channel 的发送逻辑（飞书/Discord/WebUI/TUI）各自实现 Processor 子类，
    Gateway 无需再做 if channel_id != "webchat" 这类特判。
  - ask_user / permission_request 通过 callback 机制回流答案，不再阻塞 Emitter 层。

典型使用方式：
  # 在 Gateway.dispatch_inbound() 中创建 OutputTarget
  processor = FeishuProcessor(adapter=feishu_adapter, target=target)
  target = OutputTarget(..., processor=processor)
  session.output_targets.append(target)

  # EventBus 订阅者在 on_done 时调用
  await target.processor.on_done(full_text, event)
"""

from typing import Callable, Awaitable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ccserver.event_bus import AgentEvent
    from ccserver.channels.base import BaseChannelAdapter
    from ccserver.channels.output_target import OutputTarget


# ── Processor 基类 ────────────────────────────────────────────────────────────


class Processor:
    """
    token 流处理器接口。

    所有方法默认为空操作（no-op），子类只需覆盖自己关心的事件即可。

    生命周期：
        on_turn_start()  → Agent 开始新一轮处理
        on_token()       → 每个流式 token（可选）
        on_done()        → Agent 完成，输出最终文本
        on_error()       → Agent 出错
        on_turn_end()    → Agent 本轮处理结束（不管成功/失败都会调）
        on_ask_user()    → Agent 向用户提问（需调 answer_cb 注入答案）
        on_permission_request() → Agent 请求工具权限（需调 grant_cb 注入决定）
    """

    async def on_turn_start(self) -> None:
        """每轮 Agent run 开始时调用，子类可用于重置内部状态。"""
        pass

    async def on_token(self, token: str, event: "AgentEvent") -> None:
        """
        收到 LLM 流式输出的一个 token 片段。

        Args:
            token: token 文本内容。
            event: 原始 AgentEvent（含 visibility、agent_id 等元数据）。
        """
        pass

    async def on_done(self, full_text: str, event: "AgentEvent") -> None:
        """
        Agent 完成，收到最终完整输出文本。

        Args:
            full_text: Agent 的最终输出。
            event:     原始 AgentEvent。
        """
        pass

    async def on_ask_user(
        self,
        questions: list,
        answer_cb: Callable[[str], None],
    ) -> None:
        """
        Agent 发出 ask_user 事件，需要等待用户回答。

        实现要求：实现方必须在用户回答后调用 answer_cb(answer_text)，
        将答案注入回 BusEmitter 挂起的 future，Agent 才能继续执行。

        不同 channel 的实现策略：
          - WebUI：推 SSE ask_user 事件，HTTP 层收到 POST /answer 后调 answer_cb。
          - 飞书：发"请回答："消息，注册"下一条 InboundMessage 作为答案"的 hook，
                 下一条消息到来后调 answer_cb。
          - TUI：终端打印问题，用 run_in_executor 读 stdin 后调 answer_cb。

        Args:
            questions:  问题列表，每项含 question/options 等字段。
            answer_cb:  调用 answer_cb(text) 将答案注入 Agent。
        """
        pass

    async def on_permission_request(
        self,
        tool_name: str,
        tool_input: dict,
        grant_cb: Callable[[bool], None],
    ) -> None:
        """
        Agent 请求工具权限审批。

        实现要求：实现方必须调用 grant_cb(True/False) 注入审批结果。
        不调用则 Agent 永久挂起。

        Args:
            tool_name:  请求使用的工具名称。
            tool_input: 工具调用的输入参数。
            grant_cb:   grant_cb(True) 批准，grant_cb(False) 拒绝。
        """
        pass

    async def on_error(self, error: str, event: "AgentEvent") -> None:
        """
        Agent 出错。

        Args:
            error: 错误信息。
            event: 原始 AgentEvent。
        """
        pass

    async def on_turn_end(self) -> None:
        """每轮 Agent run 结束时调用（不管成功/失败都会调）。"""
        pass


# ── PassthroughProcessor ──────────────────────────────────────────────────────


class PassthroughProcessor(Processor):
    """
    透传 Processor：收到 done 事件后直接调用 adapter.send_text()。

    适用于飞书、Discord 等"等待最终结果再发送"的 channel。
    WebUI SSE 流式推流场景应使用各自专用的 Processor 实现。

    Args:
        adapter: BaseChannelAdapter 实例，提供 send_text() 方法。
        target:  所属 OutputTarget，用于取 account_id / to / reply_to_id。
    """

    def __init__(self, adapter: "BaseChannelAdapter", target: "OutputTarget"):
        # adapter 和 target 在 __init__ 时注入，避免循环引用
        self._adapter = adapter
        self._target = target

    async def on_done(self, full_text: str, event: "AgentEvent") -> None:
        """
        收到 done 事件后，通过 adapter 发送最终文本。

        Args:
            full_text: Agent 的完整回复文本。
            event:     原始 AgentEvent（本实现不使用，保持接口一致）。
        """
        if not full_text:
            return
        await self._adapter.send_text(
            self._target.account_id,
            self._target.to,
            full_text,
            reply_to_id=self._target.reply_to_id,
        )
