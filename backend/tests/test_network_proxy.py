"""common.network.proxy 环境变量行为。"""

from __future__ import annotations

import os

import httpx
import pytest

from common.network import proxy as network_proxy


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_SSL_VERIFY",
        "SSL_VERIFY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_set_proxy_syncs_upper_and_lower_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("http_proxy", "http://proxy:8080")
    network_proxy.set_proxy()
    assert os.environ.get("HTTP_PROXY") == "http://proxy:8080"
    assert os.environ.get("http_proxy") == "http://proxy:8080"


def test_http_ssl_verify_defaults_true() -> None:
    assert network_proxy.http_ssl_verify() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "FALSE"])
def test_http_ssl_verify_false_when_disabled(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HTTP_SSL_VERIFY", value)
    assert network_proxy.http_ssl_verify() is False


def test_httpx_client_kwargs_respects_ssl_verify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_SSL_VERIFY", "false")
    assert network_proxy.httpx_client_kwargs()["verify"] is False


def test_create_sync_client_passes_through_timeout() -> None:
    client = network_proxy.create_sync_client(timeout=12.5)
    try:
        assert client.timeout.read == 12.5
    finally:
        client.close()


def test_create_async_client_is_async_client() -> None:
    client = network_proxy.create_async_client()
    assert isinstance(client, httpx.AsyncClient)
