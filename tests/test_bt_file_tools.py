"""
tests/test_bt_file_tools.py — BTRead / BTWrite / BTEdit / BTGlob / BTGrep 单元测试

覆盖：
  BTRead:
    - 读取文件内容，带 1-based 行号
    - offset/limit 分页
    - 文件不存在返回错误
    - 路径逃逸返回错误

  BTWrite:
    - 写入新文件
    - 覆盖已存在文件
    - 自动创建父目录
    - 路径逃逸返回错误

  BTEdit:
    - 替换唯一 old_string
    - old_string 不存在返回错误
    - 多处出现且 replace_all=False 返回错误
    - replace_all=True 替换所有出现
    - old_string 为空字符串替换（删除操作）
    - 路径逃逸返回错误

  BTGlob:
    - 通配符匹配文件
    - 无匹配返回 "none"
    - 路径逃逸返回错误

  BTGrep:
    - 正则匹配文件内容
    - 无匹配返回 "none"
    - 无效正则返回错误
    - 路径逃逸返回错误
"""

import asyncio
import pytest
from pathlib import Path

from ccserver.builtins.tools import BTRead
from ccserver.builtins.tools import BTWrite
from ccserver.builtins.tools import BTEdit
from ccserver.builtins.tools import BTGlob
from ccserver.builtins.tools import BTGrep


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── BTRead ───────────────────────────────────────────────────────────────────


def test_read_basic(tmp_path):
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3")
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="hello.txt"))
    assert result.is_error is False
    assert "1\tline1" in result.content
    assert "2\tline2" in result.content
    assert "3\tline3" in result.content


def test_read_offset(tmp_path):
    (tmp_path / "f.txt").write_text("a\nb\nc\nd")
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="f.txt", offset=2))
    assert "3\tc" in result.content
    assert "1\ta" not in result.content


def test_read_limit(tmp_path):
    (tmp_path / "f.txt").write_text("\n".join(str(i) for i in range(10)))
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="f.txt", limit=3))
    assert "1\t0" in result.content
    assert "3\t2" in result.content
    # 超出 limit 部分不显示（显示省略提示）
    assert "more lines" in result.content


def test_read_nonexistent_file(tmp_path):
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="no_such_file.txt"))
    assert result.is_error is True


def test_read_path_escape(tmp_path):
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="../../etc/passwd"))
    assert result.is_error is True


def test_read_empty_file(tmp_path):
    (tmp_path / "empty.txt").write_text("")
    tool = BTRead(tmp_path)
    result = _run(tool(file_path="empty.txt"))
    assert result.is_error is False
    assert result.content == ""


# ─── BTWrite ─────────────────────────────────────────────────────────────────


def test_write_creates_file(tmp_path):
    tool = BTWrite(tmp_path)
    result = _run(tool(file_path="new.txt", content="hello world"))
    assert result.is_error is False
    assert (tmp_path / "new.txt").read_text() == "hello world"


def test_write_overwrites_existing(tmp_path):
    (tmp_path / "existing.txt").write_text("old content")
    tool = BTWrite(tmp_path)
    _run(tool(file_path="existing.txt", content="new content"))
    assert (tmp_path / "existing.txt").read_text() == "new content"


def test_write_creates_parent_dirs(tmp_path):
    tool = BTWrite(tmp_path)
    result = _run(tool(file_path="a/b/c/file.txt", content="deep"))
    assert result.is_error is False
    assert (tmp_path / "a" / "b" / "c" / "file.txt").read_text() == "deep"


def test_write_path_escape(tmp_path):
    tool = BTWrite(tmp_path)
    result = _run(tool(file_path="../../evil.txt", content="bad"))
    assert result.is_error is True


def test_write_returns_byte_count(tmp_path):
    tool = BTWrite(tmp_path)
    content = "hello"
    result = _run(tool(file_path="f.txt", content=content))
    assert str(len(content)) in result.content


# ─── BTEdit ──────────────────────────────────────────────────────────────────


def test_edit_replaces_unique_string(tmp_path):
    (tmp_path / "f.txt").write_text("foo bar baz")
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="f.txt", old_string="bar", new_string="qux"))
    assert result.is_error is False
    assert (tmp_path / "f.txt").read_text() == "foo qux baz"


def test_edit_old_string_not_found(tmp_path):
    (tmp_path / "f.txt").write_text("hello world")
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="f.txt", old_string="nonexistent", new_string="x"))
    assert result.is_error is True
    assert "not found" in result.content


def test_edit_duplicate_string_without_replace_all(tmp_path):
    (tmp_path / "f.txt").write_text("abc abc abc")
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="f.txt", old_string="abc", new_string="xyz"))
    assert result.is_error is True
    assert "3" in result.content  # 显示出现次数


def test_edit_replace_all(tmp_path):
    (tmp_path / "f.txt").write_text("abc abc abc")
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="f.txt", old_string="abc", new_string="xyz", replace_all=True))
    assert result.is_error is False
    assert (tmp_path / "f.txt").read_text() == "xyz xyz xyz"


def test_edit_delete_by_empty_new_string(tmp_path):
    (tmp_path / "f.txt").write_text("prefix_REMOVE_suffix")
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="f.txt", old_string="_REMOVE_", new_string=""))
    assert result.is_error is False
    assert (tmp_path / "f.txt").read_text() == "prefixsuffix"


def test_edit_path_escape(tmp_path):
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="../../bad.txt", old_string="x", new_string="y"))
    assert result.is_error is True


def test_edit_file_not_exist(tmp_path):
    tool = BTEdit(tmp_path)
    result = _run(tool(file_path="missing.txt", old_string="x", new_string="y"))
    assert result.is_error is True


# ─── BTGlob ───────────────────────────────────────────────────────────────────


def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "c.txt").write_text("")
    tool = BTGlob(tmp_path)
    result = _run(tool(pattern="*.py"))
    assert result.is_error is False
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert "c.txt" not in result.content


def test_glob_recursive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("")
    tool = BTGlob(tmp_path)
    result = _run(tool(pattern="**/*.py"))
    assert "deep.py" in result.content


def test_glob_no_match_returns_none(tmp_path):
    tool = BTGlob(tmp_path)
    result = _run(tool(pattern="*.nonexistent"))
    assert result.is_error is False
    assert result.content == "none"


def test_glob_path_escape(tmp_path):
    tool = BTGlob(tmp_path)
    result = _run(tool(pattern="*.py", path="../../"))
    assert result.is_error is True


def test_glob_with_subdirectory(tmp_path):
    sub = tmp_path / "mydir"
    sub.mkdir()
    (sub / "file.txt").write_text("")
    tool = BTGlob(tmp_path)
    result = _run(tool(pattern="*.txt", path="mydir"))
    assert "file.txt" in result.content


# ─── BTGrep ───────────────────────────────────────────────────────────────────


def test_grep_finds_matching_lines(tmp_path):
    (tmp_path / "code.py").write_text("def foo():\n    pass\ndef bar():\n    pass\n")
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="def \\w+"))
    assert result.is_error is False
    assert "def foo" in result.content
    assert "def bar" in result.content


def test_grep_no_match_returns_none(tmp_path):
    (tmp_path / "f.txt").write_text("hello world")
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="nonexistent_xyz_pattern"))
    assert result.is_error is False
    assert result.content == "none"


def test_grep_invalid_regex_returns_error(tmp_path):
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="[invalid"))
    assert result.is_error is True
    assert "Invalid regex" in result.content


def test_grep_path_escape(tmp_path):
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern=".*", path="../../"))
    assert result.is_error is True


def test_grep_output_format(tmp_path):
    (tmp_path / "sample.txt").write_text("first line\nsecond line\nthird line\n")
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="second"))
    # 格式: filepath:line_number:line_content
    assert ":2:second line" in result.content


def test_grep_caps_at_50_results(tmp_path):
    # 生成 100 行匹配内容
    lines = "\n".join([f"match line {i}" for i in range(100)])
    (tmp_path / "big.txt").write_text(lines)
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="match line"))
    output_lines = [l for l in result.content.split("\n") if l]
    assert len(output_lines) <= 50


def test_grep_case_sensitive_by_default(tmp_path):
    (tmp_path / "f.txt").write_text("Hello World\nhello world\n")
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="Hello"))
    assert "Hello World" in result.content
    # 大小写敏感：小写 hello 不应匹配
    lines = result.content.split("\n")
    matched_contents = [l.split(":", 2)[-1] if ":" in l else "" for l in lines if l]
    assert not any("hello world" == c for c in matched_contents)


def test_grep_case_insensitive_with_flag(tmp_path):
    (tmp_path / "f.txt").write_text("Hello World\nhello world\n")
    tool = BTGrep(tmp_path)
    result = _run(tool(pattern="(?i)hello"))
    assert "Hello World" in result.content
    assert "hello world" in result.content
