"""从 fixture 的「将被压缩区域」由 LLM 生成事实 recall 题库。

用法（backend/ 下）:
    uv run python -m evals.compression.gen_probes --fixture <id> [--questions 15] [--model-id m]

题库按 transcript 内容 hash 缓存（probes/<fixture>.probes.json 内记 transcript_sha256），
fixture 内容变化后重跑会拒绝复用旧题库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from evals.compression.driver import _approx_token_counter, parse_fixture_messages
from evals.compression.fixture_loader import (
    PROBES_DIR,
    load_fixture,
    load_probes,
)

GEN_PROMPT_VERSION = "gen-probes/v1"


def transcript_sha(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compacted_region(messages: list[dict[str, Any]], *, keep_n: int) -> list[dict[str, Any]]:
    """将被压缩掉的区域 = 除最近 keep_n 条外的全部消息（force 压缩路径）。"""
    return messages[:-keep_n] if keep_n and len(messages) > keep_n else messages


def build_gen_prompt(region_text: str, *, n_questions: int) -> str:
    return f"""你是评测题库生成器。以下是一段将被压缩摘要掉的长会话记录。
请从中提炼 {n_questions} 道事实 recall 题：问题必须是仅凭这段记录才能回答的具体事实
（错误码、配置值、文件路径、决策结论、数字、名称），并给出标准答案。

要求：
- 问题不得依赖会话之外的知识
- 标准答案必须能在记录中逐字或近似找到
- 覆盖记录的不同部分（开头/中部/结尾），不要扎堆

仅输出 JSON 数组，不要其它文字：
[{{"id": "p1", "type": "recall", "question": "...", "reference_answer": "..."}}]

CONVERSATION RECORD（将被压缩的区域）:
{region_text}"""


def parse_probes_response(raw: str, *, n_questions: int) -> list[dict[str, Any]]:
    if not raw or not raw.strip():
        raise ValueError("empty response")
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fence:
        text = fence.group(1).strip()
    arr = re.search(r"\[[\s\S]*\]", text)
    if not arr:
        raise ValueError(f"no JSON array: {raw[:200]!r}")
    probes = json.loads(arr.group(0))
    if not isinstance(probes, list) or not probes:
        raise ValueError("probes empty")
    out = []
    for i, probe in enumerate(probes[:n_questions], 1):
        if not isinstance(probe, dict) or not str(probe.get("question") or "").strip():
            raise ValueError(f"probe {i} missing question")
        out.append({
            "id": str(probe.get("id") or f"p{i}"),
            "type": "recall",
            "question": str(probe["question"]),
            "reference_answer": str(probe.get("reference_answer") or ""),
        })
    return out


def generate_probes(
    fixture_id: str,
    *,
    n_questions: int = 15,
    model_id: str | None = None,
    keep_n: int | None = None,
    model_user: str | None = None,
) -> Path:
    fixture = load_fixture(fixture_id)
    messages = parse_fixture_messages(fixture["messages"])
    if keep_n is None:
        from noesis.config.env import ModelConfig
        keep_n = int(ModelConfig.summarization_messages_to_keep or 4)
    region = compacted_region(fixture["messages"], keep_n=keep_n)

    # 既有题库且 transcript 未变 → 直接复用（缓存语义）；
    # 无 sha 的旧手写题库视为已缓存（fixtures 冻结，编辑 fixture 须手动重新生成）
    sha = transcript_sha(fixture["messages"])
    try:
        existing = load_probes(fixture_id)
        if existing.get("transcript_sha256") in (sha, None):
            print(f"probes 缓存命中（transcript 未变或手写题库）: {fixture_id}")
            return PROBES_DIR / f"{fixture_id}.probes.json"
        print("transcript 已变化，重新生成题库", file=sys.stderr)
    except FileNotFoundError:
        pass

    from noesis.llm import get_llm

    if model_user:
        from evals.bootstrap import bind_user_model_sync
        model_id = bind_user_model_sync(model_user, model_id)

    region_text = "\n\n".join(
        f"[{m.get('type')}] {m.get('content', '')}" for m in region)
    # 截断到 ~60k chars，超长 region 采样首中尾
    if len(region_text) > 60_000:
        head, tail = region_text[:30_000], region_text[-25_000:]
        region_text = f"{head}\n\n[...middle truncated...]\n\n{tail}"

    llm = get_llm(model_id=model_id)
    prompt = build_gen_prompt(region_text, n_questions=n_questions)
    raw = str(llm.invoke(prompt).content or "")
    probes = parse_probes_response(raw, n_questions=n_questions)

    payload = {
        "fixture_id": fixture_id,
        "transcript_sha256": sha,
        "gen_prompt_version": GEN_PROMPT_VERSION,
        "region_messages": len(region),
        "region_tokens": _approx_token_counter(parse_fixture_messages(region)),
        "probes": probes,
    }
    PROBES_DIR.mkdir(parents=True, exist_ok=True)
    out = PROBES_DIR / f"{fixture_id}.probes.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"probes: {len(probes)} 题 → {out}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="为压缩 fixture 生成事实 recall 题库")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--questions", type=int, default=15)
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--keep-n", type=int, default=None,
                        help="压缩保留的最近消息数（默认取配置）")
    parser.add_argument("--model-user", default=None, help="自定义模型归属用户")
    args = parser.parse_args()
    generate_probes(args.fixture, n_questions=args.questions,
                    model_id=args.model_id or None, keep_n=args.keep_n,
                    model_user=args.model_user or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
