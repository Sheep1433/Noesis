"""MCP 目录、probe 与安全工具元数据 read model。"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from noesis.config.mcp_config import McpJsonConfig, save_user_mcp_json
from noesis.services.mcp_service import McpService, _probe_error_category, clear_mcp_probe_cache


@pytest.fixture(autouse=True)
def settings_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
def users_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import noesis.config.user_data_paths as user_paths

    root = tmp_path / "users"
    monkeypatch.setattr(user_paths, "_USERS_ROOT", root)
    return root


def test_mcp_user_catalog_is_isolated(users_root: Path) -> None:
    save_user_mcp_json("user-a", McpJsonConfig(mcpServers={"only-a": {
        "transport": "streamable_http", "url": "https://a.example/mcp",
    }}))
    save_user_mcp_json("user-b", McpJsonConfig(mcpServers={"only-b": {
        "transport": "streamable_http", "url": "https://b.example/mcp",
    }}))
    assert [item.id for item in McpService.list_servers("user-a", scope="user")] == ["only-a"]
    assert [item.id for item in McpService.list_servers("user-b", scope="user")] == ["only-b"]


@pytest.mark.parametrize(
    ("error", "category"),
    [(RuntimeError("HTTP 401 unauthorized secret=abc"), "authentication"),
     (RuntimeError("request timed out token=abc"), "timeout"),
     (RuntimeError("connection refused Authorization=abc"), "connection")],
)
def test_probe_error_categories_are_stable(error: Exception, category: str) -> None:
    assert _probe_error_category(error) == category


@pytest.mark.asyncio
async def test_probe_timeout_does_not_expose_url_secret(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_mcp_json("u1", McpJsonConfig(mcpServers={"slow": {
        "transport": "streamable_http",
        "url": "https://mcp.example/mcp?api_key=must-not-leak",
        "headers": {"Authorization": "Bearer must-not-leak"},
    }}))

    class FakeClient:
        def __init__(self, _connections):
            pass

        async def get_tools(self):
            return []

    async def timeout(awaitable, **_kwargs):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    monkeypatch.setattr("noesis.services.mcp_service.asyncio.wait_for", timeout)
    result = await McpService.probe_server("u1", "slow", use_cache=False)
    assert result.error_category == "timeout"
    assert "must-not-leak" not in repr(result.model_dump())


@pytest.mark.asyncio
async def test_status_catalog_does_not_probe_when_probe_disabled(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_mcp_json("u1", McpJsonConfig(mcpServers={"catalog": {
        "transport": "streamable_http", "url": "https://mcp.example/mcp",
    }}))

    async def unexpected_probe(*_args, **_kwargs):
        raise AssertionError("catalog status must not perform a remote probe")

    monkeypatch.setattr(McpService, "probe_server", unexpected_probe)
    result = await McpService.list_server_status("u1", probe=False, scope="user")

    assert [(item.id, item.status, item.tool_count) for item in result] == [
        ("catalog", "unknown", 0),
    ]


@pytest.mark.asyncio
async def test_tool_catalog_reuses_successful_probe_metadata(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_mcp_json("u1", McpJsonConfig(mcpServers={"tools": {
        "transport": "streamable_http", "url": "https://mcp.example/mcp",
    }}))
    calls = 0

    class FakeClient:
        def __init__(self, _connections):
            pass

        async def get_tools(self):
            nonlocal calls
            calls += 1
            return [type("Tool", (), {"name": "search", "description": "Search docs"})()]

    clear_mcp_probe_cache()
    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    await McpService.probe_server("u1", "tools", use_cache=False)
    tools = await McpService.list_server_tools("u1", "tools")

    assert calls == 1
    assert [tool.model_dump() for tool in tools] == [
        {"name": "search", "description": "Search docs"},
    ]


@pytest.mark.asyncio
async def test_tool_catalog_returns_only_safe_metadata(users_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_mcp_json("u1", McpJsonConfig(mcpServers={"tools": {
        "transport": "streamable_http", "url": "https://mcp.example/mcp",
        "headers": {"Authorization": "Bearer hidden"},
    }}))

    class FakeClient:
        def __init__(self, _connections):
            pass

        async def get_tools(self):
            return [type("Tool", (), {"name": "search", "description": "Search docs", "headers": {"Authorization": "Bearer hidden"}})()]

    monkeypatch.setattr("langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient)
    tools = await McpService.list_server_tools("u1", "tools")
    assert [tool.model_dump() for tool in tools] == [{"name": "search", "description": "Search docs"}]
    assert "hidden" not in repr(tools)
