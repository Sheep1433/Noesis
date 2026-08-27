"""distributed_runs 配置校验：显式模式选择、redis 条件必填、fail-fast（task 1.2）。"""

from __future__ import annotations

import pytest

from noesis.config.env import (
    DistributedRunsSettings,
    EnvSecrets,
    _build_distributed_runs,
)
from noesis.config.yaml_config import AppYamlConfig


def _build(backend: str = "", redis_url: str = "", cluster_id: str = "") -> DistributedRunsSettings:
    secrets = EnvSecrets.model_construct(
        run_bus_backend=backend, redis_url=redis_url, cluster_id=cluster_id
    )
    return _build_distributed_runs(secrets, AppYamlConfig())


def test_memory_backend_requires_explicit_value():
    settings = _build(backend="memory")
    assert settings.backend == "memory"
    # memory 模式写入本地稳定 cluster_id，与 redis 模式同构
    assert settings.cluster_id == "local"
    assert settings.redis_url == ""


def test_backend_value_is_normalized():
    assert _build(backend=" Memory ").backend == "memory"


def test_missing_backend_fails_fast():
    with pytest.raises(ValueError, match="NOESIS_RUN_BUS_BACKEND 必填"):
        _build(backend="")


@pytest.mark.parametrize("value", ["MEMORY2", "none", "inmemory", "kafka"])
def test_unknown_backend_fails_fast(value: str):
    with pytest.raises(ValueError, match="非法值"):
        _build(backend=value)


def test_redis_backend_requires_url_and_cluster_id():
    with pytest.raises(ValueError, match="REDIS_URL 必填"):
        _build(backend="redis", cluster_id="noesis-prod")
    with pytest.raises(ValueError, match="NOESIS_CLUSTER_ID 必填"):
        _build(backend="redis", redis_url="redis://redis:6379/0")


def test_redis_backend_cluster_id_format_validated():
    with pytest.raises(ValueError, match="NOESIS_CLUSTER_ID"):
        _build(
            backend="redis",
            redis_url="redis://redis:6379/0",
            cluster_id="noesis prod",
        )


def test_redis_backend_builds_settings():
    settings = _build(
        backend="redis",
        redis_url="redis://redis:6379/0",
        cluster_id="noesis-prod",
    )
    assert settings.backend == "redis"
    assert settings.cluster_id == "noesis-prod"
    assert settings.queued_scan_interval_seconds > 0
    assert settings.publisher_queue_max_events > 0


def test_yaml_tuning_section_defaults():
    settings = _build(backend="memory")
    assert settings.leader_heartbeat_seconds == 30.0
    assert settings.command_scan_interval_seconds == 5.0
    assert settings.reconciliation_interval_seconds == 30.0
    assert settings.periodic_checkpoint_interval_seconds == 15.0
    assert settings.redis_pool_max_connections == 20
