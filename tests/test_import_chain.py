"""Test import chain: session → tasks → bash → session"""
import pytest
from ccserver.tasks import ShellTaskRegistry, ShellTaskState
from ccserver.session import Session
from ccserver.builtins.tools.bash import BTBash


def test_import_chain():
    """导入链不应产生循环导入错误。"""
    # session → tasks → bash → session 链
    # 若产生循环，pytest 在此文件加载时就报错了
    assert ShellTaskRegistry is not None
    assert Session is not None
    assert BTBash is not None


def test_btb_session_property():
    """BTBash._shell_tasks 和 ._emitter 属性正确返回 None（无 session）。"""
    class MockSettings:
        def is_command_allowed(self, tool, cmd): return True
        denied_commands = {}
        allowed_commands = {}

    bash = BTBash(workdir="/tmp", settings=MockSettings(), session=None)
    assert bash._session is None
    assert bash._shell_tasks is None
    assert bash._emitter is None
