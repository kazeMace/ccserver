# tests/prompts_lib/test_cc_reverse.py
import pytest
from unittest.mock import MagicMock
from ccserver.prompts_lib.cc_reverse.v2_1_81.lib import CcReverseV2181


def _make_session():
    session = MagicMock()
    session.project_root = "/tmp/test_project"
    return session


def test_build_system_returns_list():
    lib = CcReverseV2181()
    system = lib.build_system(_make_session(), model="claude-sonnet-4-6", language="简体中文")
    assert isinstance(system, list)
    assert len(system) >= 2
    for item in system:
        assert item.get("type") == "text"
        assert isinstance(item.get("text"), str)
        assert len(item["text"]) > 0


def test_build_system_contains_language():
    lib = CcReverseV2181()
    system = lib.build_system(_make_session(), model="claude-sonnet-4-6", language="English")
    full_text = " ".join(item["text"] for item in system)
    assert "English" in full_text


def test_build_user_message_no_reminders():
    lib = CcReverseV2181()
    result = lib.build_user_message("hello", session=None, context={})
    # 返回 list，包含原始文本
    assert isinstance(result, list)
    assert any(part.get("text") == "hello" for part in result)


def test_build_user_message_with_hook_context():
    # hook_context 是 build_user_message 支持的 reminder 注入方式（is_first=True 时生效）
    lib = CcReverseV2181()
    session = MagicMock()
    session.project_root = MagicMock()
    session.project_root.__truediv__ = lambda self, other: MagicMock(exists=lambda: False)
    session.skills.list_skills.return_value = []
    session.commands.list_commands.return_value = []
    result = lib.build_user_message(
        "hello",
        session=session,
        context={"is_first": True, "hook_context": "hook reminder A"},
    )
    assert isinstance(result, list)
    full = str(result)
    assert "hello" in full
    assert "hook reminder A" in full


def test_build_compact_messages():
    lib = CcReverseV2181()
    msgs = lib.build_compact_messages("summary here", "transcripts/abc.json")
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert "summary here" in msgs[0]["content"]
    assert "transcripts/abc.json" in msgs[0]["content"]
    assert msgs[1]["role"] == "assistant"
