"""
tests/test_matcher.py — HookMatcher 单元测试

直接导入 ccserver/hooks/matcher.py，绕过 ccserver 包导入链，
从而避免 Session / HookLoader 等依赖未安装时导致测试无法运行。
"""

import sys
from pathlib import Path

# 让 import matcher 直接从 ccserver/hooks/ 加载
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ccserver" / "hooks"))
import matcher


# ─── AlwaysMatcher ────────────────────────────────────────────────────────────


def test_always_matcher():
    m = matcher.AlwaysMatcher()
    assert m.match({}) is True
    assert m.match({"tool_name": "Bash"}) is True


# ─── LiteralMatcher ───────────────────────────────────────────────────────────


def test_literal_exact():
    m = matcher.LiteralMatcher("Bash")
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Write"}) is False


def test_literal_multi():
    m = matcher.LiteralMatcher("Bash|Write")
    assert m.match({"tool_name": "Write"}) is True
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Edit"}) is False


def test_literal_regex():
    m = matcher.LiteralMatcher("^Write.*")
    assert m.match({"tool_name": "WriteFile"}) is True
    assert m.match({"tool_name": "Bash"}) is False


def test_literal_invalid_regex():
    m = matcher.LiteralMatcher("[invalid")
    # 非法正则被静默忽略，返回 False
    assert m.match({"tool_name": "anything"}) is False


def test_literal_empty_payload():
    m = matcher.LiteralMatcher("Bash")
    assert m.match({}) is False


# ─── ExpressionMatcher ────────────────────────────────────────────────────────


def test_expr_string_eq():
    m = matcher.ExpressionMatcher('tool == "Bash"')
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Write"}) is False


def test_expr_string_ne():
    m = matcher.ExpressionMatcher('tool != "Bash"')
    assert m.match({"tool_name": "Write"}) is True
    assert m.match({"tool_name": "Bash"}) is False


def test_expr_matches():
    m = matcher.ExpressionMatcher('tool_input.command matches "git *"')
    assert m.match({"tool_input": {"command": "git status"}}) is True
    assert m.match({"tool_input": {"command": "npm install"}}) is False


def test_expr_contains():
    m = matcher.ExpressionMatcher('tool_input.command contains "run"')
    assert m.match({"tool_input": {"command": "npm run dev"}}) is True
    assert m.match({"tool_input": {"command": "git status"}}) is False


def test_expr_startswith():
    m = matcher.ExpressionMatcher('tool_input.command startswith "git"')
    assert m.match({"tool_input": {"command": "git push"}}) is True
    assert m.match({"tool_input": {"command": "npm install"}}) is False


def test_expr_endswith():
    m = matcher.ExpressionMatcher('tool_input.command endswith "dev"')
    assert m.match({"tool_input": {"command": "bun run dev"}}) is True
    assert m.match({"tool_input": {"command": "git push"}}) is False


def test_expr_and():
    m = matcher.ExpressionMatcher('tool == "Bash" && tool_input.command matches "git *"')
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "git status"}}) is True
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "npm run"}}) is False
    assert m.match({"tool_name": "Write", "tool_input": {"command": "git status"}}) is False


def test_expr_or():
    m = matcher.ExpressionMatcher('tool == "Bash" || tool == "Write"')
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Write"}) is True
    assert m.match({"tool_name": "Edit"}) is False


def test_expr_not():
    m = matcher.ExpressionMatcher('!tool == "Bash"')
    assert m.match({"tool_name": "Write"}) is True
    assert m.match({"tool_name": "Bash"}) is False


def test_expr_parens():
    m = matcher.ExpressionMatcher('(tool == "Bash" || tool == "Write") && tool_input.file matches "\\.md$"')
    assert m.match({"tool_name": "Bash", "tool_input": {"file": "readme.md"}}) is True
    assert m.match({"tool_name": "Write", "tool_input": {"file": "readme.md"}}) is True
    assert m.match({"tool_name": "Write", "tool_input": {"file": "readme.txt"}}) is False


def test_expr_number_literal():
    m = matcher.ExpressionMatcher("1 == 1")
    assert m.match({}) is True
    m2 = matcher.ExpressionMatcher("1 != 2")
    assert m2.match({}) is True


def test_expr_bool_literal():
    m = matcher.ExpressionMatcher("true")
    assert m.match({}) is True
    m2 = matcher.ExpressionMatcher("false")
    assert m2.match({}) is False


def test_expr_field_access():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/test.md", "content": "hello"},
        "tool_response": "done",
        "tool_use_id": "tu-123",
    }
    assert matcher.ExpressionMatcher('tool == "Write"').match(payload) is True
    assert matcher.ExpressionMatcher('tool_input.file_path == "/tmp/test.md"').match(payload) is True
    assert matcher.ExpressionMatcher('tool_output == "done"').match(payload) is True
    assert matcher.ExpressionMatcher('tool_use_id == "tu-123"').match(payload) is True


def test_expr_missing_field_safe():
    m = matcher.ExpressionMatcher('tool_input.missing == "x"')
    # 缺失字段返回 None，比较结果应为 False
    assert m.match({"tool_input": {"other": 1}}) is False


def test_expr_reusable():
    """同一个 ExpressionMatcher 实例可被多次调用。"""
    m = matcher.ExpressionMatcher('tool == "Bash"')
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Bash"}) is True
    assert m.match({"tool_name": "Write"}) is False


def test_expr_real_world_complex():
    """来自 Claude Code 真实配置的复杂 matcher。"""
    expr = (
        'tool == "Bash" && tool_input.command matches '
        '"(npm run dev|pnpm( run)? dev|yarn dev|bun run dev)"'
    )
    m = matcher.ExpressionMatcher(expr)
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "npm run dev"}}) is True
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "pnpm dev"}}) is True
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "yarn dev"}}) is True
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "bun run dev"}}) is True
    assert m.match({"tool_name": "Bash", "tool_input": {"command": "npm run build"}}) is False
    assert m.match({"tool_name": "Write", "tool_input": {"command": "npm run dev"}}) is False


def test_expr_syntax_error_returns_false():
    m = matcher.ExpressionMatcher("tool ==")
    assert m.match({"tool_name": "Bash"}) is False


# ─── build_matcher factory ────────────────────────────────────────────────────


def test_build_empty_or_star():
    assert isinstance(matcher.build_matcher(""), matcher.AlwaysMatcher)
    assert isinstance(matcher.build_matcher("*"), matcher.AlwaysMatcher)


def test_build_literal():
    assert isinstance(matcher.build_matcher("Bash"), matcher.LiteralMatcher)
    assert isinstance(matcher.build_matcher("Bash|Write"), matcher.LiteralMatcher)
    assert isinstance(matcher.build_matcher("^Write.*"), matcher.LiteralMatcher)


def test_build_expression():
    m = matcher.build_matcher('tool == "Bash"')
    assert isinstance(m, matcher.ExpressionMatcher)
    assert m.match({"tool_name": "Bash"}) is True


def test_build_none():
    assert isinstance(matcher.build_matcher(None), matcher.AlwaysMatcher)
