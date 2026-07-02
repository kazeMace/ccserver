"""Narrator component for authoritative scene announcements."""

from typing import Any

from .stage import Stage
from .state import State


class Narrator:
    """
    旁白 / 主持人 — 剧目信息的权威发布者。

    职责：
      - 向指定 Scope 投递提示词（cue）
      - 发布公告（死亡、胜负等）
      - 唯一能向私密 Scope 投递消息的角色

    不思考，只转发。不是 AI Agent，就是一个消息投递器。
    """

    def __init__(self, stage: Stage, narrator_name: str = "主持人", tracer: Any = None):
        """
        初始化旁白。

        参数：
          stage        — 舞台实例（用于投递消息）
          narrator_name — 旁白的显示名称（默认「主持人」）
          tracer       — 可选观测记录器。非 None 时，say 会记录「主持人对哪个 Scope 说了什么」，
                         用于可视化里的「控场主持人」卡片。None 时无影响。
        """
        self._stage = stage
        self.name = narrator_name
        self._tracer = tracer

    async def say(self, text: str, scope_name: str, state: State) -> None:
        """
        向指定 Scope 的所有成员投递一条旁白消息。

        参数：
          text       — 旁白文本内容
          scope_name — 投递到哪个 Scope
          state      — 当前世界状态（用于 Scope 成员求值）
        """
        if not text:
            # 空文本不投递
            return

        msg = {
            "sender": self.name,
            "text": text,
        }

        print(f"[Narrator] [{scope_name}] {text[:100]}")

        # 观测旁路：记录主持人的控场发言（投到哪个 Scope）
        if self._tracer is not None:
            self._tracer.record_narration(scope=scope_name, text=text)

        await self._stage.deliver(msg, scope_name, state)
