"""续写风格组件实现。

每种风格只需：
  1. 声明 STYLE_KEY（对应 prompts 模块的风格名，build_prompt 由基类统一处理）
  2. 实现 parse_response，把 LLM 原始响应转为标准化内容结构
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    NarrationStyleComponent,
)


class PlainNarrationStyle(NarrationStyleComponent):
    """平铺直叙：纯旁白描写。"""

    STYLE_KEY = "plain_narration"

    def parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "narration": str(raw.get("narration") or raw.get("scene_text") or ""),
            "choices": list(raw.get("choices") or []),
            "should_end": bool(raw.get("should_end", False)),
            "ending_id": raw.get("ending_id"),
        }


class DialogueSequenceStyle(NarrationStyleComponent):
    """对话序列：逐句对话（speaker + text），类似 The Clause 的 dialogue_history。"""

    STYLE_KEY = "dialogue_sequence"

    def parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        dialogue = raw.get("dialogue_history") or raw.get("dialogue") or []
        # 兼容：如果 LLM 返回 narration 而非 dialogue_history
        if not dialogue and raw.get("narration"):
            dialogue = [{"speaker": "narrator", "text": str(raw["narration"])}]
        return {
            "title": raw.get("title"),
            "synopsis": raw.get("synopsis"),
            "location": raw.get("location"),
            "dialogue_history": list(dialogue),
            "choices": list(raw.get("choices") or []),
            "should_end": bool(raw.get("should_end", False)),
            "ending_id": raw.get("ending_id"),
        }


class MixedStyle(NarrationStyleComponent):
    """混排：旁白 + 对话交替。"""

    STYLE_KEY = "mixed"

    def parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "narration": str(raw.get("narration") or ""),
            "dialogue_history": list(raw.get("dialogue_history") or []),
            "choices": list(raw.get("choices") or []),
            "should_end": bool(raw.get("should_end", False)),
            "ending_id": raw.get("ending_id"),
        }


__all__ = ["PlainNarrationStyle", "DialogueSequenceStyle", "MixedStyle"]
