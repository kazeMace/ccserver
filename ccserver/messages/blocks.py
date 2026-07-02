"""
ccserver/messages/blocks.py

UnifiedBlock 子类 — 输入侧 + 输出侧共用块类型。零依赖：只有 dataclass 和标准库。

设计说明：
- 每个子类对应一种消息块类型（TextBlock、ThinkingBlock 等）
- type 字段为类型判别字段，用于序列化/反序列化时区分块类型
- to_dict() 将块序列化为字典（用于 JSON 传输）
- from_dict() 从字典反序列化为块实例（classmethod）
- 所有子类使用 @dataclass 定义，避免手写 __init__
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────
# 根基类
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedBlock:
    """
    所有 UnifiedBlock 子类的根基类。

    type: str — 类型判别字段，子类中为固定值。
    基类的 to_dict / from_dict 抛 NotImplementedError，
    强制子类提供各自的序列化实现。
    """

    # 类型判别字段，子类固定为具体值（如 "text"、"thinking" 等）
    # 基类不设默认值；子类中固定为各自的字符串常量
    type: str

    def to_dict(self) -> dict:
        """
        将块序列化为字典。
        子类必须覆盖此方法，基类直接抛 NotImplementedError。
        """
        raise NotImplementedError(f"{self.__class__.__name__} 未实现 to_dict()")

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedBlock":
        """
        从字典反序列化为块实例。
        子类必须覆盖此 classmethod，基类直接抛 NotImplementedError。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedBlock 子类实例
        """
        raise NotImplementedError(f"{cls.__name__} 未实现 from_dict()")


# ─────────────────────────────────────────────────────────────
# UnifiedTextBlock — 纯文本块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedTextBlock(UnifiedBlock):
    """
    纯文本内容块。

    字段：
        text: str — 文本内容
        type: str — 固定为 "text"
    """

    text: str = ""
    type: str = "text"

    def to_dict(self) -> dict:
        """
        序列化为 {"type": "text", "text": ...}

        返回：
            dict — 包含 type 和 text 的字典
        """
        return {
            "type": "text",
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedTextBlock":
        """
        从字典构造 UnifiedTextBlock。
        缺失 text 键时默认为空字符串。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedTextBlock 实例
        """
        return cls(text=d.get("text", ""))


# ─────────────────────────────────────────────────────────────
# UnifiedThinkingBlock — 思考链块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedThinkingBlock(UnifiedBlock):
    """
    思考链（chain-of-thought）内容块，对应 Anthropic extended thinking 的 thinking 块。

    字段：
        thinking: str — 思考链内容
        signature: str | None — 可选签名，用于 extended thinking 验证；None 时不序列化
        type: str — 固定为 "thinking"
    """

    thinking: str = ""
    signature: str | None = None
    type: str = "thinking"

    def to_dict(self) -> dict:
        """
        序列化为字典。
        当 signature 为 None 时，字典中不包含 signature 键。
        当 signature 有值时，字典中包含 signature 键。

        返回：
            dict — 包含 type、thinking，以及可选 signature 的字典
        """
        result: dict = {
            "type": "thinking",
            "thinking": self.thinking,
        }
        # signature 为 None 时不加入字典，避免干扰下游的键判断
        if self.signature is not None:
            result["signature"] = self.signature
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedThinkingBlock":
        """
        从字典构造 UnifiedThinkingBlock。
        signature 不存在时为 None。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedThinkingBlock 实例
        """
        return cls(
            thinking=d.get("thinking", ""),
            signature=d.get("signature"),  # 键不存在时返回 None
        )


# ─────────────────────────────────────────────────────────────
# UnifiedToolUseBlock — 工具调用块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedToolUseBlock(UnifiedBlock):
    """
    工具调用块，表示模型发起的一次工具调用请求。

    字段：
        id: str — 工具调用的唯一标识，由模型生成
        name: str — 工具名称
        input: dict — 工具调用的输入参数
        provider_data: dict | None — 运行时内存字段，存储 provider 特定元数据；
                                     不序列化到 to_dict（不发送给 API）
        type: str — 固定为 "tool_use"
    """

    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    provider_data: dict | None = None
    type: str = "tool_use"

    def to_dict(self) -> dict:
        """
        序列化为字典。
        注意：provider_data 不包含在输出中，它仅用于运行时内存。

        返回：
            dict — 包含 type、id、name、input 的字典
        """
        return {
            "type": "tool_use",
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedToolUseBlock":
        """
        从字典构造 UnifiedToolUseBlock。
        provider_data 始终为 None（不从序列化数据中恢复）。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedToolUseBlock 实例
        """
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            input=d.get("input") or {},
            provider_data=None,  # 运行时字段，不从字典恢复
        )


# ─────────────────────────────────────────────────────────────
# UnifiedToolResultBlock — 工具结果块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedToolResultBlock(UnifiedBlock):
    """
    工具执行结果块，表示一次工具调用的返回结果。

    字段：
        tool_use_id: str — 对应 UnifiedToolUseBlock 的 id
        content: str | list[UnifiedBlock] — 工具返回内容；
                 可为字符串（文本结果）或 UnifiedBlock 列表（结构化结果）
        is_error: bool — 工具执行是否出错，默认 False
        type: str — 固定为 "tool_result"
    """

    tool_use_id: str = ""
    content: "str | list" = ""  # str（文本结果）或 list[UnifiedBlock]（结构化结果）
    is_error: bool = False
    type: str = "tool_result"

    def to_dict(self) -> dict:
        """
        序列化为字典。
        content 为列表时，逐个调用元素的 to_dict()。

        返回：
            dict — 包含 type、tool_use_id、content、is_error 的字典
        """
        # content 为 list 时，列表中每个元素都是 UnifiedBlock，逐个序列化
        if isinstance(self.content, list):
            serialized_content = [block.to_dict() for block in self.content]
        else:
            serialized_content = self.content

        return {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": serialized_content,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedToolResultBlock":
        """
        从字典构造 UnifiedToolResultBlock。
        content 直接赋值（不做深度反序列化，因为 list 内容类型不确定）。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedToolResultBlock 实例
        """
        return cls(
            tool_use_id=d.get("tool_use_id", ""),
            content=d.get("content", ""),
            is_error=d.get("is_error", False),
        )


# ─────────────────────────────────────────────────────────────
# UnifiedImageBlock — 图片块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedImageBlock(UnifiedBlock):
    """
    图片内容块，用于向 API 传递图片数据。

    字段：
        source: dict — 图片来源描述（格式遵循 Anthropic API 规范，
                        如 {"type": "base64", "media_type": "image/png", "data": "..."}）
        type: str — 固定为 "image"
    """

    source: dict = field(default_factory=dict)
    type: str = "image"

    def to_dict(self) -> dict:
        """
        序列化为 {"type": "image", "source": ...}

        返回：
            dict — 包含 type 和 source 的字典
        """
        return {
            "type": "image",
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedImageBlock":
        """
        从字典构造 UnifiedImageBlock。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedImageBlock 实例
        """
        return cls(source=d.get("source", {}))


# ─────────────────────────────────────────────────────────────
# UnifiedImageThumbnailBlock — 图片缩略图块（ccserver 内部块）
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedImageThumbnailBlock(UnifiedBlock):
    """
    图片缩略图块（ccserver 内部约定，不发给任何 API）。

    说明：
        此块仅在 ccserver 内部使用，用于在消息历史中存储图片缩略图。
        在发送给 AI API 之前，必须先将其转换为 UnifiedImageBlock 或过滤掉。

    字段：
        source: dict — 缩略图来源描述（格式同 UnifiedImageBlock）
        type: str — 固定为 "image_thumbnail"
    """

    source: dict = field(default_factory=dict)
    type: str = "image_thumbnail"

    def to_dict(self) -> dict:
        """
        序列化为 {"type": "image_thumbnail", "source": ...}

        返回：
            dict — 包含 type 和 source 的字典
        """
        return {
            "type": "image_thumbnail",
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedImageThumbnailBlock":
        """
        从字典构造 UnifiedImageThumbnailBlock。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedImageThumbnailBlock 实例
        """
        return cls(source=d.get("source", {}))


# ─────────────────────────────────────────────────────────────
# UnifiedFileBlock — 文件块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedFileBlock(UnifiedBlock):
    """
    文件引用块，用于传递文件 ID 和元数据。

    字段：
        file_id: str — 文件唯一标识
        filename: str — 文件名，默认空字符串
        mime_type: str — MIME 类型，如 "application/pdf"，默认空字符串
        type: str — 固定为 "file"
    """

    file_id: str = ""
    filename: str = ""
    mime_type: str = ""
    type: str = "file"

    def to_dict(self) -> dict:
        """
        序列化为 {"type": "file", "file_id": ..., "filename": ..., "mime_type": ...}

        返回：
            dict — 包含 type、file_id、filename、mime_type 的字典
        """
        return {
            "type": "file",
            "file_id": self.file_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedFileBlock":
        """
        从字典构造 UnifiedFileBlock。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedFileBlock 实例
        """
        return cls(
            file_id=d.get("file_id", ""),
            filename=d.get("filename", ""),
            mime_type=d.get("mime_type", ""),
        )


# ─────────────────────────────────────────────────────────────
# UnifiedCommandBlock — 命令执行块
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedCommandBlock(UnifiedBlock):
    """
    命令执行记录块，用于记录终端命令的执行结果。

    注意序列化键名：to_dict() 使用 "_type" 而不是 "type"，
    这是 ccserver 内部约定，用于区分 AI 消息块与命令记录。

    字段：
        name: str — 命令名称（如 "bash"、"grep"）
        args: str — 命令参数
        stdout: str — 标准输出（摘要）
        body: str — 完整输出内容
        type: str — 固定为 "command"（实例属性，用于 isinstance 判断）
    """

    name: str = ""
    args: str = ""
    stdout: str = ""
    body: str = ""
    type: str = "command"

    def to_dict(self) -> dict:
        """
        序列化为字典。
        注意：使用 "_type" 键而不是 "type" 键（ccserver 内部约定）。

        返回：
            dict — 包含 _type、name、args、stdout、body 的字典
        """
        return {
            "_type": "command",   # 注意：使用下划线前缀 "_type"，不是 "type"
            "name": self.name,
            "args": self.args,
            "stdout": self.stdout,
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedCommandBlock":
        """
        从字典构造 UnifiedCommandBlock。
        缺失字段时默认为空字符串。

        参数：
            d: 包含块数据的字典（通常含 "_type" 键）

        返回：
            UnifiedCommandBlock 实例
        """
        return cls(
            name=d.get("name", ""),
            args=d.get("args", ""),
            stdout=d.get("stdout", ""),
            body=d.get("body", ""),
        )


# ─────────────────────────────────────────────────────────────
# UnifiedPassthroughBlock — 透传块（未知/不支持的块类型）
# ─────────────────────────────────────────────────────────────

@dataclass
class UnifiedPassthroughBlock(UnifiedBlock):
    """
    透传块，用于存储 ccserver 不识别或不需要解析的块类型。

    典型用途：
        - 存储 Anthropic 的 "redacted_thinking" 块（内容加密，无法解析）
        - 存储未来 API 新增的块类型（向前兼容）

    字段：
        type: str — 块类型由外部传入，无固定默认值（与其他子类不同）
        raw: object | None — 原始数据；to_dict 时，若为 dict 则原样返回
    """

    # 注意：type 没有固定默认值，必须由外部传入
    # 这与其他子类（如 UnifiedTextBlock(type="text")）不同
    raw: Any = None

    def to_dict(self) -> dict:
        """
        序列化为字典。
        - raw 为 dict 时：原样返回 raw（透传原始数据）
        - raw 为其他类型（包括 None）时：返回 {"type": self.type}

        返回：
            dict — 原始字典或仅含 type 的字典
        """
        if isinstance(self.raw, dict):
            return self.raw  # 原样透传，不拷贝
        return {"type": self.type}

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedPassthroughBlock":
        """
        从字典构造 UnifiedPassthroughBlock。
        type 来自 d["type"]，缺失时为 "unknown"。
        raw 为整个 d 字典。

        参数：
            d: 包含块数据的字典

        返回：
            UnifiedPassthroughBlock 实例
        """
        return cls(
            type=str(d.get("type", "unknown")),
            raw=d,
        )
