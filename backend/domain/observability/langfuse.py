"""Langfuse + LangChain RunnableConfig 合并（可选 CallbackHandler）。"""

from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from common.logging import logger

_otel_exporter_direct_http_patched = False

# 入口 merge_langfuse_runnable_config + langfuse_workflow_context 写入，下游只读
_lf_trace_context: ContextVar[Optional[Dict[str, str]]] = ContextVar(
    "lf_trace_context", default=None
)
_lf_session_id: ContextVar[Optional[str]] = ContextVar("lf_session_id", default=None)

_EVAL_LANGFUSE_ENV_KEYS = (
    "LANGFUSE_TRACING_ENABLED",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_HOST",
)


@dataclass(frozen=True)
class _EvalLangfuseActive:
    line: str
    tag: str
    session_id: str
    trace_id: str


_eval_langfuse_active: ContextVar[Optional[_EvalLangfuseActive]] = ContextVar(
    "eval_langfuse_active", default=None
)


def langfuse_tracing_enabled() -> bool:
    """线上 Langfuse 开关；评测 with 块内若激活 evals/.env 则视为开启。"""
    if _eval_langfuse_active.get() is not None:
        return True
    from config.env import LangfuseConfig

    return bool(LangfuseConfig.langfuse_tracing_enabled)


def eval_langfuse_metadata() -> Dict[str, str]:
    active = _eval_langfuse_active.get()
    if not active:
        return {}
    return {
        "source": "noesis-eval",
        "eval_line": active.line,
        "eval_tag": active.tag,
        "eval_session_id": active.session_id,
    }


@contextmanager
def activate_eval_langfuse(
    *,
    settings: Any,
    line: str,
    tag: str,
    session_id: str,
    trace_id: str,
) -> Iterator[None]:
    """
    临时注入评测 Langfuse 凭据（仅 with 块内），供 CallbackHandler / get_client 使用。
    settings 须含 tracing_enabled, public_key, secret_key, base_url 属性。
    """
    if not settings.tracing_enabled:
        yield
        return

    saved_env = {key: os.environ.get(key) for key in _EVAL_LANGFUSE_ENV_KEYS}
    normalized_trace_id = normalize_langfuse_trace_id(trace_id) or trace_id
    active = _EvalLangfuseActive(
        line=line,
        tag=tag,
        session_id=session_id,
        trace_id=normalized_trace_id,
    )
    tok_active = _eval_langfuse_active.set(active)
    tok_trace = _lf_trace_context.set({"trace_id": normalized_trace_id})
    tok_session = _lf_session_id.set(session_id)
    eval_client = None
    try:
        _patch_langfuse_otel_direct_http()
        os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.secret_key
        if settings.base_url:
            os.environ["LANGFUSE_BASE_URL"] = settings.base_url
            os.environ["LANGFUSE_HOST"] = settings.base_url
        # 须先注册 Langfuse 客户端，再创建 CallbackHandler / @observe；
        # 否则 get_client(public_key=...) 会降级为 disabled fake client 且不上报 trace。
        from langfuse import Langfuse

        client_kwargs: Dict[str, Any] = {
            "public_key": settings.public_key,
            "secret_key": settings.secret_key,
            "httpx_client": _langfuse_direct_httpx_client(),
        }
        if settings.base_url:
            client_kwargs["host"] = settings.base_url
        eval_client = Langfuse(**client_kwargs)
        yield
    finally:
        if eval_client is not None:
            try:
                eval_client.flush()
            except Exception:
                logger.warning("评测 Langfuse flush 失败", exc_info=True)
        _eval_langfuse_active.reset(tok_active)
        _lf_trace_context.reset(tok_trace)
        _lf_session_id.reset(tok_session)
        for key in _EVAL_LANGFUSE_ENV_KEYS:
            prev = saved_env.get(key)
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


@contextmanager
def eval_langfuse_observation(
    *,
    name: str,
    input_data: Optional[Dict[str, Any]] = None,
) -> Iterator[Any]:
    """评测 fixture / item 级根 span（压缩线等无 LangChain callback 时使用）。"""
    if not langfuse_tracing_enabled():
        yield None
        return
    trace_context = _lf_trace_context.get()
    session_id = _lf_session_id.get()
    meta = eval_langfuse_metadata()
    try:
        from langfuse import get_client

        client = get_client()
        if _eval_langfuse_active.get() is not None:
            public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
            if public_key:
                client = get_client(public_key=public_key)
        with client.start_as_current_observation(
            name=name,
            as_type="span",
            input={**(input_data or {}), **meta},
            trace_context=trace_context,
        ) as span:
            if span is not None and session_id:
                span.update_trace(session_id=str(session_id), metadata=meta)
            yield span
    except Exception:
        logger.warning("评测 Langfuse observation 失败，降级继续", exc_info=True)
        yield None


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


def _langfuse_direct_httpx_client(**kwargs: Any) -> Any:
    import httpx

    return httpx.Client(trust_env=False, **kwargs)


def _patch_langfuse_otel_direct_http() -> None:
    """
    Langfuse OTEL exporter 默认 requests.Session 会读取系统网络设置（如 macOS 10810）。
    注入 trust_env=False 的 session，与业务侧「全部直连」一致。
    """
    global _otel_exporter_direct_http_patched
    if _otel_exporter_direct_http_patched:
        return

    import requests
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    original_init = OTLPSpanExporter.__init__

    def patched_init(
        self: Any,
        *args: Any,
        session: Any = None,
        **kwargs: Any,
    ) -> None:
        if session is None:
            session = requests.Session()
            session.trust_env = False
        original_init(self, *args, session=session, **kwargs)

    OTLPSpanExporter.__init__ = patched_init  # type: ignore[method-assign]
    _otel_exporter_direct_http_patched = True


def _register_langfuse_client_from_config() -> None:
    from config.env import LangfuseConfig
    from langfuse import Langfuse

    client_kwargs: Dict[str, Any] = {
        "public_key": LangfuseConfig.langfuse_public_key,
        "secret_key": LangfuseConfig.langfuse_secret_key,
        "httpx_client": _langfuse_direct_httpx_client(),
    }
    if LangfuseConfig.langfuse_base_url:
        client_kwargs["host"] = LangfuseConfig.langfuse_base_url
    Langfuse(**client_kwargs)


def sync_langfuse_env_from_app_config() -> None:
    """
    将 pydantic 中的 Langfuse 配置同步到进程环境，供 Langfuse SDK 读取。
    在每个 uvicorn worker 进程启动时调用一次即可。
    """
    from config.env import LangfuseConfig

    if not LangfuseConfig.langfuse_tracing_enabled:
        os.environ["LANGFUSE_TRACING_ENABLED"] = "false"
        return

    _patch_langfuse_otel_direct_http()
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

    if (
        LangfuseConfig.langfuse_public_key
        and LangfuseConfig.langfuse_secret_key
    ):
        _register_langfuse_client_from_config()


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
        handler_kwargs: Dict[str, Any] = {"trace_context": trace_context}
        if _eval_langfuse_active.get() is not None:
            public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
            if public_key:
                handler_kwargs["public_key"] = public_key
        handler = CallbackHandler(**handler_kwargs)
    except Exception:
        logger.warning("Langfuse CallbackHandler 初始化失败，跳过本次链路追踪", exc_info=True)
        return {}
    metadata: Dict[str, str] = {"langfuse_session_id": str(langfuse_session_id)}
    metadata.update(eval_langfuse_metadata())
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

    if not langfuse_tracing_enabled():
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

        propagate_meta: Optional[Dict[str, str]] = dict(eval_langfuse_metadata())
        if qa_type:
            propagate_meta["qa_type"] = str(qa_type)
        if not propagate_meta:
            propagate_meta = None
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
        enabled = langfuse_tracing_enabled()
    if not enabled:
        yield None
        return

    trace_context = _lf_trace_context.get()
    session_id = _lf_session_id.get()

    try:
        from langfuse import get_client

        langfuse = get_client()
        if _eval_langfuse_active.get() is not None:
            public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
            if public_key:
                langfuse = get_client(public_key=public_key)
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
