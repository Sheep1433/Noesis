"""用户自定义对话模型服务的核心行为测试：加密、掩码、key 三态、解析优先级。"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import base64

import pytest

from noesis.llm.runtime_snapshot import RuntimeModelSnapshot
from noesis.services.user_llm_service import UserLLMService


# ---------- 测试替身 ----------


class _ProviderRow:
    def __init__(self, **kw: Any) -> None:
        self.id = kw.get("id", "p1")
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
    rows = [
        {"model_id": "m1", "label": "M1", "api_type": "openai", "context_window": 64000},
    ]
    out = UserLLMService.public_model_rows(rows)
    assert out == [
        {
            "id": "m1",
            "label": "M1",
            "model_type": "openai",
            "is_default": False,
            "supports_vision": False,
            "context_window": 64000,
            "custom": True,
        }
    ]


# ---------- 加密未配置时拒绝写入 ----------


@pytest.mark.asyncio
async def test_encrypt_refuses_without_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SETTINGS_ENCRYPTION_KEY", raising=False)
    with pytest.raises(Exception):
        await UserLLMService.create_provider(
            _Session(), user_id=1, name="x", api_type="openai",
            base_url="https://x.example", api_key="sk-plain",
        )
