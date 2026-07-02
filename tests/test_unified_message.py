"""
tests/test_unified_message.py

TDD 测试：UnifiedMessage、serialization（unified_message_to_wire / wire_to_unified_message / block_from_dict）

测试覆盖：
- UnifiedMessage 基本属性
- to_dict 列表 content 路径
- to_dict 单个 CommandBlock 路径（content 折叠为 dict）
- metadata 透传
- wire 往返（text / tool_result / command block）
- wire_to_unified_message 各种 content 形态（str / dict command / dict unknown / list）
- block_from_dict 分派（所有已知 type / 未知 type / _type command）
- unified_message_to_wire 传入 dict 时原样返回
"""

import pytest

from ccserver.messages.blocks import (
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
from ccserver.messages.unified_message import UnifiedMessage
from ccserver.messages.serialization import (
    block_from_dict,
    unified_message_to_wire,
    wire_to_unified_message,
)


# ─────────────────────────────────────────────────────────────
# UnifiedMessage 基本属性
# ─────────────────────────────────────────────────────────────

def test_role_and_content_basic():
    """UnifiedMessage role / content 基本属性"""
    msg = UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hello")])
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], UnifiedTextBlock)
    assert msg.content[0].text == "hello"


def test_to_dict_list_content():
    """list[block] → {"role": ..., "content": [...]}"""
    msg = UnifiedMessage(
        role="assistant",
        content=[UnifiedTextBlock(text="hi"), UnifiedThinkingBlock(thinking="let me think")],
    )
    d = msg.to_dict()
    assert d["role"] == "assistant"
    assert isinstance(d["content"], list)
    assert d["content"][0] == {"type": "text", "text": "hi"}
    assert d["content"][1] == {"type": "thinking", "thinking": "let me think"}


def test_to_dict_single_command_block():
    """单个 UnifiedCommandBlock → content 折叠为 {"_type": "command", ...}（不是 list）"""
    cmd = UnifiedCommandBlock(name="bash", args="ls", stdout="a.py", body="a.py\n")
    msg = UnifiedMessage(role="user", content=[cmd])
    d = msg.to_dict()
    assert d["role"] == "user"
    # content 应该是 dict（不是 list）
    assert isinstance(d["content"], dict)
    assert d["content"]["_type"] == "command"
    assert d["content"]["name"] == "bash"


def test_metadata_passthrough():
    """metadata 字段透传到 to_dict 顶层"""
    msg = UnifiedMessage(
        role="user",
        content=[UnifiedTextBlock(text="hi")],
        metadata={"_ccserver_team_new_task": True, "task_id": "abc-123"},
    )
    d = msg.to_dict()
    assert d["_ccserver_team_new_task"] is True
    assert d["task_id"] == "abc-123"
    # role / content 仍然存在
    assert d["role"] == "user"


# ─────────────────────────────────────────────────────────────
# wire 往返测试
# ─────────────────────────────────────────────────────────────

def test_wire_roundtrip_text():
    """text block 的 wire 往返：UnifiedMessage → wire dict → UnifiedMessage"""
    original = UnifiedMessage(
        role="user",
        content=[UnifiedTextBlock(text="hello world")],
    )
    wire = unified_message_to_wire(original)
    restored = wire_to_unified_message(wire)

    assert restored.role == "user"
    assert len(restored.content) == 1
    assert isinstance(restored.content[0], UnifiedTextBlock)
    assert restored.content[0].text == "hello world"


def test_wire_roundtrip_tool_result():
    """包含 tool_result block 的往返"""
    original = UnifiedMessage(
        role="user",
        content=[
            UnifiedToolResultBlock(
                tool_use_id="call-1",
                content="read result",
                is_error=False,
            )
        ],
    )
    wire = unified_message_to_wire(original)
    restored = wire_to_unified_message(wire)

    assert restored.role == "user"
    assert len(restored.content) == 1
    block = restored.content[0]
    assert isinstance(block, UnifiedToolResultBlock)
    assert block.tool_use_id == "call-1"
    assert block.content == "read result"


def test_wire_roundtrip_command():
    """包含 command block 的往返"""
    original = UnifiedMessage(
        role="user",
        content=[UnifiedCommandBlock(name="bash", args="-c ls", stdout="a.py", body="a.py\n")],
    )
    wire = unified_message_to_wire(original)
    restored = wire_to_unified_message(wire)

    assert restored.role == "user"
    assert len(restored.content) == 1
    block = restored.content[0]
    assert isinstance(block, UnifiedCommandBlock)
    assert block.name == "bash"
    assert block.args == "-c ls"


# ─────────────────────────────────────────────────────────────
# wire_to_unified_message 各种 content 形态
# ─────────────────────────────────────────────────────────────

def test_wire_to_unified_str_content():
    """wire dict content 为字符串 → 单个 UnifiedTextBlock"""
    d = {"role": "user", "content": "plain text"}
    msg = wire_to_unified_message(d)
    assert msg.role == "user"
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], UnifiedTextBlock)
    assert msg.content[0].text == "plain text"


def test_wire_to_unified_dict_content_command():
    """wire dict content 为 {"_type":"command",...} → UnifiedCommandBlock"""
    d = {
        "role": "user",
        "content": {"_type": "command", "name": "grep", "args": "foo", "stdout": "match", "body": "match\n"},
    }
    msg = wire_to_unified_message(d)
    assert len(msg.content) == 1
    block = msg.content[0]
    assert isinstance(block, UnifiedCommandBlock)
    assert block.name == "grep"


def test_wire_to_unified_dict_content_unknown():
    """wire dict content 为未知 dict → UnifiedPassthroughBlock"""
    d = {"role": "user", "content": {"some_key": "some_value"}}
    msg = wire_to_unified_message(d)
    assert len(msg.content) == 1
    block = msg.content[0]
    assert isinstance(block, UnifiedPassthroughBlock)


# ─────────────────────────────────────────────────────────────
# block_from_dict 分派
# ─────────────────────────────────────────────────────────────

def test_block_from_dict_known_types():
    """所有已知 type 均正确分派到对应类"""
    cases = [
        ({"type": "text", "text": "hi"}, UnifiedTextBlock),
        ({"type": "thinking", "thinking": "hmm"}, UnifiedThinkingBlock),
        ({"type": "tool_use", "id": "1", "name": "fn", "input": {}}, UnifiedToolUseBlock),
        ({"type": "tool_result", "tool_use_id": "1", "content": "ok"}, UnifiedToolResultBlock),
        ({"type": "image", "source": {"type": "base64"}}, UnifiedImageBlock),
        ({"type": "image_thumbnail", "source": {}}, UnifiedImageThumbnailBlock),
        ({"type": "file", "file_id": "f1", "filename": "a.txt", "mime_type": "text/plain"}, UnifiedFileBlock),
        ({"_type": "command", "name": "bash", "args": "", "stdout": "", "body": ""}, UnifiedCommandBlock),
    ]
    for d, expected_cls in cases:
        result = block_from_dict(d)
        assert isinstance(result, expected_cls), (
            f"block_from_dict({d!r}) 应返回 {expected_cls.__name__}，实际返回 {type(result).__name__}"
        )


def test_block_from_dict_unknown_type():
    """未知 type → UnifiedPassthroughBlock"""
    d = {"type": "future_type_xyz", "data": 42}
    result = block_from_dict(d)
    assert isinstance(result, UnifiedPassthroughBlock)


def test_block_from_dict_command_underscore_type():
    """_type: "command" → UnifiedCommandBlock（优先于 type 字段分派）"""
    d = {"_type": "command", "name": "cat", "args": "file.txt", "stdout": "", "body": ""}
    result = block_from_dict(d)
    assert isinstance(result, UnifiedCommandBlock)
    assert result.name == "cat"


# ─────────────────────────────────────────────────────────────
# unified_message_to_wire 传入 dict
# ─────────────────────────────────────────────────────────────

def test_unified_message_to_wire_already_dict():
    """unified_message_to_wire 传入 dict 时原样返回（过渡期兼容）"""
    d = {"role": "user", "content": "hello"}
    result = unified_message_to_wire(d)
    assert result is d   # 原样返回，同一个对象
