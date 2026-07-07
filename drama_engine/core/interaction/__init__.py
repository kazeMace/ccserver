"""统一交互协议投影层（interaction.v1）。

把内部 SessionEventStore 事件 / ActionRequest 归一成对外稳定的
InteractionMessage / ReplyRequest / InboxResponse / StateView（见
docs/interaction_protocol_design.md）。协议是投影，不是新事件系统：内部 event kind
可继续演进，对外 interaction.v1 保持稳定。
"""

from drama_engine.core.interaction.projector import InteractionProjector

__all__ = ["InteractionProjector"]
