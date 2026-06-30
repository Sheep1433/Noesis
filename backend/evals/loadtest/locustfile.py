"""
Noesis 超级智能体（SUPER_AGENT_QA）Locust 性能测试。

单用户（admin）、多 session；客户端不设超时，等后端 SSE 自然结束。

前置：后端已启动，MySQL / LLM / sandbox-runner 可用。

安装与运行::

    cd backend
    uv sync --extra loadtest
    uv run locust -f evals/loadtest/locustfile.py --host=http://127.0.0.1:8089

无 UI 压测（1 并发）::

    uv run locust -f evals/loadtest/locustfile.py \\
      --host=http://127.0.0.1:8089 \\
      --headless -u 1 -r 1 --run-time 30m --only-summary

查询集见 ``evals/loadtest/data/queries.jsonl``。
"""

from __future__ import annotations

import time
import uuid

from locust import HttpUser, between, events, task
from locust.exception import StopUser

from evals.loadtest.queries import load_dataset_queries, pick_query
from evals.loadtest.sse_client import consume_sse_stream

QA_TYPE = "SUPER_AGENT_QA"
LOGIN_USERNAME = "admin"
LOGIN_PASSWORD = "123456"
_QUERY_POOL = load_dataset_queries()


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
    """单用户多 session：每个虚拟用户复用 admin 账号，每次请求新建 session。"""

    wait_time = between(5, 15)

    def on_start(self) -> None:
        self.token = _login(self.client, LOGIN_USERNAME, LOGIN_PASSWORD)
        self.auth_headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @task
    def deep_research_stream(self) -> None:
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
            timeout=None,
            name="deep_research_stream",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return

            started_at = time.perf_counter()
            metrics = consume_sse_stream(
                response.iter_lines(decode_unicode=True),
                started_at=started_at,
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
