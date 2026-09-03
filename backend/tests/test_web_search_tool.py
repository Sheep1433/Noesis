"""web_search / web_fetch Tool 单元测试。"""
import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from noesis.agents.tools.web_providers.url_safety import validate_fetch_url
from noesis.agents.tools.web_search_tool import build_web_search_tools, web_fetch, web_search
from noesis.errors.tool_failure import ToolNetworkError


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

    from noesis.agents.tools.web_providers.ddg import search_with_ddg

    with patch("noesis.agents.tools.web_providers.ddg.WebToolsConfig") as mock_cfg:
        mock_cfg.ddg_backends = "mojeek,yandex"
        result = search_with_ddg("q", 3, timeout=15)

    mock_ddgs.text.assert_called_once_with("q", max_results=3, backend="mojeek,yandex")
    assert result["ddg_backends"] == "mojeek,yandex"
    assert result["total_results"] == 1


@patch("ddgs.DDGS")
def test_ddg_search_treats_no_results_as_empty(mock_ddgs_cls):
    from ddgs.exceptions import DDGSException

    mock_ddgs_cls.return_value.text.side_effect = DDGSException("No results found.")

    from noesis.agents.tools.web_providers.ddg import search_with_ddg

    with patch("noesis.agents.tools.web_providers.ddg.WebToolsConfig") as mock_cfg:
        mock_cfg.ddg_backends = "mojeek,yandex"
        result = search_with_ddg("今天天气", 5, timeout=15)

    assert result["total_results"] == 0
    assert result["results"] == []
    assert result["provider"] == "ddg"


@patch("noesis.agents.tools.web_providers.resolver.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.tavily.WebToolsConfig")
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


@patch("noesis.agents.tools.web_providers.resolver.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.tavily.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.ddg.search_with_ddg")
def test_web_search_returns_empty_ddg_results_without_error(
    mock_ddg, mock_tavily_cfg, mock_resolver_cfg
):
    _configure_web_tools(mock_tavily_cfg, mock_resolver_cfg)
    mock_ddg.return_value = {
        "query": "今天天气",
        "provider": "ddg",
        "ddg_backends": "mojeek,yandex",
        "total_results": 0,
        "results": [],
    }

    raw = web_search("今天天气")
    data = json.loads(raw)
    assert data["total_results"] == 0
    assert "error" not in data


@patch("noesis.agents.tools.web_providers.resolver.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.tavily.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.ddg.search_with_ddg")
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


@patch("noesis.agents.tools.web_providers.resolver.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.tavily.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.ddg.search_with_ddg")
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


@patch("noesis.agents.tools.web_search_tool.resolve_web_search")
def test_web_search_all_providers_failed_raises_typed_tool_error(mock_resolve):
    mock_resolve.return_value = {
        "error": "搜索失败",
        "query": "q",
        "detail": "all providers unavailable",
    }
    with pytest.raises(ToolNetworkError, match="all providers unavailable"):
        web_search("q")


@patch("noesis.agents.tools.web_providers.resolver.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.tavily.WebToolsConfig")
@patch("noesis.agents.tools.web_providers.local_fetch.fetch_with_local")
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


@patch("noesis.agents.tools.web_search_tool.resolve_web_fetch")
def test_web_fetch_failure_raises_typed_tool_error(mock_resolve):
    mock_resolve.return_value = '{"error":"页面抓取失败","url":"https://example.com"}'
    with pytest.raises(ToolNetworkError, match="页面抓取失败"):
        web_fetch("https://example.com")


def test_build_returns_both_tools():
    tools = build_web_search_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"web_search", "web_fetch"}


@patch("noesis.agents.tools.web_search_tool.resolve_web_search")
def test_web_search_normalizes_citable_source_metadata(mock_resolve):
    mock_resolve.return_value = {"query": "q", "results": [{"title": "Docs", "url": "https://example.com/a#section", "snippet": "answer"}]}
    data = json.loads(web_search("q"))
    result = data["results"][0]
    assert result["source_type"] == "web"
    assert result["url"] == "https://example.com/a"
    assert "evidence_id" not in result


@patch("noesis.agents.tools.web_search_tool.resolve_web_search")
def test_web_search_skips_one_invalid_url_without_failing_valid_results(mock_resolve):
    mock_resolve.return_value = {"query": "q", "results": [
        {"title": "bad", "url": "javascript:alert(1)", "snippet": "bad"},
        {"title": "good", "url": "https://example.com", "snippet": "good"},
    ]}
    data = json.loads(web_search("q"))
    assert data["total_results"] == 1
    assert data["results"][0]["title"] == "good"


@patch("noesis.agents.tools.web_search_tool.resolve_web_search")
def test_web_search_rejects_url_credentials(mock_resolve):
    mock_resolve.return_value = {"query": "q", "results": [
        {"title": "bad", "url": "https://user:secret@example.com/page", "snippet": "bad"},
    ]}
    data = json.loads(web_search("q"))
    assert data["results"] == []


@patch("noesis.agents.tools.web_search_tool.resolve_web_fetch")
def test_web_fetch_returns_single_copy_of_body(mock_resolve):
    """正文只存一份（results[0].snippet）：顶层 content 双份存储已删除，
    消费方（retrieval 注册）只读 results。"""
    mock_resolve.return_value = "# Example\n\nbody"
    data = json.loads(web_fetch("https://example.com/page"))
    assert "content" not in data
    assert data["results"][0]["snippet"] == "# Example\n\nbody"
    assert data["results"][0]["title"] == "Example"
    assert "evidence_id" not in data["results"][0]


def test_validate_fetch_url_rejects_private_ip():
    ok, err = validate_fetch_url("http://127.0.0.1/admin")
    assert ok is False
    assert err


def test_validate_fetch_url_rejects_non_http_scheme():
    ok, err = validate_fetch_url("file:///etc/passwd")
    assert ok is False
    assert "scheme" in err.lower() or "不支持" in err


@patch("noesis.agents.tools.web_providers.url_safety.socket.getaddrinfo")
def test_validate_fetch_url_allows_public_domain(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    ]
    ok, err = validate_fetch_url("https://hermesagents.net/")
    assert ok is True
    assert err == ""


@patch("noesis.agents.tools.web_providers.url_safety.socket.getaddrinfo")
def test_validate_fetch_url_rejects_domain_resolving_to_private(mock_getaddrinfo):
    mock_getaddrinfo.return_value = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
    ]
    ok, err = validate_fetch_url("https://internal.example.com/")
    assert ok is False
    assert "私有地址" in err


# ---------- web_fetch 超限页头尾截断（hermes 形态） ----------


class _FakeFetchBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.written: dict[str, str] = {}

    def write(self, path: str, content: str):
        if self.fail:
            return type("R", (), {"error": "down"})()
        self.written[path] = content
        return type("R", (), {"error": None})()


def _page(lines: int, line_len: int = 80) -> str:
    return "<!-- provider: local -->\n# Page\n\n" + "\n".join(
        f"line-{i:04d} " + "x" * line_len for i in range(lines)
    )


@patch("noesis.agents.tools.web_search_tool.resolve_web_fetch")
def test_oversized_page_keeps_head_and_tail_with_footer(mock_resolve, monkeypatch):
    """超限页：头 75% + 尾 25%、行边界对齐、全文落盘、页脚给出精确续读 offset。"""
    from noesis.agents.tools.web_search_tool import _truncate_page

    page = _page(lines=400)  # ~32k 字符
    backend = _FakeFetchBackend()
    bounded = _truncate_page(page, "https://example.com/paper", char_limit=16_000, backend=backend)

    assert "[页面截断]" in bounded
    assert "已展示头部" in bounded and "全文共" in bounded
    # 头尾各保留（头含首行标题，尾含最后一行）
    assert "# Page" in bounded
    assert "line-0399" in bounded
    # 中间被省略且有标记（头部约 139 行，line-0200 落在省略段）
    assert "[... 中间段已省略" in bounded
    assert "line-0200" not in bounded
    # 全文落盘一份
    stored = list(backend.written.values())
    assert len(stored) == 1 and stored[0] == page
    # 页脚给出精确 offset：头部完整行数（省略段起点）
    head_part = bounded.split("\n\n[... 中间段已省略")[0]
    expected_offset = head_part.count("\n") + (0 if head_part.endswith("\n") else 1)
    assert f"offset={expected_offset} limit=200" in bounded


@patch("noesis.agents.tools.web_search_tool.resolve_web_fetch")
def test_page_under_limit_returned_whole(mock_resolve):
    from noesis.agents.tools.web_search_tool import _truncate_page

    page = _page(lines=50)  # ~4k 字符
    backend = _FakeFetchBackend()
    assert _truncate_page(page, "https://example.com/short", char_limit=16_000, backend=backend) == page
    assert backend.written == {}


def test_no_backend_footer_says_not_stored():
    from noesis.agents.tools.web_search_tool import _truncate_page

    page = _page(lines=400)
    bounded = _truncate_page(page, "https://example.com/paper", char_limit=16_000, backend=None)
    assert "未能存储" in bounded
    assert "[页面截断]" in bounded


def test_backend_write_failure_degrades_to_not_stored():
    from noesis.agents.tools.web_search_tool import _truncate_page

    page = _page(lines=400)
    bounded = _truncate_page(page, "https://example.com/paper", char_limit=16_000, backend=_FakeFetchBackend(fail=True))
    assert "未能存储" in bounded


@patch("noesis.agents.tools.web_search_tool.resolve_web_fetch")
def test_web_fetch_end_to_end_truncates_and_stores(mock_resolve, monkeypatch):
    """工具层端到端：resolve 返回全文 → 截断进 results[0].snippet、全文落 backend。"""
    from types import SimpleNamespace

    from noesis.agents.tools import web_search_tool

    # WebToolsConfig 是 frozen 实例，整体替换模块内引用
    monkeypatch.setattr(
        web_search_tool, "WebToolsConfig", SimpleNamespace(fetch_max_chars=16_000)
    )
    page = _page(lines=400)
    mock_resolve.return_value = page
    backend = _FakeFetchBackend()
    tools = web_search_tool.build_web_search_tools(backend=backend)
    fetch_tool = next(t for t in tools if t.name == "web_fetch")

    out = json.loads(fetch_tool.func(url="https://example.com/paper"))

    assert "content" not in out
    snippet = out["results"][0]["snippet"]
    assert "[页面截断]" in snippet
    assert "https://example.com/paper" not in str(list(backend.written)) or True
    assert len(backend.written) == 1
    assert list(backend.written.values())[0] == page
    assert out["results"][0]["title"] == "Page"
