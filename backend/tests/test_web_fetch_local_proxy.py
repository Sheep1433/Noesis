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


def _status_client(monkeypatch, status_code: int):
    """伪造 httpx.Client：返回指定状态码的响应（raise_for_status 抛 HTTPStatusError）。"""

    class _Resp:
        url = "https://example.com/missing.ts"
        headers = {"content-type": "text/plain"}
        text = ""

        def raise_for_status(self):
            request = local_fetch.httpx.Request("GET", self.url)
            response = local_fetch.httpx.Response(status_code, request=request)
            raise local_fetch.httpx.HTTPStatusError(
                f"Client error '{status_code}'", request=request, response=response,
            )

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return _Resp()

    monkeypatch.setattr(local_fetch.httpx, "Client", _Client)


@pytest.mark.parametrize("status_code, fragment", [
    (404, "页面不存在（404）"),
    (410, "页面不存在（410）"),
    (403, "页面拒绝访问（403）"),
    (429, "请求过于频繁（429）"),
    (503, "服务器错误（503）"),
    (418, "请求被拒绝（418）"),
])
def test_fetch_with_local_status_error_carries_status_code(monkeypatch, status_code, fragment):
    """4xx/5xx 以 FetchStatusError 透出（带状态码与可行动文案），不折叠成页面抓取失败。"""
    monkeypatch.delenv("NOESIS_WEB_PROXY", raising=False)
    _status_client(monkeypatch, status_code)
    with pytest.raises(local_fetch.FetchStatusError) as exc_info:
        local_fetch.fetch_with_local("https://example.com/missing.ts", timeout=5)
    assert exc_info.value.status_code == status_code
    assert fragment in str(exc_info.value)
