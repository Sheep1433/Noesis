"""用户自定义对话模型服务的核心行为测试：加密、掩码、key 三态、解析优先级。"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import base64

import httpx
import pytest

from noesis.errors.exceptions import ConflictException, NotFoundException, ServiceException
from noesis.llm.runtime_snapshot import RuntimeModelSnapshot
from noesis.services.user_llm_service import UserLLMService


# ---------- 测试替身 ----------


class _ProviderRow:
    def __init__(self, **kw: Any) -> None:
        self.id = kw.get("id", "p1")
        self.slug = kw.get("slug", "p1")
        self.name = kw.get("name", "svc")
        self.api_type = kw.get("api_type", "openai")
        self.base_url = kw.get("base_url", "https://api.example.com/v1")
        self.api_key_cipher = kw.get("api_key_cipher")
        self.api_key_suffix = kw.get("api_key_suffix")
        self.enabled = kw.get("enabled", True)


class _ModelRow:
    def __init__(self, **kw: Any) -> None:
        self.id = kw.get("id", "e1")
        self.provider_id = kw.get("provider_id", "p1")
        self.model_id = kw.get("model_id", "my-model")
        self.label = kw.get("label", "我的模型")
        self.temperature = kw.get("temperature")
        self.context_window = kw.get("context_window", 0)


class _ExecResult:
    def __init__(self, rows: List[Any]) -> None:
        self._rows = rows

    def first(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Optional[Any]:
        return self._rows[0] if self._rows else None


class _Session:
    """按表名路由的极简 db 替身。"""

    def __init__(
        self,
        providers: List[Tuple[Any, Any]] = (),
        models: List[Tuple[Any, Any]] = (),
    ) -> None:
        self.providers = list(providers)
        self.models = list(models)
        self.added: List[Any] = []
        self.committed = False

    async def execute(self, statement: Any) -> _ExecResult:
        sql = str(statement)
        if "user_llm_models" in sql:
            return _ExecResult(list(self.models))
        if "user_llm_providers" in sql:
            return _ExecResult([self.providers[0][0] if self.providers else None])
        return _ExecResult([])

    def add(self, item: Any) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed = True


# ---------- 加密与掩码 ----------


def test_encrypt_roundtrip_and_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"y"*32).decode())
    from noesis.config.secrets import SecretCipher

    cipher = SecretCipher()
    token = "sk-test-1234"
    enc = cipher.encrypt(token)
    assert cipher.decrypt(enc) == token
    assert "sk-test" not in enc
    from noesis.services.user_llm_service import _decrypt_api_key

    assert _decrypt_api_key(f"enc:{enc}") == token


def test_provider_view_never_leaks_plain_key() -> None:
    view = UserLLMService._provider_view(
        _ProviderRow(api_key_cipher="enc:xxx", api_key_suffix="1234")
    )
    assert view["has_key"] is True
    assert view["api_key_masked"] == "***1234"
    assert "enc:xxx" not in str(view)


# ---------- key 三态 ----------


@pytest.mark.asyncio
async def test_update_provider_key_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"z"*32).decode())
    provider = _ProviderRow(api_key_cipher="enc:old", api_key_suffix="old!")
    db = _Session(providers=[(provider, None)])

    # keep：不动密文
    await UserLLMService.update_provider(db, user_id=1, provider_id="p1", name="n", api_key_action="keep")
    assert provider.api_key_cipher == "enc:old"

    # clear：清空
    await UserLLMService.update_provider(db, user_id=1, provider_id="p1", api_key_action="clear")
    assert provider.api_key_cipher is None
    assert provider.api_key_suffix is None

    # replace：写入新密文（enc: 前缀 + 后缀）
    await UserLLMService.update_provider(
        db, user_id=1, provider_id="p1", api_key="sk-new-9999", api_key_action="replace"
    )
    assert provider.api_key_cipher.startswith("enc:")
    assert provider.api_key_suffix == "9999"

    # replace 空值拒绝
    with pytest.raises(Exception):
        await UserLLMService.update_provider(
            db, user_id=1, provider_id="p1", api_key=" ", api_key_action="replace"
        )


@pytest.mark.asyncio
async def test_missing_provider_raises_domain_not_found() -> None:
    with pytest.raises(NotFoundException) as exc_info:
        await UserLLMService._get_provider(_Session(), user_id=1, provider_id="missing")
    assert exc_info.value.message == "模型服务不存在"


@pytest.mark.asyncio
async def test_missing_model_raises_domain_not_found() -> None:
    with pytest.raises(NotFoundException) as exc_info:
        await UserLLMService._get_model(_Session(), user_id=1, entry_id="missing")
    assert exc_info.value.message == "模型条目不存在"


# ---------- 解析为运行时快照 ----------


@pytest.mark.asyncio
async def test_resolve_runtime_snapshots_decrypts_into_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"a"*32).decode())
    from noesis.config.secrets import SecretCipher

    plain = "sk-live-7777"
    cipher = f"enc:{SecretCipher().encrypt(plain)}"
    provider = _ProviderRow(id="p9", api_key_cipher=cipher, api_key_suffix="7777")
    model = _ModelRow(model_id="glm-custom", label="GLM 自定义", context_window=131072)
    db = _Session(models=[(model, provider)])

    snapshots = await UserLLMService.resolve_runtime_snapshots(db, user_id=1, model_id="glm-custom")
    assert len(snapshots) == 1
    snap: RuntimeModelSnapshot = snapshots[0]
    assert snap.id == "glm-custom"
    assert snap.api_key == plain  # 明文只在内存快照里
    assert snap.context_window == 131072
    assert snap.label == "GLM 自定义"


@pytest.mark.asyncio
async def test_resolve_missing_model_returns_empty() -> None:
    db = _Session(models=[])
    snapshots = await UserLLMService.resolve_runtime_snapshots(db, user_id=1, model_id="nope")
    assert snapshots == []


# ---------- 目录合并视图 ----------


def test_public_model_rows_shape() -> None:
    """复合身份：id = provider_slug/model_id；不同 Provider 同名不冲突。"""
    rows = [
        {
            "model_id": "m1", "label": "M1", "api_type": "openai",
            "context_window": 64000, "provider_slug": "my-deepseek",
            "provider_id": "p-uuid-0001",
        },
    ]
    out = UserLLMService.public_model_rows(rows)
    assert out == [
        {
            "id": "my-deepseek/m1",
            "label": "M1",
            "provider": "my-deepseek",
            "model_type": "openai",
            "is_default": False,
            "supports_vision": False,
            "context_window": 64000,
            "custom": True,
        }
    ]


@pytest.mark.asyncio
async def test_same_model_id_allowed_across_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """同名模型分属不同 Provider 合法（复合身份根治冲突）。"""
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"c"*32).decode())
    provider = _ProviderRow(id="p1", slug="svc-a")
    db = _Session(providers=[(provider, None)])
    entry = await UserLLMService.create_model(
        db, user_id=1, provider_id="p1", model_id="deepseek-chat", label="chat",
    )
    assert entry["model_id"] == "deepseek-chat"
    # 内置目录同名也不再冲突：复合身份下两者选择器 id 不同
    assert db.committed


# ---------- 加密未配置时拒绝写入 ----------


@pytest.mark.asyncio
async def test_encrypt_refuses_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
    with pytest.raises(Exception):
        await UserLLMService.create_provider(
            _Session(), user_id=1, name="x", api_type="openai",
            base_url="https://x.example", api_key="sk-plain",
        )


@pytest.mark.asyncio
async def test_discover_provider_models_reads_openai_models_without_persisting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"d"*32).decode())
    from noesis.config.secrets import SecretCipher

    plain = "sk-discovery-secret"
    provider = _ProviderRow(
        id="p-discovery",
        base_url="https://api.example.com/v1/chat/completions",
        api_key_cipher=f"enc:{SecretCipher().encrypt(plain)}",
    )
    db = _Session(providers=[(provider, None)])
    request = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"id": "deepseek-chat", "owned_by": "deepseek", "context_length": 128000},
                    {"id": "deepseek-reasoner"},
                    {"id": "deepseek-chat"},
                    request,
                ]
            }

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> _Response:
            request.update({"url": url, "headers": headers})
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    result = await UserLLMService.discover_draft_models(
        db, user_id=1, api_type="openai",
        base_url="https://api.example.com/v1/chat/completions",
        api_key=plain, provider_id="p-discovery",
    )

    assert result["ok"] is True
    assert result["status"] == "discovered"
    assert result["models"] == [
        {
            "model_id": "deepseek-chat",
            "label": "deepseek-chat",
            "owned_by": "deepseek",
            "context_window": 128000,
            "context_source": "provider",
        },
        {
            "model_id": "deepseek-reasoner",
            "label": "deepseek-reasoner",
            "owned_by": None,
            "context_window": 0,
            "context_source": "unknown",
        },
    ]
    assert request["url"] == "https://api.example.com/v1/models"
    assert request["headers"]["Authorization"] == f"Bearer {plain}"
    assert plain not in str(result)
    assert db.added == []


@pytest.mark.asyncio
async def test_discover_provider_models_reports_unsupported_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"e"*32).decode())
    from noesis.config.secrets import SecretCipher

    provider = _ProviderRow(
        api_key_cipher=f"enc:{SecretCipher().encrypt('sk-test')}",
    )
    db = _Session(providers=[(provider, None)])

    class _Response:
        status_code = 404

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, *args: object, **kwargs: object) -> _Response:
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    result = await UserLLMService.discover_draft_models(
        db, user_id=1, api_type="openai",
        base_url="https://api.example.com/v1",
        api_key="sk-test", provider_id="p1",
    )

    assert result["ok"] is False
    assert result["status"] == "unsupported"
    assert result["provider_reachable"] is True
    assert result["discovery_supported"] is False


@pytest.mark.asyncio
async def test_discover_draft_falls_back_to_stored_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编辑已保存 Provider 且 Key 留空 → 回退已存加密 Key，不强制重输。"""
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"f"*32).decode())
    from noesis.config.secrets import SecretCipher

    plain = "sk-stored"
    provider = _ProviderRow(
        id="p-fallback",
        base_url="https://api.example.com/v1",
        api_key_cipher=f"enc:{SecretCipher().encrypt(plain)}",
    )
    db = _Session(providers=[(provider, None)])
    request = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"data": [{"id": "m1"}]}

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str, *, headers: dict[str, str]) -> _Response:
            request.update({"headers": headers})
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    result = await UserLLMService.discover_draft_models(
        db, user_id=1, api_type="openai",
        base_url="https://api.example.com/v1",
        api_key="", provider_id="p-fallback",
    )

    assert result["ok"] is True
    assert request["headers"]["Authorization"] == f"Bearer {plain}"


@pytest.mark.asyncio
async def test_discover_draft_without_key_reports_missing_key() -> None:
    """未保存、未填 Key 的草案 → missing_key，不发网络请求。"""
    result = await UserLLMService.discover_draft_models(
        _Session(), user_id=1, api_type="openai",
        base_url="https://api.example.com/v1",
    )

    assert result["ok"] is False
    assert result["status"] == "missing_key"


# ---------- Provider ID（slug） ----------

@pytest.mark.asyncio
async def test_create_provider_rejects_invalid_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"a"*32).decode())
    with pytest.raises(ServiceException) as exc_info:
        await UserLLMService.create_provider(
            _Session(), user_id=1, name="svc", slug="My Provider!",
            api_type="openai", base_url="https://api.example.com/v1",
            api_key="sk-x",
        )
    assert "Provider ID" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_create_provider_rejects_duplicate_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"b"*32).decode())
    # _Session.execute 对 providers 查询恒返回第一行 → 模拟 slug 已被占用
    provider = _ProviderRow(id="p-existing", slug="my-gateway")
    db = _Session(providers=[(provider, None)])
    with pytest.raises(ConflictException) as exc_info:
        await UserLLMService.create_provider(
            db, user_id=1, name="svc", slug="my-gateway",
            api_type="openai", base_url="https://api.example.com/v1",
            api_key="sk-x",
        )
    assert "已被使用" in str(exc_info.value.message)
    assert db.added == []


@pytest.mark.asyncio
async def test_resolve_runtime_snapshots_composite_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """复合身份「slug/model_id」解析：id 保持复合（选择器身份），wire_name 为线上名。"""
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"g"*32).decode())
    from noesis.config.secrets import SecretCipher

    provider = _ProviderRow(
        id="p-zen", slug="my-zen",
        api_key_cipher=f"enc:{SecretCipher().encrypt('public')}",
    )
    model = _ModelRow(model_id="hy3-free", label="HY3", context_window=200000)
    db = _Session(models=[(model, provider)])

    snapshots = await UserLLMService.resolve_runtime_snapshots(
        db, user_id=1, model_id="my-zen/hy3-free"
    )
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.id == "my-zen/hy3-free"
    assert snap.wire_name == "hy3-free"
    assert snap.api_key == "public"


@pytest.mark.asyncio
async def test_resolve_runtime_snapshots_bare_id_legacy_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """无斜杠裸 id 走 legacy 查找（历史会话的 model_id 兼容）。"""
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"h"*32).decode())
    from noesis.config.secrets import SecretCipher

    provider = _ProviderRow(
        id="p-legacy", slug="old-svc",
        api_key_cipher=f"enc:{SecretCipher().encrypt('sk-old')}",
    )
    model = _ModelRow(model_id="glm-custom", label="GLM", context_window=131072)
    db = _Session(models=[(model, provider)])

    snapshots = await UserLLMService.resolve_runtime_snapshots(
        db, user_id=1, model_id="glm-custom"
    )
    assert len(snapshots) == 1
    assert snapshots[0].wire_name == "glm-custom"
    assert snapshots[0].id == "glm-custom"
