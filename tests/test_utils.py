"""
tests/test_utils.py — 工具函数单元测试

覆盖：
  src/tools/utils.py:
    - safe_path() 正常路径解析
    - safe_path() 路径逃逸检测
    - safe_path() 相对路径处理

  src/utils/sdk.py:
    - _block_get() dict 和 SDK 对象
    - _normalize_content() SDK 对象转 dict
    - estimate_tokens() 字符数/4 估算
    - gen_uuid() 格式和唯一性
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ccserver.tools.utils import safe_path
from ccserver.utils.sdk import _block_get, _normalize_content, estimate_tokens, gen_uuid


# ─── safe_path() ─────────────────────────────────────────────────────────────


def test_safe_path_simple_relative(tmp_path):
    p = safe_path(tmp_path, "file.txt")
    assert p == (tmp_path / "file.txt").resolve()


def test_safe_path_subdirectory(tmp_path):
    p = safe_path(tmp_path, "a/b/c.py")
    assert p == (tmp_path / "a" / "b" / "c.py").resolve()


def test_safe_path_dot_prefix(tmp_path):
    p = safe_path(tmp_path, "./src/main.py")
    assert p == (tmp_path / "src" / "main.py").resolve()


def test_safe_path_escape_raises(tmp_path):
    with pytest.raises(ValueError, match="escapes workspace"):
        safe_path(tmp_path, "../../etc/passwd")


def test_safe_path_parent_escape_raises(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../outside.txt")


def test_safe_path_stays_within_workdir(tmp_path):
    # 先进去再返回，但仍在范围内
    p = safe_path(tmp_path, "a/../b.txt")
    assert p == (tmp_path / "b.txt").resolve()
    assert p.is_relative_to(tmp_path.resolve())


def test_safe_path_absolute_outside_raises(tmp_path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, "/etc/passwd")


# ─── _block_get() ────────────────────────────────────────────────────────────


def test_block_get_from_dict():
    block = {"type": "text", "text": "hello"}
    assert _block_get(block, "type") == "text"
    assert _block_get(block, "text") == "hello"


def test_block_get_missing_key_from_dict():
    block = {"type": "text"}
    assert _block_get(block, "nonexistent") is None


def test_block_get_from_sdk_object():
    obj = MagicMock()
    obj.type = "tool_use"
    obj.name = "Bash"
    assert _block_get(obj, "type") == "tool_use"
    assert _block_get(obj, "name") == "Bash"


def test_block_get_missing_attr_from_sdk_object():
    obj = MagicMock(spec=[])  # spec=[] 让 getattr 返回 None
    assert _block_get(obj, "nonexistent") is None


# ─── _normalize_content() ────────────────────────────────────────────────────


def test_normalize_content_passthrough_dicts():
    content = [{"type": "text", "text": "hello"}, {"type": "other"}]
    result = _normalize_content(content)
    assert result == content


def test_normalize_content_sdk_text_block():
    block = MagicMock()
    block.type = "text"
    block.text = "hello world"
    result = _normalize_content([block])
    assert result == [{"type": "text", "text": "hello world"}]


def test_normalize_content_sdk_tool_use_block():
    block = MagicMock()
    block.type = "tool_use"
    block.id = "abc123"
    block.name = "Bash"
    block.input = {"command": "ls"}
    result = _normalize_content([block])
    assert result == [{"type": "tool_use", "id": "abc123", "name": "Bash", "input": {"command": "ls"}}]


def test_normalize_content_unknown_type():
    block = MagicMock()
    block.type = "unknown_type"
    result = _normalize_content([block])
    assert result == [{"type": "unknown_type"}]


def test_normalize_content_empty_list():
    assert _normalize_content([]) == []


def test_normalize_content_mixed():
    text_dict = {"type": "text", "text": "plain dict"}
    sdk_block = MagicMock()
    sdk_block.type = "text"
    sdk_block.text = "sdk object"
    result = _normalize_content([text_dict, sdk_block])
    assert len(result) == 2
    assert result[0] == text_dict
    assert result[1] == {"type": "text", "text": "sdk object"}


# ─── estimate_tokens() ───────────────────────────────────────────────────────


def test_estimate_tokens_empty():
    assert estimate_tokens([]) == 0


def test_estimate_tokens_single_short_message():
    msgs = [{"role": "user", "content": "hello"}]
    tokens = estimate_tokens(msgs)
    assert isinstance(tokens, int)
    assert tokens > 0


def test_estimate_tokens_proportional():
    short_msgs = [{"role": "user", "content": "x" * 100}]
    long_msgs = [{"role": "user", "content": "x" * 1000}]
    assert estimate_tokens(long_msgs) > estimate_tokens(short_msgs)


def test_estimate_tokens_formula():
    # 使用足够长内容，让 str(messages) 长度成为主导因子
    content = "x" * 400
    msgs = [{"role": "user", "content": content}]
    token_estimate = estimate_tokens(msgs)
    # str(msgs) 的长度约是 content 长度 + 少量 JSON 结构开销
    raw_len = len(str(msgs))
    assert token_estimate == raw_len // 4


# ─── gen_uuid() ──────────────────────────────────────────────────────────────


def test_gen_uuid_is_string():
    uid = gen_uuid()
    assert isinstance(uid, str)


def test_gen_uuid_contains_timestamp():
    uid = gen_uuid()
    # 格式：{uuid}-{yyyyMMddHHmmssSSS}
    parts = uid.split("-")
    # UUID 本身有 5 段（含连字符）
    assert len(parts) >= 6


def test_gen_uuid_unique():
    ids = {gen_uuid() for _ in range(100)}
    assert len(ids) == 100


def test_gen_uuid_timestamp_length():
    uid = gen_uuid()
    # 时间戳部分在最后，格式为 yyyyMMddHHmmssSSS（17位数字）
    timestamp_part = uid.split("-")[-1]
    assert len(timestamp_part) == 17
    assert timestamp_part.isdigit()
