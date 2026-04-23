"""
tests/test_bt_bash.py — BTBash 命令权限 validate() 测试

覆盖：
  - 硬编码兜底黑名单（_HARDBLOCK）
  - denied_commands：命令前缀黑名单
  - allowed_commands：命令前缀白名单
  - 三层优先级顺序（hardblock > deny > allow）
  - None 表示不限制的语义
"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from ccserver.builtins.tools import BTBash
from ccserver.settings import ProjectSettings


def _make_settings(allowed=None, denied=None) -> ProjectSettings:
    allowed_commands = {"Bash": allowed} if allowed is not None else None
    denied_commands = {"Bash": denied} if denied is not None else {}
    return ProjectSettings(
        allowed_tools=None,
        denied_tools=frozenset(),
        allowed_commands=allowed_commands,
        denied_commands=denied_commands,
        enabled_mcp_servers=None,
    )


def _make_bash(allowed=None, denied=None) -> BTBash:
    return BTBash(Path("/tmp"), settings=_make_settings(allowed=allowed, denied=denied))


def _validate(bash: BTBash, command: str):
    """同步调用 validate()。"""
    return asyncio.get_event_loop().run_until_complete(bash.validate(command=command))


# ─── 硬编码兜底黑名单（_HARDBLOCK）──────────────────────────────────────────


def test_hardblock_rm_rf_root():
    result = _validate(_make_bash(), "rm -rf /")
    assert result is not None
    assert result.is_error


def test_hardblock_shutdown():
    result = _validate(_make_bash(), "shutdown -h now")
    assert result is not None
    assert result.is_error


def test_hardblock_reboot():
    result = _validate(_make_bash(), "reboot")
    assert result is not None
    assert result.is_error


def test_hardblock_dev_null_redirect():
    result = _validate(_make_bash(), "echo foo > /dev/null")
    assert result is not None
    assert result.is_error


def test_hardblock_not_triggered_for_normal_cmd():
    result = _validate(_make_bash(), "ls -la")
    assert result is None


# ─── denied_commands ─────────────────────────────────────────────────────────


def test_denied_command_blocked():
    result = _validate(_make_bash(denied=["rm", "sudo"]), "rm -rf .")
    assert result is not None
    assert result.is_error


def test_denied_command_prefix_match():
    result = _validate(_make_bash(denied=["rm"]), "rm file.txt")
    assert result is not None
    assert result.is_error


def test_denied_command_no_false_positive():
    result = _validate(_make_bash(denied=["rm"]), "grep -r foo .")
    assert result is None


def test_denied_commands_none_no_restriction():
    # denied=None 无黑名单，且命令不触发 _HARDBLOCK
    result = _validate(_make_bash(denied=None), "rm -rf .")
    assert result is None


# ─── allowed_commands ────────────────────────────────────────────────────────


def test_allowed_command_passes():
    result = _validate(_make_bash(allowed=["git", "pytest"]), "git status")
    assert result is None


def test_allowed_command_not_in_list_blocked():
    result = _validate(_make_bash(allowed=["git", "pytest"]), "ls -la")
    assert result is not None
    assert result.is_error


def test_allowed_none_means_no_restriction():
    result = _validate(_make_bash(allowed=None), "ls -la")
    assert result is None


def test_allowed_prefix_match():
    result = _validate(_make_bash(allowed=["git"]), "git commit -m 'test'")
    assert result is None


# ─── 三层优先级：hardblock > deny > allow ────────────────────────────────────


def test_hardblock_overrides_allow():
    # 即使 allowed 包含 "rm -rf /"，hardblock 仍然拒绝
    result = _validate(_make_bash(allowed=["rm -rf /"]), "rm -rf /")
    assert result is not None
    assert result.is_error


def test_deny_overrides_allow():
    # 同时在 allow 和 deny 中，deny 优先
    result = _validate(_make_bash(allowed=["git"], denied=["git"]), "git status")
    assert result is not None
    assert result.is_error


def test_no_config_allows_all_non_hardblock():
    # 无任何配置，除 _HARDBLOCK 外全部允许
    result = _validate(_make_bash(allowed=None, denied=None), "arbitrary-command --flag")
    assert result is None
