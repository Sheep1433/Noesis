"""LangGraph PostgreSQL checkpointer 配置测试。"""

from config.checkpointer import checkpoint_connection_url


def test_checkpoint_uses_dedicated_postgresql_database() -> None:
    url = checkpoint_connection_url()
    assert url.startswith("postgresql://")
    assert url.endswith("/noesis_langgraph")
