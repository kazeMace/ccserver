"""
tests/test_utils.py — 工具函数单元测试

覆盖：
  src/tools/utils.py:
    - safe_path() 正常路径解析
    - safe_path() 路径逃逸检测
    - safe_path() 相对路径处理

  src/utils/sdk.py:
    - get_block_attr() dict 和 SDK 对象
    - estimate_tokens() 字符数/4 估算
    - generate_message_id() 格式和唯一性
"""

import pytest
from unittest.mock import MagicMock

from ccserver.utils import safe_path
from ccserver.utils.sdk import get_block_attr, estimate_tokens, generate_message_id


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


# ─── get_block_attr() ────────────────────────────────────────────────────────


def test_block_get_from_dict():
    block = {"type": "text", "text": "hello"}
    assert get_block_attr(block, "type") == "text"
    assert get_block_attr(block, "text") == "hello"


def test_block_get_missing_key_from_dict():
    block = {"type": "text"}
    assert get_block_attr(block, "nonexistent") is None


def test_block_get_from_sdk_object():
    obj = MagicMock()
    obj.type = "tool_use"
    obj.name = "Bash"
    assert get_block_attr(obj, "type") == "tool_use"
    assert get_block_attr(obj, "name") == "Bash"


def test_block_get_missing_attr_from_sdk_object():
    obj = MagicMock(spec=[])  # spec=[] 让 getattr 返回 None
    assert get_block_attr(obj, "nonexistent") is None


# ─── normalize_content_blocks() ──────────────────────────────────────────────
# R3 S4：normalize_content_blocks 已删除（生产侧改用 messages 包 block.to_dict），
# 对应单测随之移除。详见 R3 S4 迁移说明。


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
    # estimate_tokens 逐条消息累加 len(str(m))，不含外层 list 括号
    raw_len = sum(len(str(m)) for m in msgs)
    assert token_estimate == raw_len // 4, f"Expected {raw_len // 4}, got {token_estimate}"


# ─── generate_message_id() ───────────────────────────────────────────────────


def test_gen_uuid_is_string():
    uid = generate_message_id()
    assert isinstance(uid, str)


def test_gen_uuid_contains_timestamp():
    uid = generate_message_id()
    # 格式：{uuid}-{yyyyMMddHHmmssSSS}
    parts = uid.split("-")
    # UUID 本身有 5 段（含连字符）
    assert len(parts) >= 6


def test_gen_uuid_unique():
    ids = {generate_message_id() for _ in range(100)}
    assert len(ids) == 100


def test_gen_uuid_timestamp_length():
    uid = generate_message_id()
    # 时间戳部分在最后，格式为 yyyyMMddHHmmssSSS（17位数字）
    timestamp_part = uid.split("-")[-1]
    assert len(timestamp_part) == 17
    assert timestamp_part.isdigit()
