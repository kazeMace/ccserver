"""
tests/test_compactor.py — Compactor 单元测试

覆盖：
  - needs_compact() 阈值判断
  - micro() 截断旧工具结果（in-place 修改）
  - micro() 保留最近 keep_recent 个完整结果
  - micro() 在结果数 <= keep_recent 时不截断
  - micro() 工具名通过 tool_name_map 正确注入
  - micro() 只截断长内容（>100 字符），短内容保留
"""

import pytest
from unittest.mock import MagicMock

from ccserver.compactor import Compactor


def _make_compactor(threshold=1000, keep_recent=2):
    return Compactor(adapter=MagicMock(), threshold=threshold, keep_recent=keep_recent)


def _make_tool_use_block(tool_id: str, name: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {}}


def _make_tool_result_block(tool_use_id: str, content: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content, "is_error": False}


def _make_messages(*pairs):
    """
    pairs: list of (tool_name, result_content)
    为每对生成一个 assistant tool_use 消息 + 一个 user tool_result 消息
    """
    messages = []
    for i, (name, content) in enumerate(pairs):
        tid = f"tid_{i}"
        messages.append({
            "role": "assistant",
            "content": [_make_tool_use_block(tid, name)],
        })
        messages.append({
            "role": "user",
            "content": [_make_tool_result_block(tid, content)],
        })
    return messages


# ─── needs_compact() ─────────────────────────────────────────────────────────


def test_needs_compact_below_threshold():
    comp = _make_compactor(threshold=10000)
    messages = [{"role": "user", "content": "short"}]
    assert comp.needs_compact(messages) is False


def test_needs_compact_above_threshold():
    comp = _make_compactor(threshold=10)
    messages = [{"role": "user", "content": "x" * 200}]
    assert comp.needs_compact(messages) is True


def test_needs_compact_at_threshold_boundary():
    # estimate_tokens 基于 str(messages) 的长度，包含 dict 结构开销
    # 先测量实际 str 长度，再推导 threshold
    from ccserver.utils.sdk import estimate_tokens
    messages = [{"role": "user", "content": "x" * 200}]
    actual_tokens = estimate_tokens(messages)
    # threshold 等于实际 token 数时：actual_tokens > actual_tokens 为 False
    comp_eq = _make_compactor(threshold=actual_tokens)
    assert comp_eq.needs_compact(messages) is False
    # threshold 比实际少 1 时：actual_tokens > (actual_tokens-1) 为 True
    comp_less = _make_compactor(threshold=actual_tokens - 1)
    assert comp_less.needs_compact(messages) is True


def test_needs_compact_empty_messages():
    comp = _make_compactor(threshold=1)
    # 空列表估算 token 数很小，不应触发压缩
    assert comp.needs_compact([]) is False


# ─── micro() 基础行为 ─────────────────────────────────────────────────────────


def test_micro_no_truncation_when_few_results():
    comp = _make_compactor(keep_recent=3)
    # 只有 2 个工具结果，keep_recent=3，不应截断
    long_content = "x" * 200
    messages = _make_messages(("Bash", long_content), ("Read", long_content))
    comp.micro(messages)
    # 结果内容应保持不变
    results = [
        part
        for msg in messages
        if msg["role"] == "user" and isinstance(msg.get("content"), list)
        for part in msg["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert all(r["content"] == long_content for r in results)


def test_micro_truncates_oldest_results():
    comp = _make_compactor(keep_recent=1)
    long_content = "x" * 200
    messages = _make_messages(
        ("Bash", long_content),   # 应被截断（最旧）
        ("Read", long_content),   # 应保留（最新 1 个）
    )
    comp.micro(messages)
    results = [
        part
        for msg in messages
        if msg["role"] == "user" and isinstance(msg.get("content"), list)
        for part in msg["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert "[Previous:" in results[0]["content"]   # 旧的被截断
    assert results[1]["content"] == long_content    # 新的保留


def test_micro_keeps_recent_n_intact():
    comp = _make_compactor(keep_recent=2)
    long_content = "y" * 200
    messages = _make_messages(
        ("Tool1", long_content),  # 截断
        ("Tool2", long_content),  # 保留
        ("Tool3", long_content),  # 保留
    )
    comp.micro(messages)
    results = [
        part
        for msg in messages
        if msg["role"] == "user" and isinstance(msg.get("content"), list)
        for part in msg["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert "[Previous:" in results[0]["content"]
    assert results[1]["content"] == long_content
    assert results[2]["content"] == long_content


def test_micro_injects_tool_name_in_placeholder():
    comp = _make_compactor(keep_recent=1)
    long_content = "z" * 200
    messages = _make_messages(
        ("MyTool", long_content),  # 应被截断，包含工具名
        ("OtherTool", long_content),
    )
    comp.micro(messages)
    results = [
        part
        for msg in messages
        if msg["role"] == "user" and isinstance(msg.get("content"), list)
        for part in msg["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    assert "MyTool" in results[0]["content"]


def test_micro_does_not_truncate_short_content():
    comp = _make_compactor(keep_recent=1)
    # 内容 <= 100 字符，不截断
    short_content = "x" * 50
    messages = _make_messages(
        ("Bash", short_content),   # 短内容，不应截断
        ("Read", "y" * 200),
    )
    comp.micro(messages)
    results = [
        part
        for msg in messages
        if msg["role"] == "user" and isinstance(msg.get("content"), list)
        for part in msg["content"]
        if isinstance(part, dict) and part.get("type") == "tool_result"
    ]
    # 短内容保留原文（即使它是"最旧"的）
    assert results[0]["content"] == short_content


def test_micro_returns_same_messages_list():
    comp = _make_compactor(keep_recent=1)
    messages = _make_messages(("Bash", "x" * 200), ("Read", "y" * 200))
    returned = comp.micro(messages)
    # micro() 应返回同一个列表对象（in-place 修改）
    assert returned is messages


def test_micro_handles_empty_messages():
    comp = _make_compactor()
    result = comp.micro([])
    assert result == []


def test_micro_handles_messages_without_tool_results():
    comp = _make_compactor(keep_recent=1)
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "Response"}]},
    ]
    original = [m.copy() for m in messages]
    comp.micro(messages)
    # 无工具结果，消息不应被修改
    for orig, msg in zip(original, messages):
        assert orig == msg
