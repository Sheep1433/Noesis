"""local_fetch 抓取链路测试：直连优先、失败走 NOESIS_WEB_PROXY 代理重试。"""

import pytest

from noesis.agents.tools.web_providers import local_fetch


class _FakeResp:
    def __init__(self):
        self.url = "https://example.com/page"
        self.headers = {"content-type": "text/plain"}
        self.text = "raw body"

    def raise_for_status(self):
        return None


def _make_client(monkeypatch, *, direct_ok: bool):
    """伪造 httpx.Client：direct_ok=False 时无代理调用抛连接错误。"""
    calls = []

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            if not direct_ok and "proxy" not in self.kwargs:
                raise local_fetch.httpx.ConnectError("direct blocked")
            return _FakeResp()

    monkeypatch.setattr(local_fetch.httpx, "Client", _Client)
    return calls


def test_fetch_with_local_retries_via_proxy_on_direct_failure(monkeypatch):
    monkeypatch.setenv("NOESIS_WEB_PROXY", "http://127.0.0.1:7897")
    calls = _make_client(monkeypatch, direct_ok=False)
    result = local_fetch.fetch_with_local("https://example.com/page", timeout=5)
    assert result["provider"] == "local"
    assert "raw body" in result["markdown"]
    # 第一次直连失败，第二次带代理重试
    assert len(calls) == 2
    assert "proxy" not in calls[0].kwargs
    assert calls[1].kwargs["proxy"] == "http://127.0.0.1:7897"


def test_fetch_with_local_direct_success_skips_proxy(monkeypatch):
    monkeypatch.setenv("NOESIS_WEB_PROXY", "http://127.0.0.1:7897")
    calls = _make_client(monkeypatch, direct_ok=True)
    result = local_fetch.fetch_with_local("https://example.com/page", timeout=5)
    assert result["provider"] == "local"
    assert len(calls) == 1  # 直连成功，不做代理重试


def test_fetch_with_local_without_proxy_env_raises(monkeypatch):
    monkeypatch.delenv("NOESIS_WEB_PROXY", raising=False)
    calls = _make_client(monkeypatch, direct_ok=False)
    with pytest.raises(RuntimeError, match="页面抓取失败"):
        local_fetch.fetch_with_local("https://example.com/page", timeout=5)
    # 无代理配置时不做第二次尝试
    assert len(calls) == 1
