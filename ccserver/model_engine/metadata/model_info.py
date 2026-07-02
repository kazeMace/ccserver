"""
model_info — 模型能力描述符数据类。

每个 LLM 模型通过 ModelInfo 声明自己能处理哪些输入类型（文本、图像、视频等），
系统据此判断是否可以直接向模型发送多模态内容，还是需要先调用 VLM 将图像转为文字。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import FrozenSet


@dataclass(frozen=True)
class ModelInfo:
    """
    模型能力描述符，不可变数据类。

    frozen=True 确保注册后不会被意外修改，可以安全地在多个线程间共享。

    Attributes:
        model_id:       模型唯一标识，如 "claude-sonnet-4-6"、"gpt-4o"
        name:           人类可读名称，如 "Claude Sonnet 4.6"
        input_types:    支持的输入模态集合，如 frozenset({"text", "image"})
                        可选值：text、image、video、audio、file
        context_window: 最大输入 token 数，0 表示未知
        max_tokens:     最大输出 token 数，0 表示未知
        priority:       VLM 自动选择优先级，值越高越优先
        provider:       所属 provider id，如 "anthropic"、"openai"
    """

    model_id: str
    name: str = ""
    input_types: FrozenSet[str] = field(default_factory=lambda: frozenset({"text"}))
    context_window: int = 0
    max_tokens: int = 0
    priority: int = 0
    provider: str = ""

    # ── 便捷查询方法 ──────────────────────────────────────────────────────────

    def supports(self, input_type: str) -> bool:
        """
        判断模型是否支持指定的输入类型。

        Args:
            input_type: 输入类型，如 "text"、"image"、"video"、"audio"、"file"

        Returns:
            True 表示支持，False 表示不支持
        """
        return input_type in self.input_types

    @property
    def supports_image(self) -> bool:
        """是否支持图像输入。"""
        return "image" in self.input_types

    @property
    def supports_video(self) -> bool:
        """是否支持视频输入。"""
        return "video" in self.input_types

    @property
    def supports_audio(self) -> bool:
        """是否支持音频输入。"""
        return "audio" in self.input_types

    @property
    def supports_file(self) -> bool:
        """是否支持文件/文档输入。"""
        return "file" in self.input_types

    @property
    def is_text_only(self) -> bool:
        """是否仅支持文本输入（纯文本模型）。"""
        return self.input_types == frozenset({"text"}) or self.input_types == frozenset()

    @property
    def is_multimodal(self) -> bool:
        """是否支持多模态输入（文本以外的输入类型）。"""
        return len(self.input_types - {"text"}) > 0
