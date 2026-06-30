"""BrowseComp 官方评测：uv run python -m evals.agent.browsecomp --tag <name>"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage

from evals.agent._agent import run_super_agent
from evals.agent.browsecomp.official import BrowseCompEval, MessageList, SamplerBase, SamplerResponse
from evals.langfuse_env import eval_langfuse_run
from llm import get_llm

SUITE_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = SUITE_ROOT / "results"


class _AgentSampler(SamplerBase):
    def __init__(self, time_budget: int):
        self.time_budget = time_budget

    def __call__(self, message_list: MessageList) -> SamplerResponse:
        prompt = str(message_list[-1].get("content") or "").strip()
        sid = f"browsecomp-{uuid.uuid4().hex[:12]}"
        run = run_super_agent(query=prompt, session_id=sid, user_id="eval-browsecomp", time_budget_seconds=self.time_budget)
        text = run.get("final_text") or ""
        if not text and run.get("error"):
            text = f"Error: {run['error']}"
        return SamplerResponse(text, list(message_list), {"latency_ms": run.get("latency_ms")})


class _GraderSampler(SamplerBase):
    def __call__(self, message_list: MessageList) -> SamplerResponse:
        llm = get_llm()
        text = str(llm.invoke([HumanMessage(content=str(message_list[-1].get("content") or ""))]).content or "")
        return SamplerResponse(text, list(message_list), {})


def _results_dir(tag: str) -> Path:
    return RESULTS_ROOT / tag.replace("/", "_")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="BrowseComp（openai/simple-evals 官方流程）")
    p.add_argument("--tag", required=True)
    p.add_argument("--num-examples", type=int, default=None)
    p.add_argument("--time-budget", type=int, default=600)
    args = p.parse_args(argv)

    num = args.num_examples
    if num is None and os.environ.get("NOESIS_BROWSECOMP_NUM_EXAMPLES"):
        num = int(os.environ["NOESIS_BROWSECOMP_NUM_EXAMPLES"])

    out = _results_dir(args.tag)
    out.mkdir(parents=True, exist_ok=True)

    with eval_langfuse_run(line="agent", tag=args.tag, session_id=f"browsecomp-{args.tag}"):
        t0 = time.perf_counter()
        result = BrowseCompEval(_GraderSampler(), num_examples=num)(_AgentSampler(args.time_budget))
        elapsed = time.perf_counter() - t0

    accuracy = float((result.metrics or {}).get("is_correct") or 0.0)
    summary = {
        "benchmark": "browsecomp",
        "tag": args.tag,
        "metric": "accuracy",
        "accuracy": round(accuracy, 6),
        "num_examples": num,
        "elapsed_seconds": round(elapsed, 2),
        "upstream": "openai/simple-evals BrowseCompEval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "convos.jsonl").open("w", encoding="utf-8") as f:
        for i, convo in enumerate(result.convos or []):
            f.write(json.dumps({"index": i, "convo": convo}, ensure_ascii=False) + "\n")

    print(f"BrowseComp accuracy: {accuracy:.4f}")
    print(f"Results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
