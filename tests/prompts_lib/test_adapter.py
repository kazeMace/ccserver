# tests/prompts_lib/test_adapter.py
import pytest
from ccserver.prompts_lib.adapter import get_lib, register
from ccserver.prompts_lib.base import PromptLib


def test_get_known_lib():
    lib = get_lib("cc_reverse:v2.1.81")
    assert isinstance(lib, PromptLib)


def test_get_unknown_lib_raises():
    with pytest.raises(ValueError, match="未知的 prompt lib"):
        get_lib("nonexistent:v0.0")


def test_register_and_get():
    class MyLib(PromptLib):
        def build_system(self, session, model, language):
            return []

    register("test:v1.0", MyLib())
    lib = get_lib("test:v1.0")
    assert isinstance(lib, MyLib)
