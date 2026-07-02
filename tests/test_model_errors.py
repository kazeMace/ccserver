# tests/test_model_errors.py
"""tests/test_model_errors.py — provider 无关瞬态异常与判定助手。"""

import httpx

from ccserver.model_engine.errors import TransientLLMError, is_transient, wrap_transient


def test_transient_error_preserves_cause():
    """TransientLLMError 应保留原始异常（__cause__）。"""
    original = ValueError("boom")
    err = TransientLLMError("wrapped", original)
    assert err.__cause__ is original
    assert "wrapped" in str(err)


def test_is_transient_httpx_connect_error():
    """httpx 连接类错误判为瞬态。"""
    exc = httpx.ConnectError("conn refused")
    assert is_transient(exc) is True


def test_is_transient_httpx_remote_protocol_error():
    exc = httpx.RemoteProtocolError("server disconnected")
    assert is_transient(exc) is True


def test_is_transient_status_429():
    """带 status_code=429 的异常判为瞬态。"""
    exc = Exception()
    exc.status_code = 429
    assert is_transient(exc) is True


def test_is_transient_status_503():
    exc = Exception()
    exc.status_code = 503
    assert is_transient(exc) is True


def test_is_transient_status_400_not_transient():
    """4xx（非 429）判为不可重试。"""
    exc = Exception()
    exc.status_code = 400
    assert is_transient(exc) is False


def test_is_transient_plain_value_error_not_transient():
    assert is_transient(ValueError("bad")) is False


def test_is_transient_already_transient():
    """已是 TransientLLMError 直接判 True。"""
    assert is_transient(TransientLLMError("x", ValueError())) is True


def test_wrap_transient_wraps_and_preserves_cause():
    import pytest
    original = httpx.ConnectError("refused")
    with pytest.raises(TransientLLMError) as ei:
        wrap_transient(original, "anthropic create")
    assert ei.value.__cause__ is original
    assert "anthropic create" in str(ei.value)


def test_wrap_transient_reraises_non_transient():
    import pytest
    original = ValueError("bad")
    with pytest.raises(ValueError):
        wrap_transient(original, "x")
