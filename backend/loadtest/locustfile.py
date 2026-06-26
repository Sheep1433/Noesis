"""
Noesis 深度研究（DEEP_RESEARCH_QA）Locust 性能测试。

前置：后端已启动，MySQL / LLM / sandbox-runner 可用。

安装与运行::

    cd backend
    uv sync --extra loadtest
    uv run locust -f loadtest/locustfile.py --host=http://127.0.0.1:8089

无 UI 压测（1 并发）::

    uv run locust -f loadtest/locustfile.py \\
      --host=http://127.0.0.1:8089 \\
      --headless -u 1 -r 1 --run-time 15m --only-summary

可选参数（均为 Locust CLI，无需环境变量）::

    --username admin          登录用户（默认 admin）
    --password 123456         登录密码（默认 123456）
    --stream-timeout 600      SSE 读超时秒数（默认 600）
    --dataset PATH            使用 jsonl 评测集；省略则用内置 smoke query
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from locust import HttpUser, between, events, task
from locust.exception import StopUser

from loadtest.queries import SMOKE_QUERIES, load_dataset_queries, pick_query
from loadtest.sse_client import consume_sse_stream

QA_TYPE = "DEEP_RESEARCH_QA"
_QUERY_POOL: list[str] = list(SMOKE_QUERIES)


@events.init_command_line_parser.add_listener
def _add_cli_args(parser) -> None:
    parser.add_argument("--username", default="admin", help="登录用户名")
    parser.add_argument("--password", default="123456", help="登录密码")
    parser.add_argument(
        "--stream-timeout",
        type=int,
        default=600,
        help="SSE 读超时秒数",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="dataset.jsonl 路径；省略则使用内置 smoke query",
    )


@events.test_start.add_listener
def _init_query_pool(environment, **_kwargs) -> None:
    global _QUERY_POOL
    dataset = getattr(environment.parsed_options, "dataset", None)
    if dataset:
        _QUERY_POOL = load_dataset_queries(Path(dataset))
    else:
        _QUERY_POOL = list(SMOKE_QUERIES)


def _login(client, username: str, password: str) -> str:
    with client.post(
        "/api/user/login",
        data={"username": username, "password": password},
        name="/api/user/login",
        catch_response=True,
    ) as response:
        if response.status_code != 200:
            response.failure(f"login HTTP {response.status_code}")
            raise StopUser(f"login failed: HTTP {response.status_code}")

        payload = response.json()
        token = (payload.get("data") or {}).get("token")
        if not token:
            response.failure("login response missing token")
            raise StopUser("login failed: missing token")
        response.success()
        return str(token)


class DeepResearchUser(HttpUser):
    """模拟已登录用户发起深度研究 SSE 流式请求。"""

    wait_time = between(5, 15)

    def on_start(self) -> None:
        opts = self.environment.parsed_options
        self.token = _login(self.client, opts.username, opts.password)
        self.auth_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @task
    def deep_research_stream(self) -> None:
        opts = self.environment.parsed_options
        timeout = float(opts.stream_timeout)
        session_id = str(uuid.uuid4())
        query = pick_query(_QUERY_POOL)
        body = {
            "session_id": session_id,
            "content": query,
            "extra": {"qa_type": QA_TYPE},
        }

        with self.client.post(
            "/api/chat/sessions/stream",
            headers=self.auth_headers,
            json=body,
            stream=True,
            timeout=timeout,
            name="deep_research_stream",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return

            started_at = time.perf_counter()
            deadline = started_at + timeout
            metrics = consume_sse_stream(
                response.iter_lines(decode_unicode=True),
                started_at=started_at,
                deadline=deadline,
            )

            context = self.context()
            if metrics.ttft_ms is not None:
                events.request.fire(
                    request_type="SSE",
                    name="deep_research_ttft",
                    response_time=metrics.ttft_ms,
                    response_length=0,
                    exception=None,
                    context=context,
                )

            events.request.fire(
                request_type="SSE",
                name="deep_research_tool_calls",
                response_time=float(metrics.tool_calls),
                response_length=metrics.bytes_received,
                exception=None,
                context=context,
            )

            if metrics.succeeded:
                response.success()
            else:
                response.failure(metrics.error_message or "stream failed")
