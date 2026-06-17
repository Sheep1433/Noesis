"""stream_failure_notice 错误脱敏。"""
from domain.chat.streaming.failure_notice import is_internal_infrastructure_error, sanitize_user_facing_error


def test_sanitize_internal_docker_error() -> None:
    raw = "[INTERNAL_ERROR] Docker image ubuntu:latest not found. Please pull it first: docker pull ubuntu:latest"
    assert is_internal_infrastructure_error(raw)
    assert sanitize_user_facing_error(raw) == "环境不可用"


def test_sanitize_strips_tool_error_prefix_and_classifies() -> None:
    assert sanitize_user_facing_error("Tool error: connection refused") == "连接失败"
