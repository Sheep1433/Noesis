"""stream_failure_notice 错误脱敏。"""
from utils.stream_failure_notice import is_internal_infrastructure_error, sanitize_user_facing_error


def test_sanitize_internal_docker_error() -> None:
    raw = "[INTERNAL_ERROR] Docker image ubuntu:latest not found. Please pull it first: docker pull ubuntu:latest"
    assert is_internal_infrastructure_error(raw)
    assert "MCP" in sanitize_user_facing_error(raw)


def test_sanitize_strips_tool_error_prefix() -> None:
    assert sanitize_user_facing_error("Tool error: connection refused") == "connection refused"
