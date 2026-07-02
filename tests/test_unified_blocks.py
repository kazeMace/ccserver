"""
tests/test_unified_blocks.py

UnifiedBlock 子类的完整测试套件。
按 TDD 规范，先写测试后写实现。

覆盖范围：
- UnifiedBlock 基类（to_dict/from_dict 抛 NotImplementedError）
- 9 个子类的构造、to_dict、from_dict
- 重点边界用例：ThinkingBlock.signature None/有值、ToolUseBlock.provider_data 不序列化、
  CommandBlock 使用 _type 键、PassthroughBlock raw 为 dict 时原样返回
"""

import pytest
from ccserver.messages.blocks import (
    UnifiedBlock,
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageBlock,
    UnifiedImageThumbnailBlock,
    UnifiedFileBlock,
    UnifiedCommandBlock,
    UnifiedPassthroughBlock,
)


# ─────────────────────────────────────────
# UnifiedBlock 基类
# ─────────────────────────────────────────

class TestUnifiedBlock:
    def test_base_to_dict_raises(self):
        """基类 to_dict 应该抛 NotImplementedError；需显式传入 type"""
        block = UnifiedBlock(type="test")
        with pytest.raises(NotImplementedError):
            block.to_dict()

    def test_base_from_dict_raises(self):
        """基类 from_dict 应该抛 NotImplementedError"""
        with pytest.raises(NotImplementedError):
            UnifiedBlock.from_dict({})

    def test_base_type_required(self):
        """基类 type 没有默认值，不传 type 应抛 TypeError"""
        with pytest.raises(TypeError):
            UnifiedBlock()  # type 是必填参数

    def test_base_type_explicit(self):
        """基类显式传 type 时，字段赋值正确"""
        block = UnifiedBlock(type="custom")
        assert block.type == "custom"


# ─────────────────────────────────────────
# UnifiedTextBlock
# ─────────────────────────────────────────

class TestUnifiedTextBlock:
    def test_type_is_text(self):
        """type 字段固定为 'text'"""
        block = UnifiedTextBlock(text="hello")
        assert block.type == "text"

    def test_to_dict_basic(self):
        """to_dict 返回含 type 和 text 的字典"""
        block = UnifiedTextBlock(text="hello world")
        result = block.to_dict()
        assert result == {"type": "text", "text": "hello world"}

    def test_to_dict_empty_text(self):
        """空字符串 text 也能序列化"""
        block = UnifiedTextBlock(text="")
        assert block.to_dict() == {"type": "text", "text": ""}

    def test_from_dict_basic(self):
        """from_dict 正确反序列化"""
        d = {"type": "text", "text": "hello"}
        block = UnifiedTextBlock.from_dict(d)
        assert block.text == "hello"
        assert block.type == "text"

    def test_from_dict_missing_text_defaults_empty(self):
        """from_dict 缺 text 键时默认为空字符串"""
        block = UnifiedTextBlock.from_dict({})
        assert block.text == ""

    def test_roundtrip(self):
        """to_dict -> from_dict 保持一致"""
        original = UnifiedTextBlock(text="round trip")
        restored = UnifiedTextBlock.from_dict(original.to_dict())
        assert restored.text == original.text


# ─────────────────────────────────────────
# UnifiedThinkingBlock
# ─────────────────────────────────────────

class TestUnifiedThinkingBlock:
    def test_type_is_thinking(self):
        block = UnifiedThinkingBlock(thinking="I think...")
        assert block.type == "thinking"

    def test_to_dict_no_signature_excludes_key(self):
        """signature 为 None 时，to_dict 不含 signature 键（重点测试）"""
        block = UnifiedThinkingBlock(thinking="I think...", signature=None)
        result = block.to_dict()
        assert "signature" not in result
        assert result == {"type": "thinking", "thinking": "I think..."}

    def test_to_dict_with_signature_includes_key(self):
        """signature 有值时，to_dict 包含 signature 键（重点测试）"""
        block = UnifiedThinkingBlock(thinking="I think...", signature="abc123")
        result = block.to_dict()
        assert "signature" in result
        assert result["signature"] == "abc123"
        assert result == {"type": "thinking", "thinking": "I think...", "signature": "abc123"}

    def test_from_dict_without_signature(self):
        """from_dict 无 signature 时 signature 为 None"""
        d = {"type": "thinking", "thinking": "pondering"}
        block = UnifiedThinkingBlock.from_dict(d)
        assert block.thinking == "pondering"
        assert block.signature is None

    def test_from_dict_with_signature(self):
        """from_dict 含 signature 时保留 signature 值（重点测试）"""
        d = {"type": "thinking", "thinking": "pondering", "signature": "sig_xyz"}
        block = UnifiedThinkingBlock.from_dict(d)
        assert block.signature == "sig_xyz"

    def test_signature_default_is_none(self):
        """signature 参数默认值为 None"""
        block = UnifiedThinkingBlock(thinking="test")
        assert block.signature is None

    def test_roundtrip_with_signature(self):
        """to_dict -> from_dict 保留 signature"""
        original = UnifiedThinkingBlock(thinking="deep thought", signature="sig42")
        restored = UnifiedThinkingBlock.from_dict(original.to_dict())
        assert restored.thinking == "deep thought"
        assert restored.signature == "sig42"


# ─────────────────────────────────────────
# UnifiedToolUseBlock
# ─────────────────────────────────────────

class TestUnifiedToolUseBlock:
    def test_type_is_tool_use(self):
        block = UnifiedToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})
        assert block.type == "tool_use"

    def test_to_dict_basic(self):
        block = UnifiedToolUseBlock(id="t1", name="bash", input={"cmd": "ls"})
        result = block.to_dict()
        assert result == {"type": "tool_use", "id": "t1", "name": "bash", "input": {"cmd": "ls"}}

    def test_to_dict_excludes_provider_data(self):
        """provider_data 不出现在 to_dict 中（重点测试）"""
        block = UnifiedToolUseBlock(
            id="t1",
            name="bash",
            input={"cmd": "ls"},
            provider_data={"extra": "metadata"},
        )
        result = block.to_dict()
        assert "provider_data" not in result
        assert "extra" not in result

    def test_provider_data_default_is_none(self):
        """provider_data 默认为 None"""
        block = UnifiedToolUseBlock(id="t1", name="bash", input={})
        assert block.provider_data is None

    def test_provider_data_can_be_set(self):
        """provider_data 可以设置到实例上"""
        block = UnifiedToolUseBlock(id="t1", name="bash", input={}, provider_data={"k": "v"})
        assert block.provider_data == {"k": "v"}

    def test_from_dict_basic(self):
        d = {"type": "tool_use", "id": "t2", "name": "grep", "input": {"pattern": "foo"}}
        block = UnifiedToolUseBlock.from_dict(d)
        assert block.id == "t2"
        assert block.name == "grep"
        assert block.input == {"pattern": "foo"}

    def test_from_dict_provider_data_is_none(self):
        """from_dict 后 provider_data 始终为 None（重点测试）"""
        d = {"type": "tool_use", "id": "t3", "name": "tool", "input": {}}
        block = UnifiedToolUseBlock.from_dict(d)
        assert block.provider_data is None

    def test_roundtrip(self):
        original = UnifiedToolUseBlock(id="x", name="fn", input={"a": 1})
        restored = UnifiedToolUseBlock.from_dict(original.to_dict())
        assert restored.id == "x"
        assert restored.name == "fn"
        assert restored.input == {"a": 1}


# ─────────────────────────────────────────
# UnifiedToolResultBlock
# ─────────────────────────────────────────

class TestUnifiedToolResultBlock:
    def test_type_is_tool_result(self):
        block = UnifiedToolResultBlock(tool_use_id="t1", content="done")
        assert block.type == "tool_result"

    def test_to_dict_with_string_content(self):
        """content 为字符串时序列化为字符串"""
        block = UnifiedToolResultBlock(tool_use_id="t1", content="output text")
        result = block.to_dict()
        assert result == {
            "type": "tool_result",
            "tool_use_id": "t1",
            "content": "output text",
            "is_error": False,
        }

    def test_to_dict_with_list_content(self):
        """content 为 list[UnifiedBlock] 时逐个调 to_dict"""
        inner = UnifiedTextBlock(text="result text")
        block = UnifiedToolResultBlock(tool_use_id="t2", content=[inner])
        result = block.to_dict()
        assert result["content"] == [{"type": "text", "text": "result text"}]

    def test_to_dict_is_error_true(self):
        block = UnifiedToolResultBlock(tool_use_id="t1", content="error msg", is_error=True)
        assert block.to_dict()["is_error"] is True

    def test_is_error_default_false(self):
        block = UnifiedToolResultBlock(tool_use_id="t1", content="ok")
        assert block.is_error is False

    def test_from_dict_string_content(self):
        d = {"type": "tool_result", "tool_use_id": "t1", "content": "out", "is_error": False}
        block = UnifiedToolResultBlock.from_dict(d)
        assert block.tool_use_id == "t1"
        assert block.content == "out"
        assert block.is_error is False

    def test_from_dict_is_error(self):
        d = {"type": "tool_result", "tool_use_id": "t1", "content": "err", "is_error": True}
        block = UnifiedToolResultBlock.from_dict(d)
        assert block.is_error is True


# ─────────────────────────────────────────
# UnifiedImageBlock
# ─────────────────────────────────────────

class TestUnifiedImageBlock:
    def test_type_is_image(self):
        block = UnifiedImageBlock(source={"type": "base64", "data": "abc"})
        assert block.type == "image"

    def test_to_dict(self):
        source = {"type": "base64", "media_type": "image/png", "data": "abc"}
        block = UnifiedImageBlock(source=source)
        result = block.to_dict()
        assert result == {"type": "image", "source": source}

    def test_from_dict(self):
        source = {"type": "url", "url": "https://example.com/img.png"}
        d = {"type": "image", "source": source}
        block = UnifiedImageBlock.from_dict(d)
        assert block.source == source

    def test_roundtrip(self):
        source = {"type": "base64", "data": "xyz"}
        original = UnifiedImageBlock(source=source)
        restored = UnifiedImageBlock.from_dict(original.to_dict())
        assert restored.source == source


# ─────────────────────────────────────────
# UnifiedImageThumbnailBlock
# ─────────────────────────────────────────

class TestUnifiedImageThumbnailBlock:
    def test_type_is_image_thumbnail(self):
        block = UnifiedImageThumbnailBlock(source={"type": "base64", "data": "abc"})
        assert block.type == "image_thumbnail"

    def test_to_dict(self):
        source = {"type": "base64", "data": "thumb_data"}
        block = UnifiedImageThumbnailBlock(source=source)
        result = block.to_dict()
        assert result == {"type": "image_thumbnail", "source": source}

    def test_from_dict(self):
        source = {"type": "base64", "data": "td"}
        d = {"type": "image_thumbnail", "source": source}
        block = UnifiedImageThumbnailBlock.from_dict(d)
        assert block.source == source

    def test_ccserver_internal_note(self):
        """image_thumbnail 是 ccserver 内部块，不发给任何 API，to_dict 仍保留 type 标记"""
        block = UnifiedImageThumbnailBlock(source={})
        assert block.to_dict()["type"] == "image_thumbnail"


# ─────────────────────────────────────────
# UnifiedFileBlock
# ─────────────────────────────────────────

class TestUnifiedFileBlock:
    def test_type_is_file(self):
        block = UnifiedFileBlock(file_id="f1")
        assert block.type == "file"

    def test_to_dict_full(self):
        block = UnifiedFileBlock(file_id="f1", filename="doc.pdf", mime_type="application/pdf")
        result = block.to_dict()
        assert result == {
            "type": "file",
            "file_id": "f1",
            "filename": "doc.pdf",
            "mime_type": "application/pdf",
        }

    def test_defaults(self):
        """filename 和 mime_type 默认为空字符串"""
        block = UnifiedFileBlock(file_id="f2")
        assert block.filename == ""
        assert block.mime_type == ""

    def test_from_dict(self):
        d = {"type": "file", "file_id": "f3", "filename": "img.png", "mime_type": "image/png"}
        block = UnifiedFileBlock.from_dict(d)
        assert block.file_id == "f3"
        assert block.filename == "img.png"
        assert block.mime_type == "image/png"

    def test_roundtrip(self):
        original = UnifiedFileBlock(file_id="f4", filename="a.txt", mime_type="text/plain")
        restored = UnifiedFileBlock.from_dict(original.to_dict())
        assert restored.file_id == "f4"
        assert restored.filename == "a.txt"


# ─────────────────────────────────────────
# UnifiedCommandBlock
# ─────────────────────────────────────────

class TestUnifiedCommandBlock:
    def test_type_is_command(self):
        block = UnifiedCommandBlock(name="ls", args="-la", stdout="file.py", body="")
        assert block.type == "command"

    def test_to_dict_uses_underscore_type_key(self):
        """to_dict 使用 '_type' 键而不是 'type'（重点测试）"""
        block = UnifiedCommandBlock(name="ls", args="-la", stdout="file.py", body="content")
        result = block.to_dict()
        assert "_type" in result
        assert result["_type"] == "command"
        assert "type" not in result

    def test_to_dict_full(self):
        block = UnifiedCommandBlock(name="grep", args="foo bar", stdout="match line", body="full output")
        result = block.to_dict()
        assert result == {
            "_type": "command",
            "name": "grep",
            "args": "foo bar",
            "stdout": "match line",
            "body": "full output",
        }

    def test_from_dict(self):
        d = {"_type": "command", "name": "cat", "args": "file.txt", "stdout": "hello", "body": "hello world"}
        block = UnifiedCommandBlock.from_dict(d)
        assert block.name == "cat"
        assert block.args == "file.txt"
        assert block.stdout == "hello"
        assert block.body == "hello world"

    def test_from_dict_missing_fields_default_empty(self):
        """from_dict 缺字段时默认为空字符串"""
        block = UnifiedCommandBlock.from_dict({})
        assert block.name == ""
        assert block.args == ""
        assert block.stdout == ""
        assert block.body == ""

    def test_roundtrip(self):
        original = UnifiedCommandBlock(name="echo", args="hi", stdout="hi", body="hi\n")
        restored = UnifiedCommandBlock.from_dict(original.to_dict())
        assert restored.name == "echo"
        assert restored.body == "hi\n"


# ─────────────────────────────────────────
# UnifiedPassthroughBlock
# ─────────────────────────────────────────

class TestUnifiedPassthroughBlock:
    def test_type_from_constructor(self):
        """type 由外部传入，无固定默认值（重点测试）"""
        block = UnifiedPassthroughBlock(type="redacted_thinking")
        assert block.type == "redacted_thinking"

    def test_type_required_no_default(self):
        """UnifiedPassthroughBlock() 不传 type 应抛 TypeError（无默认值）"""
        with pytest.raises(TypeError):
            UnifiedPassthroughBlock()  # type 是必填参数

    def test_to_dict_raw_is_dict_returns_raw(self):
        """raw 为 dict 时 to_dict 原样返回 raw（重点测试）"""
        raw = {"type": "redacted_thinking", "data": "encrypted_data_xyz"}
        block = UnifiedPassthroughBlock(type="redacted_thinking", raw=raw)
        result = block.to_dict()
        assert result is raw  # 原样返回，不是拷贝

    def test_to_dict_raw_is_none_returns_type_dict(self):
        """raw 为 None 时返回 {'type': self.type}"""
        block = UnifiedPassthroughBlock(type="unknown_type")
        result = block.to_dict()
        assert result == {"type": "unknown_type"}

    def test_to_dict_raw_is_non_dict_returns_type_dict(self):
        """raw 为非 dict（如字符串）时返回 {'type': self.type}"""
        block = UnifiedPassthroughBlock(type="weird", raw="some string")
        result = block.to_dict()
        assert result == {"type": "weird"}

    def test_raw_default_is_none(self):
        """raw 默认为 None"""
        block = UnifiedPassthroughBlock(type="x")
        assert block.raw is None

    def test_from_dict_basic(self):
        """from_dict 从字典构造，raw 为整个 d"""
        d = {"type": "redacted_thinking", "payload": "abc"}
        block = UnifiedPassthroughBlock.from_dict(d)
        assert block.type == "redacted_thinking"
        assert block.raw == d

    def test_from_dict_missing_type_defaults_unknown(self):
        """from_dict 无 type 键时 type 为 'unknown'"""
        block = UnifiedPassthroughBlock.from_dict({"payload": "data"})
        assert block.type == "unknown"

    def test_roundtrip_with_dict_raw(self):
        """to_dict -> from_dict 保持 type 信息"""
        original = UnifiedPassthroughBlock(
            type="redacted_thinking",
            raw={"type": "redacted_thinking", "data": "enc"}
        )
        d = original.to_dict()
        restored = UnifiedPassthroughBlock.from_dict(d)
        assert restored.type == "redacted_thinking"


# ── UnifiedUsage ──────────────────────────────────────────────────────────────

from ccserver.messages.usage import UnifiedUsage

def test_usage_cache_fields():
    u = UnifiedUsage(input_tokens=100, output_tokens=50, total_tokens=150,
                     cache_read_input_tokens=80, cache_creation_input_tokens=20)
    assert u.cache_read_input_tokens == 80
    assert u.cache_creation_input_tokens == 20

def test_usage_defaults_all_zero():
    u = UnifiedUsage()
    assert u.input_tokens == 0
    assert u.cache_read_input_tokens == 0
    assert u.cache_creation_input_tokens == 0

def test_usage_to_dict():
    u = UnifiedUsage(input_tokens=10, output_tokens=5, total_tokens=15,
                     cache_read_input_tokens=8, cache_creation_input_tokens=2)
    d = u.to_dict()
    assert d["input_tokens"] == 10
    assert d["cache_read_input_tokens"] == 8
    assert d["cache_creation_input_tokens"] == 2

def test_usage_from_dict_roundtrip():
    orig = UnifiedUsage(input_tokens=10, output_tokens=5, total_tokens=15,
                        cache_read_input_tokens=3, cache_creation_input_tokens=1)
    d = orig.to_dict()
    restored = UnifiedUsage.from_dict(d)
    assert restored.cache_read_input_tokens == 3
    assert restored.cache_creation_input_tokens == 1

def test_usage_from_dict_missing_cache_fields():
    """旧格式 dict（无 cache 字段）应默认为 0。"""
    u = UnifiedUsage.from_dict({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
    assert u.cache_read_input_tokens == 0
    assert u.cache_creation_input_tokens == 0

# ── ThinkingConfig ────────────────────────────────────────────────────────────

from ccserver.messages.thinking import ThinkingConfig

def test_thinking_config_defaults():
    tc = ThinkingConfig()
    assert tc.enabled is True
    assert tc.effort == "high"
    assert tc.display == "omitted"

def test_thinking_config_disabled():
    tc = ThinkingConfig(enabled=False, effort="low", display="summarized")
    assert tc.enabled is False
    assert tc.effort == "low"
    assert tc.display == "summarized"

# ── UnifiedToolCall ───────────────────────────────────────────────────────────

from ccserver.messages.tool_call import UnifiedToolCall

def test_tool_call_basic():
    tc = UnifiedToolCall(id="t1", name="Bash", input={"cmd": "ls"})
    assert tc.id == "t1"
    assert tc.name == "Bash"
    assert tc.input == {"cmd": "ls"}
    assert tc.provider_data is None

def test_tool_call_with_provider_data():
    tc = UnifiedToolCall(id="t1", name="Bash", input={},
                         provider_data={"call_id": "call_XXX", "response_item_id": "fc_YYY"})
    assert tc.provider_data["call_id"] == "call_XXX"

def test_tool_call_provider_data_runtime_only():
    """provider_data 是运行时字段，UnifiedToolCall 没有 to_dict，确认不暴露序列化接口。"""
    tc = UnifiedToolCall(id="t1", name="Read", input={"path": "/tmp"},
                         provider_data={"thought_signature": "sig123"})
    assert not hasattr(tc, "to_dict")
