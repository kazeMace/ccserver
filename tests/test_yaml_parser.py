"""
tests/test_yaml_parser.py — parse_frontmatter（统一 YAML 解析入口）单元测试

覆盖：
  - 无 frontmatter：返回 ({}, 原始文本)
  - 格式错误（--- 开头但无闭合）：返回 (None, 原始文本)
  - 基础键值对：string、int、float、bool（true/false/yes/no）
  - 列表键：block list（- item 格式）
  - 多行字符串（key: |）
  - URL / 普通逗号句不被误判为列表
  - 注释行（# 开头）忽略
  - 正文（frontmatter 之后内容）正确提取

说明：
  2025-04-11 迁移至 PyYAML 后，以下旧自定义行为不再由解析器承担：
  - 内联逗号分隔字符串不会自动拆分为 list（调用方如需可自己 split）
  - "key:" 空值返回 None 而非 []（符合 YAML 语义）
"""

import pytest

from ccserver.utils.yaml_parser import parse


# ─── 无 frontmatter ───────────────────────────────────────────────────────────


def test_no_frontmatter_returns_empty_meta():
    meta, body = parse("Just some text without frontmatter.")
    assert meta == {}
    assert body == "Just some text without frontmatter."


def test_no_frontmatter_empty_string():
    meta, body = parse("")
    assert meta == {}
    assert body == ""


# ─── 格式错误 ─────────────────────────────────────────────────────────────────


def test_malformed_frontmatter_returns_none():
    # 以 --- 开头但无闭合 ---
    text = "---\nname: test\n"
    meta, body = parse(text)
    assert meta is None
    assert body == text


# ─── 基础类型解析 ─────────────────────────────────────────────────────────────


def test_string_value():
    text = "---\nname: hello world\n---\nbody"
    meta, _ = parse(text)
    assert meta["name"] == "hello world"


def test_integer_value():
    text = "---\ncount: 42\n---\n"
    meta, _ = parse(text)
    assert meta["count"] == 42
    assert isinstance(meta["count"], int)


def test_float_value():
    text = "---\nscore: 3.14\n---\n"
    meta, _ = parse(text)
    assert meta["score"] == pytest.approx(3.14)
    assert isinstance(meta["score"], float)


def test_bool_true_lowercase():
    text = "---\nenabled: true\n---\n"
    meta, _ = parse(text)
    assert meta["enabled"] is True


def test_bool_false_lowercase():
    text = "---\nenabled: false\n---\n"
    meta, _ = parse(text)
    assert meta["enabled"] is False


def test_bool_yes():
    text = "---\nflag: yes\n---\n"
    meta, _ = parse(text)
    assert meta["flag"] is True


def test_bool_no():
    text = "---\nflag: no\n---\n"
    meta, _ = parse(text)
    assert meta["flag"] is False


def test_multiple_keys():
    text = "---\nname: test\ncount: 5\nactive: true\n---\n"
    meta, _ = parse(text)
    assert meta["name"] == "test"
    assert meta["count"] == 5
    assert meta["active"] is True


# ─── 列表（block list）────────────────────────────────────────────────────────


def test_block_list():
    text = "---\ntags:\n  - alpha\n  - beta\n  - gamma\n---\n"
    meta, _ = parse(text)
    assert meta["tags"] == ["alpha", "beta", "gamma"]


def test_block_list_single_item():
    text = "---\ntools:\n  - Bash\n---\n"
    meta, _ = parse(text)
    assert meta["tools"] == ["Bash"]


def test_block_list_empty_key_followed_by_other_key():
    """空值 key 在 YAML 中返回 None。"""
    text = "---\ntags:\nname: other\n---\n"
    meta, _ = parse(text)
    assert meta["tags"] is None
    assert meta["name"] == "other"


# ─── 内联逗号字符串（PyYAML 语义：不自动拆分为 list）──────────────────────────


def test_inline_comma_string():
    text = "---\ntags: alpha,beta,gamma\n---\n"
    meta, _ = parse(text)
    assert meta["tags"] == "alpha,beta,gamma"


def test_inline_comma_string_with_spaces():
    text = "---\ntags: a, b, c\n---\n"
    meta, _ = parse(text)
    assert meta["tags"] == "a, b, c"


def test_inline_comma_string_two_items():
    text = "---\ncolors: red,blue\n---\n"
    meta, _ = parse(text)
    assert meta["colors"] == "red,blue"


# ─── URL 不误判为内联列表 ─────────────────────────────────────────────────────


def test_url_not_split_as_list():
    text = "---\nurl: https://example.com/path,extra\n---\n"
    meta, _ = parse(text)
    # 含 :// 的值不拆成列表
    assert isinstance(meta["url"], str)
    assert meta["url"] == "https://example.com/path,extra"


def test_plain_sentence_with_comma_not_split():
    """普通描述句含逗号但元素有空格，不拆成列表。"""
    text = "---\ndescription: Build X, deploy Y and verify Z\n---\n"
    meta, _ = parse(text)
    assert isinstance(meta["description"], str)


# ─── 多行字符串（key: |）─────────────────────────────────────────────────────


def test_multiline_string():
    text = "---\nbody: |\n  line one\n  line two\n---\n"
    meta, _ = parse(text)
    assert "line one" in meta["body"]
    assert "line two" in meta["body"]


def test_multiline_string_trailing_newline_clip():
    """
    PyYAML '|' (clip) 在 block 末尾无显式换行时不会追加尾随换行。
    由于 frontmatter 正则在 block 结尾处消费了换行，此处行为与迁移前一致。
    """
    text = "---\ntext: |\n  hello\n  world\n---\n"
    meta, _ = parse(text)
    assert not meta["text"].endswith("\n")
    assert meta["text"] == "hello\nworld"


def test_multiline_string_strip_indicator():
    """PyYAML '|-' 可彻底去除尾随换行。"""
    text = "---\ntext: |-\n  hello\n  world\n---\n"
    meta, _ = parse(text)
    assert not meta["text"].endswith("\n")
    assert meta["text"] == "hello\nworld"


# ─── 注释行 ───────────────────────────────────────────────────────────────────


def test_comment_lines_ignored():
    text = "---\n# this is a comment\nname: real\n---\n"
    meta, _ = parse(text)
    assert "name" in meta
    assert meta["name"] == "real"
    # 注释行不作为键
    assert len(meta) == 1


# ─── 正文提取 ─────────────────────────────────────────────────────────────────


def test_body_after_frontmatter():
    text = "---\nname: test\n---\nThis is the body.\nSecond line."
    _, body = parse(text)
    assert "This is the body." in body
    assert "Second line." in body


def test_body_stripped():
    text = "---\nname: x\n---\n\n  body content  \n\n"
    _, body = parse(text)
    assert body == "body content"


def test_empty_body():
    text = "---\nname: x\n---\n"
    _, body = parse(text)
    assert body == ""


# ─── 综合：完整 SKILL.md 格式 ─────────────────────────────────────────────────


def test_full_skill_md_format():
    text = """\
---
name: my-skill
description: A useful skill for testing
tags: python,testing,dev
version: 1
active: true
---
## Instructions

Use this skill to run tests.
"""
    meta, body = parse(text)
    assert meta["name"] == "my-skill"
    assert meta["description"] == "A useful skill for testing"
    assert meta["tags"] == "python,testing,dev"
    assert meta["version"] == 1
    assert meta["active"] is True
    assert "Instructions" in body
