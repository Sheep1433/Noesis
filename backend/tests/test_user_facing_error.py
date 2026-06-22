"""stream_failure_notice 错误脱敏。"""
from domain.chat.streaming.failure_notice import (
    is_internal_infrastructure_error,
    sanitize_stream_error,
    sanitize_tool_error,
)


def test_sanitize_stream_internal_docker_error() -> None:
    raw = "[INTERNAL_ERROR] Docker image ubuntu:latest not found. Please pull it first: docker pull ubuntu:latest"
    assert is_internal_infrastructure_error(raw)
    assert sanitize_stream_error(raw) == "环境不可用"


def test_sanitize_stream_connection_refused_not_tool_classifier() -> None:
    assert sanitize_stream_error("connection refused") == "connection refused"


def test_sanitize_tool_error_unknown_for_free_text() -> None:
    assert sanitize_tool_error("HTTP 403 Forbidden in response body") == "执行失败"


def test_sanitize_tool_error_from_explicit_header() -> None:
    raw = "[tool_error category=network_unreachable retryable=true]\nConnectError"
    assert sanitize_tool_error(raw) == "连接失败"
