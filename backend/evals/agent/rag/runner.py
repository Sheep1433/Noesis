"""Run common QA through the production HTTP API（HTTP 版被测驱动）。

与 web 前端完全同一条生产链路：登录 → 建会话 → 创建 run → 消费 run 事件流
→ 读 DB 权威 assistant 消息。会话/消息/工具调用全部由 server 持久化，
`--eval-user` 账号在前端天然可见；评测脚本只做 HTTP 编排与采集，
不落库、不驱动 Agent、不碰产品内部接口。

产物只做采集不做判分：质量指标全部由 ERB 官方判分脚本产出
（见 to_erb.py 导出格式与 evals/README 的官方判分步骤）。

前置：backend server 已运行（scripts/run.sh dev；默认 127.0.0.1:8089）。
评测账号可用环境变量覆盖：NOESIS_EVAL_USERNAME / NOESIS_EVAL_PASSWORD /
NOESIS_EVAL_SERVER_URL。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

import re

import httpx

DEFAULT_SERVER = os.environ.get("NOESIS_EVAL_SERVER_URL", "http://127.0.0.1:8089")
EVAL_USERNAME = os.environ.get("NOESIS_EVAL_USERNAME", "test")
EVAL_PASSWORD = os.environ.get("NOESIS_EVAL_PASSWORD", "123456")
_TERMINAL_EVENT_TYPES = {"run-completed", "run-error", "run-aborted", "run-stopped"}
# 引用契约（agents/prompts/citations.py）：[citation:标签](kb:集合/文件名)
_KB_REF_RE = re.compile(r"\(kb:([^)]+)\)")

# 登录态进程级缓存：一次评测跑 N 题只登录一次
_http: httpx.AsyncClient | None = None
_csrf: str = ""


async def _get_http() -> httpx.AsyncClient:
    global _http, _csrf
    if _http is not None and not _http.is_closed:
        return _http
    _http = httpx.AsyncClient(base_url=DEFAULT_SERVER, timeout=httpx.Timeout(60))
    resp = await _http.post(
        "/api/auth/login",
        data={"username": EVAL_USERNAME, "password": EVAL_PASSWORD},
    )
    body = resp.json()
    if resp.status_code != 200 or body.get("code") not in (200, 0):
        raise RuntimeError(f"评测账号登录失败: {str(body)[:200]}")
    _csrf = str((body.get("data") or {}).get("csrf_token") or "")
    return _http


def _csrf_headers() -> dict[str, str]:
    return {"X-CSRF-Token": _csrf} if _csrf else {}


async def _close_http() -> None:
    global _http
    if _http is not None and not _http.is_closed:
        await _http.aclose()
    _http = None


def _parts_text(content: Any) -> str:
    """multipart 消息 → 纯文本（text parts 顺序拼接）。"""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return content
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts") or []
    return "\n".join(
        str(p.get("content") or "")
        for p in parts
        if isinstance(p, dict) and p.get("type") == "text"
    )


def _parts_tools(content: Any) -> list[dict[str, Any]]:
    """multipart 消息 → 工具调用列表（name/input/output），供 to_erb 提取文档。"""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return []
    if not isinstance(content, dict):
        return []
    tools = []
    for p in content.get("parts") or []:
        if isinstance(p, dict) and p.get("type") == "tool":
            tools.append({
                "name": str(p.get("name") or "unknown"),
                "input": p.get("input"),
                "output": str(p.get("output") or ""),
            })
    return tools


async def run_agentic_rag_sample(
    sample: dict[str, Any],
    *,
    time_budget_seconds: int = 180,
    model_id: str | None = None,
    eval_user: str = "test",
) -> dict[str, Any]:
    query = str(sample.get("query") or "").strip()
    if not query:
        raise ValueError("Agentic RAG sample requires query")
    sample_id = str(sample.get("id") or uuid.uuid4().hex[:12])
    started = time.perf_counter()

    http = await _get_http()

    # ① 建会话（title 取题面前 80 字，前端列表直接可读）
    resp = await http.post(
        "/api/chat/sessions",
        json={"title": query[:80], "extra": {"qa_type": "common", "eval_line": "agent-rag"}},
        headers=_csrf_headers(),
    )
    body = resp.json()
    if resp.status_code != 200 or body.get("code") not in (200, 0):
        raise RuntimeError(f"创建会话失败: {str(body)[:200]}")
    session_id = str(body["data"]["id"])

    # ② 创建 run（生产 dispatcher 启动；kb/web 参数与进程内版同语义）
    record: dict[str, Any] = {
        "sample_id": sample_id,
        "completed": False,
        "error": None,
        "final_text": "",
        "tool_stats": {},
        "tool_outputs": [],
        "kb_refs": [],
        "_pending": {},
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": 0,
        "session_id": session_id,
    }
    usage: dict[str, Any] = {}
    try:
        resp = await http.post(
            "/api/chat/runs",
            json={
                "session_id": session_id,
                "content": query,
                "client_request_id": f"eval-{sample_id}-{uuid.uuid4().hex[:8]}",
                "extra": {
                    "qa_type": "COMMON_QA",
                    "model_id": model_id,
                    "kb_collections": [c for c in (sample.get("collection_names") or []) if c],
                    "kb_search_enabled": True,
                    "web_search_enabled": False,
                },
            },
            headers=_csrf_headers(),
        )
        body = resp.json()
        if resp.status_code != 200 or body.get("code") not in (200, 0):
            raise RuntimeError(f"创建 run 失败: {str(body)[:200]}")
        run_id = str(body["data"]["run_id"])

        # ③ 消费 run 事件流（SSE；终态事件后服务端关闭连接）
        async def consume() -> None:
            async with http.stream(
                "GET", f"/api/chat/runs/{run_id}/stream", params={"after_sequence": 0}
            ) as stream:
                data_payload: dict[str, Any] | None = None
                async for line in stream.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            data_payload = json.loads(line[5:].strip())
                        except ValueError:
                            data_payload = None
                        continue
                    if line.strip() or data_payload is None:
                        continue
                    ptype = str(data_payload.get("type") or "")
                    if ptype == "tool-input-available":
                        record["_pending"][str(data_payload.get("tool_call_id") or "")] = (
                            data_payload.get("name"), data_payload.get("input"))
                    elif ptype == "tool-output-available":
                        tid = str(data_payload.get("tool_call_id") or "")
                        name, tool_input = record["_pending"].pop(
                            tid, (data_payload.get("name"), None))
                        resolved = str(name or "unknown")
                        record["tool_stats"][resolved] = record["tool_stats"].get(resolved, 0) + 1
                        record["tool_outputs"].append({
                            "name": resolved,
                            "input": tool_input,
                            "output": str(data_payload.get("output") or ""),
                        })
                    elif ptype == "run-completed":
                        usage = dict(data_payload.get("usage") or {})
                    elif ptype in _TERMINAL_EVENT_TYPES:
                        if data_payload.get("message"):
                            record["error"] = str(data_payload["message"])
                        return
                    data_payload = None

        await asyncio.wait_for(consume(), timeout=time_budget_seconds)
    except asyncio.TimeoutError:
        record["completed"] = False
        record["error"] = f"timeout after {time_budget_seconds}s"
    except RuntimeError:
        record["completed"] = False
        raise
    except Exception as exc:  # noqa: BLE001
        record["completed"] = False
        record["error"] = f"http driver failure: {exc!r}"

    # ④ 读 DB 权威 assistant 消息（终态；不从流式文本重拼）。
    # assistant 终态落库可能在流关闭后短暂未提交：轮询直到有非空正文。
    if not record["error"]:
        last_error = None
        for _attempt in range(10):
            try:
                resp = await http.get(f"/api/chat/sessions/{session_id}/messages")
                body = resp.json()
                data = body.get("data") or {}
                messages = data.get("messages") if isinstance(data, dict) else data
                messages = messages or []
                assistant = [m for m in messages if m.get("role") == "assistant"]
                if assistant:
                    last = assistant[-1]
                    text = _parts_text(last.get("content"))
                    if text.strip():
                        record["final_text"] = text
                        tools = _parts_tools(last.get("content"))
                        if tools:
                            record["tool_outputs"] = tools
                            stats: dict[str, int] = {}
                            for t in tools:
                                stats[t["name"]] = stats.get(t["name"], 0) + 1
                            record["tool_stats"] = stats
                        extra = last.get("extra") or {}
                        if isinstance(extra, dict) and extra.get("usage"):
                            usage = {**usage, **(extra.get("usage") or {})}
                        # document_ids：回答引用标记里的 KB 文件名（保留集合前缀）
                        record["kb_refs"] = sorted({
                            m.group(1).strip()
                            for m in _KB_REF_RE.finditer(record["final_text"])
                        })
                        last_error = None
                        break
                last_error = "assistant 消息尚无正文"
            except Exception as exc:  # noqa: BLE001
                last_error = f"读 assistant 消息失败: {exc!r}"
            await asyncio.sleep(1)
        if last_error:
            record["error"] = str(last_error)

    record["completed"] = (
        not record["error"] and bool(record["final_text"].strip())
    )
    record["input_tokens"] = int(usage.get("input_tokens") or 0)
    record["output_tokens"] = int(usage.get("output_tokens") or 0)
    record["latency_ms"] = int((time.perf_counter() - started) * 1000)
    record.pop("_pending", None)
    return record
