# tests/prompts_lib/test_base.py
import pytest
from ccserver.prompts_lib.base import PromptLib

def test_base_build_system_raises():
    lib = PromptLib()
    with pytest.raises(NotImplementedError):
        lib.build_system(session=None, model="test", language="zh")

def test_base_build_user_message_default():
    lib = PromptLib()
    result = lib.build_user_message("hello", session=None, context={})
    assert result == [{"type": "text", "text": "hello"}]

def test_base_build_compact_messages_default():
    lib = PromptLib()
    result = lib.build_compact_messages("summary text", "transcripts/abc.json")
    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert "summary text" in result[0]["content"]
    assert "transcripts/abc.json" in result[0]["content"]
    assert result[1]["role"] == "assistant"
