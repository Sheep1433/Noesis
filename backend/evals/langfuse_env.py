"""评测专用 Langfuse 配置（仅读取 evals/.env，不写入主项目 .env）。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

from dotenv import dotenv_values

EVALS_ROOT = Path(__file__).resolve().parent
EVAL_ENV_FILE = EVALS_ROOT / ".env"


@dataclass(frozen=True)
class EvalLangfuseSettings:
    tracing_enabled: bool
    public_key: str
    secret_key: str
    base_url: str


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_eval_langfuse_settings() -> Optional[EvalLangfuseSettings]:
    """从 evals/.env 加载；文件不存在或未配置 key 时返回 None。"""
    if not EVAL_ENV_FILE.is_file():
        return None
    raw = dotenv_values(EVAL_ENV_FILE)
    public_key = str(raw.get("LANGFUSE_PUBLIC_KEY") or "").strip()
    secret_key = str(raw.get("LANGFUSE_SECRET_KEY") or "").strip()
    if not public_key or not secret_key:
        return None
    base_url = str(
        raw.get("LANGFUSE_BASE_URL") or raw.get("LANGFUSE_HOST") or ""
    ).strip()
    enabled = _truthy(raw.get("LANGFUSE_TRACING_ENABLED", "true"))
    return EvalLangfuseSettings(
        tracing_enabled=enabled,
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
    )


@contextmanager
def eval_langfuse_run(
    *,
    line: str,
    tag: str,
    session_id: str,
    trace_id: Optional[str] = None,
) -> Iterator[bool]:
    """
    激活评测 Langfuse 上下文（三条评测线共用同一 evals/.env 项目）。

    仅在 with 块内临时设置 Langfuse SDK 所需环境变量，退出后恢复，不影响主项目。
  """
    from domain.observability.langfuse import activate_eval_langfuse

    settings = load_eval_langfuse_settings()
    if settings is None or not settings.tracing_enabled:
        yield False
        return

    with activate_eval_langfuse(
        settings=settings,
        line=line,
        tag=tag,
        session_id=session_id,
        trace_id=trace_id or session_id,
    ):
        yield True
