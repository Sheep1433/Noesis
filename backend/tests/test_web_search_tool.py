"""web_search / web_fetch Tool 单元测试。"""
import json
import socket
from unittest.mock import MagicMock, patch

from agent.tools.web_providers.url_safety import validate_fetch_url
from agent.tools.web_search_tool import build_web_search_tools, web_fetch, web_search


def _configure_web_tools(*mocks, **overrides) -> None:
    for mock_cfg in mocks:
        mock_cfg.max_search_results = overrides.get("max_search_results", 8)
        mock_cfg.fetch_max_chars = overrides.get("fetch_max_chars", 4096)
        mock_cfg.fetch_timeout_seconds = overrides.get("fetch_timeout_seconds", 30)
        mock_cfg.ddg_backends = overrides.get("ddg_backends", "mojeek,yandex")
        mock_cfg.tavily_api_key = overrides.get("tavily_api_key", "")


@patch("ddgs.DDGS")
def test_ddg_search_uses_configured_backends(mock_ddgs_cls):
    mock_ddgs = mock_ddgs_cls.return_value
    mock_ddgs.text.return_value = [
        {"title": "T", "href": "https://t.com", "body": "snippet"},
    ]

    from agent.tools.web_providers.ddg import search_with_ddg

    with patch("agent.tools.web_providers.ddg.WebToolsConfig") as mock_cfg:
        mock_cfg.ddg_backends = "mojeek,yandex"
        result = search_with_ddg("q", 3, timeout=15)

    mock_ddgs.text.assert_called_once_with("q", max_results=3, backend="mojeek,yandex")
    assert result["ddg_backends"] == "mojeek,yandex"
    assert result["total_results"] == 1


@patch("agent.tools.web_providers.resolver.WebToolsConfig")
@patch("agent.tools.web_providers.tavily.WebToolsConfig")
@patch("tavily.TavilyClient")
def test_web_search_uses_tavily_when_key_present(
    mock_client_cls, mock_tavily_cfg, mock_resolver_cfg
):
    _configure_web_tools(mock_tavily_cfg, mock_resolver_cfg, tavily_api_key="tvly-test")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search.return_value = {
        "results": [
            {"title": "A", "url": "https://a.com", "content": "snippet a"},
        ]
    }

    raw = web_search("ai agents", limit=5)
    data = json.loads(raw)
    assert data["provider"] == "tavily"
    assert data["total_results"] == 1
    assert data["results"][0]["url"] == "https://a.com"
    mock_client.search.assert_called_once()


@patch("agent.tools.web_providers.resolver.WebToolsConfig")
@patch("agent.tools.web_providers.tavily.WebToolsConfig")
@patch("agent.tools.web_providers.ddg.search_with_ddg")
def test_web_search_falls_back_to_ddg_without_key(
    mock_ddg, mock_tavily_cfg, mock_resolver_cfg
):
    _configure_web_tools(mock_tavily_cfg, mock_resolver_cfg)
    mock_ddg.return_value = {
        "query": "test",
        "provider": "ddg",
        "total_results": 1,
        "results": [{"title": "B", "url": "https://b.com", "snippet": "body"}],
    }

    raw = web_search("test query")
    data = json.loads(raw)
    assert data["provider"] == "ddg"
    mock_ddg.assert_called_once()


@patch("agent.tools.web_providers.resolver.WebToolsConfig")
@patch("agent.tools.web_providers.tavily.WebToolsConfig")
@patch("agent.tools.web_providers.ddg.search_with_ddg")
@patch("tavily.TavilyClient")
def test_web_search_falls_back_when_tavily_fails(
    mock_client_cls, mock_ddg, mock_tavily_cfg, mock_resolver_cfg
):
    _configure_web_tools(mock_tavily_cfg, mock_resolver_cfg, tavily_api_key="tvly-test")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.search.side_effect = RuntimeError("rate limit")
    mock_ddg.return_value = {
        "query": "q",
        "provider": "ddg",
        "total_results": 1,
        "results": [{"title": "C", "url": "https://c.com", "snippet": "s"}],
    }

    raw = web_search("q")
    data = json.loads(raw)
    assert data["provider"] == "ddg"
    mock_ddg.assert_called_once()


@patch("agent.tools.web_providers.resolver.WebToolsConfig")
@patch("agent.tools.web_providers.tavily.WebToolsConfig")
@patch("agent.tools.web_providers.local_fetch.fetch_with_local")
def test_web_fetch_falls_back_to_local_without_key(
    mock_local, mock_tavily_cfg, mock_resolver_cfg
):
    _configure_web_tools(mock_tavily_cfg, mock_resolver_cfg)
    mock_local.return_value = {
        "provider": "local",
        "url": "https://example.com",
        "markdown": "<!-- provider: local -->\n# Example\n\nbody",
    }

    result = web_fetch("https://example.com")
    assert "provider: local" in result
    mock_local.assert_called_once()


def test_build_returns_both_tools():
    tools = build_web_search_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"web_search", "web_fetch"}


def test_validate_fetch_url_rejects_private_ip():
    ok, err = validate_fetch_url("http://127.0.0.1/admin")
    assert ok is False
    assert err


def test_validate_fetch_url_rejects_non_http_scheme():
    ok, err = validate_fetch_url("file:///etc/passwd")
    assert ok is False
    assert "scheme" in err.lower() or "不支持" in err


@patch("agent.tools.web_providers.url_safety.socket.getaddrinfo")
def test_validate_fetch_url_allows_public_domain(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    ok, err = validate_fetch_url("https://hermesagents.net/")
    assert ok is True
    assert err == ""


@patch("agent.tools.web_providers.url_safety.socket.getaddrinfo")
def test_validate_fetch_url_rejects_domain_resolving_to_private(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
    ]
    ok, err = validate_fetch_url("https://internal.example.com/")
    assert ok is False
    assert "私有地址" in err
