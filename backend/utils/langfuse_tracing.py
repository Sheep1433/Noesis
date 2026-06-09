"""Langfuse + LangChain RunnableConfig 合并（可选 CallbackHandler）。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional

from utils.log_util import logger

# 入口 merge_langfuse_runnable_config + langfuse_workflow_context 写入，下游只读
_lf_trace_context: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "lf_trace_context", default=None
)
_lf_session_id: ContextVar[Optional[str]] = ContextVar("lf_session_id", default=None)


def normalize_langfuse_trace_id(raw: Optional[str]) -> Optional[str]:
    """
    Langfuse trace_id 须为 32 位小写 hex（W3C trace id 格式）。
    会话 UUID（含连字符）在此转为去连字符形式；其它自定义 id 原样保留。
    """
    if not raw:
        return None
    s = str(raw).strip()
    compact = s.replace("-", "").lower()
    if len(compact) == 32 and all(c in "0123456789abcdef" for c in compact):
        return compact
    return s


def sync_langfuse_env_from_app_config() -> None:
    """
    将 pydantic 中的 Langfuse 配置同步到进程环境，供 Langfuse SDK 读取。
    在每个 uvicorn worker 进程启动时调用一次即可。
    """
    from config.env import LangfuseConfig

    if not LangfuseConfig.langfuse_tracing_enabled:
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
        return

    os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
    if LangfuseConfig.langfuse_secret_key:
        os.environ.setdefault(
            "LANGFUSE_SECRET_KEY", LangfuseConfig.langfuse_secret_key
        )
    if LangfuseConfig.langfuse_public_key:
        os.environ.setdefault(
            "LANGFUSE_PUBLIC_KEY", LangfuseConfig.langfuse_public_key
        )
    if LangfuseConfig.langfuse_base_url:
        os.environ.setdefault(
            "LANGFUSE_BASE_URL", LangfuseConfig.langfuse_base_url
        )


def merge_langfuse_runnable_config(
    base: Dict[str, Any],
    *,
    langfuse_session_id: Optional[str],
    qa_type: Optional[str],
    enabled: bool,
    langfuse_trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """返回合并了 Langfuse callbacks/metadata 的新 RunnableConfig（不修改入参 base）。"""
    out = dict(base)
    patch = _langfuse_config_patch(
        langfuse_session_id=langfuse_session_id,
        qa_type=qa_type,
        enabled=enabled,
        langfuse_trace_id=langfuse_trace_id,
    )
    if not patch:
        return out

    add_cb = patch.get("callbacks")
    if add_cb:
        existing = out.get("callbacks")
        merged_cb: List[Any] = []
        if existing:
            merged_cb.extend(existing if isinstance(existing, list) else [existing])
        merged_cb.extend(add_cb if isinstance(add_cb, list) else [add_cb])
        out["callbacks"] = merged_cb

    meta = dict(out.get("metadata") or {})
    meta.update(patch.get("metadata") or {})
    if meta:
        out["metadata"] = meta
    return out


def _langfuse_config_patch(
    *,
    langfuse_session_id: Optional[str],
    qa_type: Optional[str],
    enabled: bool,
    langfuse_trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not enabled or not langfuse_session_id:
        return {}
    normalized_trace_id = normalize_langfuse_trace_id(langfuse_trace_id)
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("Langfuse 追踪已开启但无法导入 langfuse.langchain.CallbackHandler")
        return {}
    try:
        trace_context = None
        if normalized_trace_id:
            trace_context = {"trace_id": normalized_trace_id}
        handler = CallbackHandler(trace_context=trace_context)
    except Exception:
        logger.warning("Langfuse CallbackHandler 初始化失败，跳过本次链路追踪", exc_info=True)
        return {}
    metadata: Dict[str, str] = {"langfuse_session_id": str(langfuse_session_id)}
    if qa_type:
        metadata["qa_type"] = str(qa_type)
    if normalized_trace_id:
        metadata["langfuse_trace_id"] = normalized_trace_id
    return {"callbacks": [handler], "metadata": metadata}


def langfuse_session_id_from_config(
    config: Optional[Dict[str, Any]],
) -> Optional[str]:
    """从 RunnableConfig.metadata 读取 Langfuse session_id。"""
    if not config:
        return None
    meta = config.get("metadata") or {}
    sid = meta.get("langfuse_session_id")
    return str(sid) if sid else None


def langfuse_trace_context_from_config(
    config: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    """从 RunnableConfig.metadata 提取 Langfuse trace_context（供 native span 挂到同一 trace）。"""
    if not config:
        return None
    meta = config.get("metadata") or {}
    trace_id = meta.get("langfuse_trace_id")
    if trace_id:
        return {"trace_id": str(trace_id)}
    return None


@contextmanager
def langfuse_workflow_context(
    run_config: Optional[Dict[str, Any]],
) -> Iterator[None]:
    """
    从 run_config.metadata 读取 session/trace id，一次注入全链路复用。
    配合 merge_langfuse_runnable_config 在 workflow 入口调用即可。
    """
    if not run_config:
        yield
        return

    from config.env import LangfuseConfig

    if not LangfuseConfig.langfuse_tracing_enabled:
        yield
        return

    session_id = langfuse_session_id_from_config(run_config)
    trace_context = langfuse_trace_context_from_config(run_config)
    meta = run_config.get("metadata") or {}
    qa_type = meta.get("qa_type")

    if not session_id:
        yield
        return

    tok_trace = _lf_trace_context.set(trace_context)
    tok_session = _lf_session_id.set(session_id)
    try:
        from langfuse import propagate_attributes

        propagate_meta: Optional[Dict[str, str]] = None
        if qa_type:
            propagate_meta = {"qa_type": str(qa_type)}
        with propagate_attributes(session_id=str(session_id), metadata=propagate_meta):
            yield
    except Exception:
        logger.warning("Langfuse workflow 上下文传播失败，降级继续", exc_info=True)
        yield
    finally:
        _lf_trace_context.reset(tok_trace)
        _lf_session_id.reset(tok_session)


def capture_langfuse_trace_id(run_config: Dict[str, Any]) -> Optional[str]:
    """从 run_config 中的 CallbackHandler 读取最近一次 trace_id（兜底）。"""
    for cb in run_config.get("callbacks") or []:
        tid = getattr(cb, "last_trace_id", None)
        if tid:
            return str(tid)
    meta = run_config.get("metadata") or {}
    tid = meta.get("langfuse_trace_id")
    return str(tid) if tid else None


def hits_to_langfuse_payload(hits: List[Any]) -> List[Dict[str, Any]]:
    """将 KbSearchHit 列表序列化为 Langfuse retrieval output。"""
    out: List[Dict[str, Any]] = []
    for h in hits:
        out.append(
            {
                "id": getattr(h, "id", None),
                "score": getattr(h, "score", None),
                "file_name": getattr(h, "file_name", None),
                "content": getattr(h, "content", None),
            }
        )
    return out


@contextmanager
def langfuse_retrieval_observation(
    *,
    name: str,
    input_data: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None,
) -> Iterator[Any]:
    """
    可选 Langfuse retrieval span；trace/session id 从 langfuse_workflow_context 自动读取。
    """
    if enabled is None:
        from config.env import LangfuseConfig

        enabled = LangfuseConfig.langfuse_tracing_enabled
    if not enabled:
        yield None
        return

    trace_context = _lf_trace_context.get()
    session_id = _lf_session_id.get()

    try:
        from langfuse import get_client

        langfuse = get_client()
        with langfuse.start_as_current_observation(
            name=name,
            as_type="retrieval",
            input=input_data or {},
            trace_context=trace_context,
        ) as span:
            if span is not None and session_id:
                span.update_trace(session_id=str(session_id))
            yield span
    except Exception:
        logger.warning("Langfuse retrieval span 失败，降级继续", exc_info=True)
        yield None
