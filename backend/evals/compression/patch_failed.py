"""定向补跑 run 记录里的故障题（completed=False 的回合）并合并回原记录。

背景：评测回合的「未完成」是环境故障（网关限流/超时），不是答错——但记录
口径按 0 分计入，会污染组间对比（实测不压缩组 8/60 回合被压低 17-23 个
百分点）。整组重跑比补 8 题贵一个数量级，本工具只重跑失败题。

用法（backend/ 下）:
    uv run python -m evals.compression.patch_failed <run记录.json> \
        --model-user test --model-id huoshan/glm-5.3-flash \
        --judge-model-user admin --judge-model-id huoshan/deepseek-v4-flash

流程：读旧记录 → 取未完成题 → 重跑该组该题（重新落库/播种，环境与首跑
一致）→ 判卷 → 替换条目、重算 usage → 原地重写记录文件（附 patch 溯源）。
前提：该记录的 arm 为不压缩组（免压缩直跑）；压缩组失败题应整组重跑——
压缩产物是新会话状态，单题补跑无法共享摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_bank(fixture_id: str) -> list[dict[str, Any]]:
    path = Path(__file__).resolve().parent / "probes" / f"{fixture_id}.probes.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc["probes"]


async def _patch(
    payload_path: Path,
    *,
    model_id: str,
    user_uuid: str,
    judge_llm: Any,
) -> dict[str, Any]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    fixture_id = payload["fixture_id"]
    arm = payload["arm"]
    if arm != "uncompacted":
        raise SystemExit(f"仅支持不压缩组记录的补跑（当前 arm={arm}）——压缩组失败题请整组重跑")
    failed = [p["probe_id"] for p in payload["probes"] if not p.get("completed")]
    if not failed:
        print("无未完成题，无需补跑")
        return payload

    from evals.compression.fixture_loader import load_fixture
    from evals.compression.agent_path import run_fixture_arms

    bank = _load_bank(fixture_id)
    probes = [p for p in bank if p["id"] in failed]
    print(f"{fixture_id} [{arm}] 补跑 {len(probes)} 题: {failed}", flush=True)

    fixture = load_fixture(fixture_id)
    flow = await run_fixture_arms(
        fixture, fixture_id, probes, [arm],
        model_id=model_id, user_id=user_uuid,
        eval_run_id=f"patch-{uuid.uuid4().hex[:8]}",
    )
    # flow 的 probe_runs 按入参 probes 顺序排列（不带 probe_id），zip 对齐
    fresh = {probe["id"]: run for probe, run in
             zip(probes, flow["arms"][arm]["probes"])}

    from evals.compression.grader import grade_probe
    patched = 0
    for entry in payload["probes"]:
        pid = entry["probe_id"]
        if pid not in fresh:
            continue
        run = fresh[pid]
        judged = grade_probe(
            probe_question=str(next(p["question"] for p in bank if p["id"] == pid)),
            probe_type=str(next(p["type"] for p in bank if p["id"] == pid)),
            reference_answer=str(next(p["reference_answer"] for p in bank if p["id"] == pid)),
            continuation_text=str(run.get("continuation_text") or ""),
            llm=judge_llm,
        )
        entry.update({
            "continuation_text": run.get("continuation_text"),
            "completed": run.get("completed"),
            "error": run.get("error"),
            "tool_stats": run.get("tool_stats") or {},
            "input_tokens": run.get("input_tokens") or 0,
            "output_tokens": run.get("output_tokens") or 0,
            "recall": judged.get("recall"),
            "judged": judged,
            "patched": True,
        })
        patched += 1
        print(f"  {pid}: completed={entry['completed']} recall={entry['recall']}", flush=True)
    payload["usage"] = {
        "input_tokens": sum(p.get("input_tokens") or 0 for p in payload["probes"]),
        "output_tokens": sum(p.get("output_tokens") or 0 for p in payload["probes"]),
    }
    payload["patch_note"] = f"{len(failed)} 个故障题已补跑重判（patch_failed）"
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已合并重写 {payload_path}（补跑 {patched} 题）", flush=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="补跑 run 记录中的故障题并合并")
    parser.add_argument("payload", type=Path)
    parser.add_argument("--model-user", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--judge-model-user", required=True)
    parser.add_argument("--judge-model-id", required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(BACKEND_DIR))
    from evals.bootstrap import (
        bind_user_model_sync,
        eval_runtime,
        resolve_user_uuid_sync,
    )

    # 绑定顺序与主驱动一致：判卷先绑（构造 LLM 即固定端点），作答后绑——
    # bind_snapshots 的 ContextVar 后绑覆盖先绑，顺序反了作答快照会被顶掉
    judge_snapshot = bind_user_model_sync(args.judge_model_user, args.judge_model_id)
    from noesis.llm import get_llm
    judge_llm = get_llm(model_id=judge_snapshot)
    subject_snapshot_id = bind_user_model_sync(
        args.model_user, args.model_id, include_summarization=True)
    user_uuid = resolve_user_uuid_sync(args.model_user)

    async def _main():
        async with eval_runtime(no_attachments=True):
            await _patch(
                args.payload, model_id=subject_snapshot_id,
                user_uuid=user_uuid, judge_llm=judge_llm)

    asyncio.run(_main())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
