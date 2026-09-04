"""统一评测产物基建：manifest schema v1、tag 防覆盖、judge 分离校验、人工抽检清单。

四条评测线（kb/agent-rag/compression/agent-memory）共用的落盘约定：
results/<tag>/{manifest.json, raw.json(l), summary.json, summary.md}
"""

from __future__ import annotations

import json
import random
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "noesis-eval-manifest/v1"
MANIFEST_NAME = "manifest.json"
REVIEW_QUEUE_NAME = "manual_review_queue.json"
REVIEW_FRACTION = 0.1


class TagExistsError(FileExistsError):
    """tag 已有评测产物；历史结果不覆盖，换新 tag 再跑。"""


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return "unknown"


def require_judge_separation(subject_model: str | None, judge_model: str | None) -> None:
    """LLM-as-judge 评测线启动前校验：judge 与被测模型必须不同。"""
    if not judge_model:
        raise ValueError("LLM-as-judge 评测线必须显式提供 judge 模型（--judge-model-id）")
    if subject_model and judge_model == subject_model:
        raise ValueError(
            f"judge 模型不得与被测模型相同（均为 {judge_model}）；换一个不同档位的 judge"
        )


def init_results_dir(results_root: Path, tag: str, *, allow_resume: bool = False) -> Path:
    """创建/复用 results/<tag>/。

    已有 manifest 视为历史基线，拒绝覆盖（换新 tag）；allow_resume 是
    续跑例外——断点续跑/--retry-failed 必须复用同 tag，只追加 raw 记录
    不改写历史。
    """
    out_dir = Path(results_root) / tag.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    if (out_dir / MANIFEST_NAME).is_file() and not allow_resume:
        raise TagExistsError(
            f"{out_dir} 已有评测产物（tag 复用会覆盖历史基线）；"
            f"续跑请加 --resume，其余情况换新 tag"
        )
    return out_dir


def build_manifest(
    *,
    eval_line: str,
    tag: str,
    subject_model: str | None = None,
    judge_model: str | None = None,
    embedding_model: str | None = None,
    rerank_model: str | None = None,
    dataset: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    usage: dict[str, int] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """组装 manifest dict；usage 至少含 input_tokens / output_tokens。"""
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "eval_line": eval_line,
        "tag": tag,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha(),
        "models": {
            "subject": subject_model,
            "judge": judge_model,
            "embedding": embedding_model,
            "rerank": rerank_model,
        },
        "dataset": dataset or {},
        "config": config or {},
        "usage": {
            "input_tokens": int((usage or {}).get("input_tokens") or 0),
            "output_tokens": int((usage or {}).get("output_tokens") or 0),
        },
    }
    if notes:
        manifest["notes"] = notes
    return manifest


def write_manifest(results_dir: Path, manifest: dict[str, Any]) -> Path:
    path = Path(results_dir) / MANIFEST_NAME
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_manual_review_queue(
    results_dir: Path,
    records: Sequence[dict[str, Any]],
    *,
    seed: int,
    fraction: float = REVIEW_FRACTION,
) -> Path:
    """固定种子抽 10% 样本供人工抽检校准 judge；不足 1 条时至少抽 1 条。"""
    if not records:
        raise ValueError("records 为空，无法生成抽检清单")
    rng = random.Random(seed)
    count = max(1, round(len(records) * fraction))
    picked = rng.sample(range(len(records)), min(count, len(records)))
    payload = {
        "schema_version": "noesis-eval-review-queue/v1",
        "fraction": fraction,
        "seed": seed,
        "total": len(records),
        "indices": sorted(picked),
        "samples": [records[i] for i in sorted(picked)],
    }
    path = Path(results_dir) / REVIEW_QUEUE_NAME
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def aggregate_usage(runs: Iterable[dict[str, Any]]) -> dict[str, int]:
    """从逐题记录聚合 token 用量（字段缺失按 0 计）。"""
    input_tokens = 0
    output_tokens = 0
    for run in runs:
        input_tokens += int(run.get("input_tokens") or 0)
        output_tokens += int(run.get("output_tokens") or 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}
