"""评测专用 Langfuse 配置加载与隔离测试。"""

from __future__ import annotations

import os
from unittest.mock import patch

from domain.observability import langfuse as lf
from evals.langfuse_env import EvalLangfuseSettings, eval_langfuse_run


def test_eval_langfuse_run_restores_process_env(monkeypatch):
    settings = EvalLangfuseSettings(
        tracing_enabled=True,
        public_key="pk-eval",
        secret_key="sk-eval",
        base_url="http://lf-eval.test",
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-main")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-main")

    with patch("evals.langfuse_env.load_eval_langfuse_settings", return_value=settings):
        with eval_langfuse_run(line="agent", tag="t1", session_id="sess-1") as active:
            assert active is True
            assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-eval"
            assert lf.langfuse_tracing_enabled() is True
            meta = lf.eval_langfuse_metadata()
            assert meta["eval_line"] == "agent"
            assert meta["eval_tag"] == "t1"
            assert meta["source"] == "noesis-eval"

    assert os.environ.get("LANGFUSE_PUBLIC_KEY") == "pk-main"


def test_eval_langfuse_run_noop_without_settings():
    with patch("evals.langfuse_env.load_eval_langfuse_settings", return_value=None):
        with eval_langfuse_run(line="case", tag="t", session_id="s") as active:
            assert active is False
