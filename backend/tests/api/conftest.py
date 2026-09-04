"""接口测试 fixtures：本地起后端服务后，用真实 HTTP 调接口。

前置：
1. ``cd backend && uv run app.py`` 启动服务（需 PostgreSQL / noesis_langgraph DB / Qdrant / 有效 MODEL_API_KEY）。
2. 按层选择运行：
   - 快速轮（不触 LLM，纯接口/CRUD，~1 分钟）：``uv run pytest tests/api -m 'integration and not llm'``
   - 全量（含真实 LLM 用例，受网关限流影响耗时可达半小时）：``uv run pytest tests/api -m integration``

认证模型为 cookie session + CSRF（非 Bearer）：登录后从 ``data.csrf_token`` 取令牌、
``Set-Cookie: noesis_session`` 由 httpx cookie jar 自动保管，后续写操作带 ``X-CSRF-Token`` 头。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field

import httpx
import pytest

from evals.loadtest.sse_client import SseStreamMetrics, consume_sse_stream

# 默认指向本地后端；统一使用 demo 账号 test/123456（Alembic 种子账号，
# 用户确认该账号即测试专用，测试数据落在其名下属预期行为）。
DEFAULT_BASE_URL = "http://127.0.0.1:8089"
DEFAULT_USERNAME = "test"
DEFAULT_PASSWORD = "123456"

# SSE 单次消费安全上限（秒），防止真实 LLM 异常时挂死测试
SSE_DEADLINE_SECONDS = 300


@pytest.fixture
def gateway_skip():
    """免费网关 5 次退避重试耗尽后 run 以 error 终态收尾（降级文案「生成失败」）。

    属环境依赖而非接口回归：命中时 skip，避免限流时段整套误报。
    """

    def _check(message: str | None) -> None:
        text = message or ""
        if "生成失败" in text or "服务暂时不可用" in text:
            pytest.skip(f"网关生成失败（环境依赖）: {text}")

    return _check


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("NOESIS_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


@pytest.fixture(scope="session")
def auth_client(base_url: str) -> httpx.Client:
    """登录一次，返回带 cookie + CSRF 头的同步 httpx.Client。

    session 级：整轮测试复用同一会话，避免每个用例重复登录。
    """
    client = httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(connect=5.0, read=600.0, write=10.0, pool=10.0),
    )
    try:
        resp = client.post(
            "/api/auth/login",
            data={
                "username": os.environ.get("NOESIS_TEST_USER", DEFAULT_USERNAME),
                "password": os.environ.get("NOESIS_TEST_PASSWORD", DEFAULT_PASSWORD),
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data") or {}
        csrf_token = data.get("csrf_token")
        if not csrf_token:
            pytest.fail(f"登录响应缺少 data.csrf_token: {payload}")
        # noesis_session cookie 已由 httpx cookie jar 自动保存
        client.headers["X-CSRF-Token"] = csrf_token
        yield client
    finally:
        client.close()


def _consume_run_stream(
    client: httpx.Client,
    run_id: str,
    *,
    after_sequence: int = 0,
    deadline_seconds: float = SSE_DEADLINE_SECONDS,
) -> SseStreamMetrics:
    """订阅 ``/api/chat/runs/{run_id}/stream``，复用 evals.loadtest.sse_client 解析。

    返回 :class:`SseStreamMetrics`（``succeeded`` / ``finish_reason`` / ``event_counts`` 等）。
    """
    deadline = time.perf_counter() + deadline_seconds
    with client.stream(
        "GET",
        f"/api/chat/runs/{run_id}/stream",
        params={"after_sequence": after_sequence},
    ) as response:
        response.raise_for_status()
        return consume_sse_stream(
            response.iter_lines(),
            deadline=deadline,
        )


@pytest.fixture
def consume_run_stream():
    """注入 :func:`_consume_run_stream`，免去跨包 import。"""
    return _consume_run_stream


def _create_session(
    client: httpx.Client,
    *,
    title: str,
    qa_type: str | None = None,
    extra: dict | None = None,
) -> str:
    body: dict = {"title": title}
    session_extra = dict(extra or {})
    if qa_type:
        session_extra["qa_type"] = qa_type
    if session_extra:
        body["extra"] = session_extra
    resp = client.post("/api/chat/sessions", json=body)
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _create_run(client: httpx.Client, *, session_id: str, content: str, qa_type: str) -> dict:
    resp = client.post(
        "/api/chat/runs",
        json={
            "session_id": session_id,
            "content": content,
            "client_request_id": uuid.uuid4().hex,
            "extra": {"qa_type": qa_type},
        },
    )
    resp.raise_for_status()
    return resp.json()["data"]


@pytest.fixture
def create_session(auth_client):
    """返回绑定到 auth_client 的建会话辅助函数。"""
    return lambda **kwargs: _create_session(auth_client, **kwargs)


@pytest.fixture
def create_run(auth_client):
    """返回绑定到 auth_client 的建 run 辅助函数。"""
    return lambda **kwargs: _create_run(auth_client, **kwargs)


@pytest.fixture
def citation_kb_collection(auth_client):
    """创建一份带 versioned identity 的临时 KB，结束后精确删除。"""
    collection_name = f"citation-live-{uuid.uuid4().hex[:8]}"
    response = auth_client.post(
        "/api/knowledge_base/collections",
        json={"name": collection_name, "vector_dimension": 1024},
    )
    response.raise_for_status()
    try:
        response = auth_client.post(
            f"/api/knowledge_base/collections/{collection_name}/upload",
            files={
                "file": (
                    "登录验证码规则.md",
                    "# 登录验证码规则\n\n登录验证码发送后五分钟内有效，超过五分钟必须重新获取。",
                    "text/markdown",
                )
            },
        )
        response.raise_for_status()
        assert response.json()["data"]["shards_created"] > 0
        yield collection_name
    finally:
        response = auth_client.delete(
            f"/api/knowledge_base/collections/{collection_name}"
        )
        response.raise_for_status()


@dataclass
class StreamEvents:
    """``collect_run_stream`` 的结果：保留全部事件载荷，便于按工具名/输出断言。"""

    events: list[tuple[str | None, dict | str]] = field(default_factory=list)
    finish_reason: str | None = None
    done: bool = False
    error: str | None = None

    @property
    def text(self) -> str:
        """拼接所有 text-delta 的 text_delta。"""
        return "".join(
            p.get("text_delta", "")
            for name, p in self.events
            if name == "text-delta" and isinstance(p, dict)
        )

    @property
    def reasoning(self) -> str:
        return "".join(
            p.get("text_delta", "")
            for name, p in self.events
            if name == "reasoning-delta" and isinstance(p, dict)
        )

    @property
    def tool_inputs(self) -> list[dict]:
        """``tool-input-available`` 事件载荷（含 name / input / tool_call_id）。"""
        return [p for name, p in self.events if name == "tool-input-available" and isinstance(p, dict)]

    @property
    def tool_outputs(self) -> list[dict]:
        """``tool-output-available`` 事件载荷（含 output / status / tool_call_id）。"""
        return [p for name, p in self.events if name == "tool-output-available" and isinstance(p, dict)]

    @property
    def tool_names(self) -> list[str]:
        return [p.get("name") for p in self.tool_inputs if p.get("name")]

    def output_for_tool(self, name: str) -> dict | None:
        """按工具名取首个 ``tool-output-available``（经 tool_call_id 关联）。"""
        wanted_ids = {p.get("tool_call_id") for p in self.tool_inputs if p.get("name") == name}
        for out in self.tool_outputs:
            if out.get("tool_call_id") in wanted_ids:
                return out
        return None

    @property
    def succeeded(self) -> bool:
        return self.done and self.finish_reason == "stop" and self.error is None


def _collect_run_stream(
    client: httpx.Client,
    run_id: str,
    *,
    after_sequence: int = 0,
    deadline_seconds: float = SSE_DEADLINE_SECONDS,
) -> StreamEvents:
    """订阅 SSE，保留全部事件载荷（不像 ``_consume_run_stream`` 只回计数）。"""
    result = StreamEvents()
    deadline = time.perf_counter() + deadline_seconds
    buffer = ""

    def handle(event_name: str | None, data_line: str) -> bool:
        if data_line == "[DONE]":
            result.done = True
            return True
        try:
            payload = json.loads(data_line)
        except json.JSONDecodeError:
            return False
        event_type = event_name or str(payload.get("type") or "")
        result.events.append((event_type, payload))
        if event_type == "finish":
            result.finish_reason = str(payload.get("finish_reason") or "")
            if result.finish_reason != "stop":
                result.error = str(payload.get("error") or result.finish_reason)
        elif event_type == "run.finished":
            # run 流的终态帧（unify-run-delivery 后唯一流终止事件，随后 [DONE]）
            result.finish_reason = str(payload.get("finish_reason") or "")
            if payload.get("status") != "completed" or result.finish_reason != "stop":
                result.error = (
                    str(payload.get("error") or result.finish_reason or "run failed")
                )
        elif event_type == "error":
            result.error = str(payload.get("error") or payload.get("message") or "stream error")
        elif event_type == "abort":
            result.error = str(payload.get("content") or "aborted")
        return False

    with client.stream(
        "GET",
        f"/api/chat/runs/{run_id}/stream",
        params={"after_sequence": after_sequence},
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines():
            if time.perf_counter() >= deadline:
                result.error = "client timeout while reading SSE"
                break
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
            if line == "":
                if buffer:
                    name, data_line = _parse_sse_frame(buffer)
                    buffer = ""
                    if data_line is not None and handle(name, data_line):
                        break
                continue
            buffer = f"{buffer}\n{line}" if buffer else line
    return result


def _parse_sse_frame(frame: str) -> tuple[str | None, str | None]:
    event_name: str | None = None
    data_line: str | None = None
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_line = line[len("data:"):].strip()
    return event_name, data_line


@pytest.fixture
def collect_run_stream():
    """注入 :func:`_collect_run_stream`，用于需检查工具名/输出的事件级断言。"""
    return _collect_run_stream
