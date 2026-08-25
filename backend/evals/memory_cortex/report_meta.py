"""Version and code fingerprint shared by release-grade memory eval reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parent
BACKEND = ROOT.parents[1]
REPO = BACKEND.parent
CORE = BACKEND / "packages/noesis-core/src/noesis"


def effective_config_snapshot() -> dict[str, object]:
    from noesis.config.env import MachineMemoryConfig, ModelConfig

    return {
        "runtime_model": ModelConfig.model_name,
        "runtime_model_type": ModelConfig.model_type,
        "runtime_model_base_url": ModelConfig.model_base_url,
        "extraction_model": MachineMemoryConfig.extraction_model or ModelConfig.model_name,
        "embedding_model_name": ModelConfig.embedding_model_name,
        "embedding_model_base_url": ModelConfig.embedding_model_base_url,
        "chunk_max_tokens": MachineMemoryConfig.chunk_max_tokens,
        "chunk_concurrency": MachineMemoryConfig.chunk_concurrency,
        "chunk_attempts": MachineMemoryConfig.chunk_attempts,
        "chunk_retry_delay_seconds": MachineMemoryConfig.chunk_retry_delay_seconds,
        "embedding_template_version": MachineMemoryConfig.embedding_template_version,
        "collection_name": MachineMemoryConfig.collection_name,
        "retrieval_top_k": MachineMemoryConfig.retrieval_top_k,
        "retrieval_overfetch": MachineMemoryConfig.retrieval_overfetch,
        "retrieval_min_score": MachineMemoryConfig.retrieval_min_score,
        "bulletin_max_tokens": MachineMemoryConfig.bulletin_max_tokens,
        "bulletin_timeout_seconds": MachineMemoryConfig.bulletin_timeout_seconds,
        "deep_query_timeout_seconds": MachineMemoryConfig.deep_query_timeout_seconds,
        "deep_query_max_steps": MachineMemoryConfig.deep_query_max_steps,
        "deep_query_max_spans": MachineMemoryConfig.deep_query_max_spans,
        "deep_query_concurrency": MachineMemoryConfig.deep_query_concurrency,
    }


def _fingerprint_paths() -> list[Path]:
    paths = {
        *ROOT.glob("*.py"),
        *ROOT.glob("*.json"),
        *ROOT.glob("fixtures/*.json"),
        *(CORE / "services/memory").glob("**/*.py"),
        CORE / "repositories/machine_memory_repository.py",
        CORE / "repositories/memory_preference_repository.py",
        CORE / "schemas/memory.py",
        CORE / "storage/postgres/models/memory.py",
        CORE / "storage/migrations/versions/202608220001_machine_memory.py",
        CORE / "storage/migrations/versions/202608240001_reset_unreleased_memory.py",
        CORE / "storage/migrations/versions/202608240002_uuid_user_ids.py",
        CORE / "storage/migrations/versions/202608240003_run_memory_context.py",
        CORE / "agents/memory_runtime.py",
        CORE / "agents/tools/memory_tools.py",
        CORE / "agents/middlewares/late_context.py",
        CORE / "agents/middlewares/memory_bulletin_middleware.py",
        CORE / "agents/middlewares/stack.py",
        CORE / "agents/middlewares/dynamic_context_middleware.py",
        CORE / "agents/common_qa.py",
        CORE / "agents/fault_operation.py",
        CORE / "agents/super_agent.py",
        CORE / "factory.py",
        CORE / "services/run_service.py",
        CORE / "services/qa/service.py",
        CORE / "chat/event_mapping/usage_normalize.py",
        CORE / "chat/event_mapping/langgraph_bridge.py",
        CORE / "config/memory_paths.py",
        CORE / "config/env.py",
        CORE / "config/yaml_config.py",
        BACKEND / "server/api/user_settings_api.py",
        BACKEND / "server/main.py",
        BACKEND.parent / "frontend/src/api/settings.ts",
        BACKEND.parent / "frontend/src/views/settings/sections/MemoryEditorSection.vue",
    }
    return sorted(path for path in paths if path.is_file())


def evaluation_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in _fingerprint_paths():
        digest.update(str(path.relative_to(REPO)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    digest.update(
        json.dumps(
            effective_config_snapshot(), sort_keys=True, separators=(",", ":")
        ).encode()
    )
    return digest.hexdigest()


__all__ = ["effective_config_snapshot", "evaluation_fingerprint"]
