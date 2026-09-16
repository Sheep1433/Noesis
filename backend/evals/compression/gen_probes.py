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

from evals.compression.fixture_loader import _approx_token_counter, parse_fixture_messages
from evals.compression.fixture_loader import (
    PROBES_DIR,
    load_fixture,
    load_probes,
)

GEN_PROMPT_VERSION = "gen-probes/v2-stratified"

# 分层定义：题目按「摘要应保留到什么程度」分三档，报告按层分别报召回，
# 避免 headline 被微观细节题主导（闭卷天然丢细节，宏观事实应保留）
LAYERS: dict[str, str] = {
    "macro": (
        "宏观：会话整体事实——用户的原始目标与诉求、最终交付了什么、"
        "关键决策及其理由、会话结束时的状态。一份合格摘要必须保留"
    ),
    "meso": (
        "中观：模块/任务级事实——某文件或某模块改了什么、某个 Bug 的根因结论、"
        "某功能的方案取舍与被否选项。一份合格摘要通常应保留"
    ),
    "detail": (
        "微观：精确细节——错误信息原文、具体数值、路径+行号、命令原文、"
        "用户的原话措辞。摘要通常不逐字保留，需要时靠检索原文补回"
    ),
}

# 出题 prompt 的 region 字符预算；超预算时等距采样（硬截断会让题目偏向首尾）
_REGION_PROMPT_CHAR_BUDGET = 150_000


def sample_region_text(region_text: str, *, budget: int = _REGION_PROMPT_CHAR_BUDGET) -> str:
    """超预算的 region 等距采样：等分 k 段、各取头部拼接，覆盖头/中/尾。"""
    if len(region_text) <= budget:
        return region_text
    k = -(-len(region_text) // budget)  # ceil div
    slice_len = -(-len(region_text) // k)
    keep = budget // k
    sampled = []
    for i in range(k):
        part = region_text[i * slice_len:(i + 1) * slice_len]
        if len(part) > keep:
            # 每段取头部；末段取尾部（region 尾 = 最近的待压缩内容，出题价值最高）
            chunk = part[-keep:] if i == k - 1 else part[:keep]
            sampled.append(chunk + "\n[...本段截断...]")
        else:
            sampled.append(part)
    return "\n\n[...段间省略...]\n\n".join(sampled)


def transcript_sha(messages: list[dict[str, Any]]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compacted_region(messages: list[dict[str, Any]], *, keep_n: int) -> list[dict[str, Any]]:
    """将被压缩掉的区域 = 除最近 keep_n 条外的全部消息（force 压缩路径）。"""
    return messages[:-keep_n] if keep_n and len(messages) > keep_n else messages


def parse_layer_split(spec: str) -> dict[str, int]:
    """把 "10:5:5" 解析为 {macro:10, meso:5, detail:5}（段序 = LAYERS 定义序）。"""
    parts = [p.strip() for p in spec.split(":") if p.strip()]
    if len(parts) != len(LAYERS):
        raise ValueError(
            f"layer-split 须为 {len(LAYERS)} 段（{'/'.join(LAYERS)}）: {spec!r}")
    counts: dict[str, int] = {}
    for name, part in zip(LAYERS, parts):
        try:
            n = int(part)
        except ValueError:
            raise ValueError(f"layer-split 段非整数: {part!r}") from None
        if n < 1:
            raise ValueError(f"layer-split 段须 ≥1: {part!r}")
        counts[name] = n
    return counts


def even_layer_split(n_questions: int) -> dict[str, int]:
    """无显式配额时按题量均分三层（余数从首层起每层 +1）。"""
    if n_questions < len(LAYERS):
        raise ValueError(f"题量 {n_questions} 不足以三层各 ≥1")
    base = n_questions // len(LAYERS)
    counts = {name: base for name in LAYERS}
    for name in list(LAYERS)[:n_questions - base * len(LAYERS)]:
        counts[name] += 1
    return counts


def build_gen_prompt(region_text: str, *, layer_counts: dict[str, int]) -> str:
    total = sum(layer_counts.values())
    layer_rules = "\n".join(
        f'- "{name}": {desc}（出 {layer_counts[name]} 题）'
        for name, desc in LAYERS.items())
    return f"""你是评测题库生成器。以下是一段将被压缩摘要掉的长会话记录。
请从中提炼 {total} 道事实 recall 题，按信息粒度分三层出题，并给出标准答案。

三层定义与配额：
{layer_rules}

要求：
- 问题不得依赖会话之外的知识
- 标准答案必须能在记录中逐字或近似找到
- 严格按配额出题，且覆盖记录的不同部分（开头/中部/结尾），不要扎堆
- 每题标注所属 layer（macro / meso / detail）

仅输出 JSON 数组，不要其它文字：
[{{"id": "p1", "type": "recall", "layer": "macro", "question": "...", "reference_answer": "..."}}]

CONVERSATION RECORD（将被压缩的区域）:
{region_text}"""


def parse_probes_response(
    raw: str,
    *,
    layer_counts: dict[str, int],
    require_layers: bool = True,
) -> list[dict[str, Any]]:
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
    valid = []
    for i, probe in enumerate(probes, 1):
        if not isinstance(probe, dict) or not str(probe.get("question") or "").strip():
            raise ValueError(f"probe {i} missing question")
        layer = str(probe.get("layer") or "").strip()
        if layer not in LAYERS:
            raise ValueError(f"probe {i} layer 缺失或非法: {layer!r}")
        valid.append({
            "id": str(probe.get("id") or f"p{i}"),
            "type": "recall",
            "layer": layer,
            "question": str(probe["question"]),
            "reference_answer": str(probe.get("reference_answer") or ""),
        })
    # 每层截到配额（保持生成顺序）；配额未满是坏题库，拒绝落盘
    out: list[dict[str, Any]] = []
    seen: dict[str, int] = {name: 0 for name in LAYERS}
    for probe in valid:
        if seen[probe["layer"]] < layer_counts[probe["layer"]]:
            out.append(probe)
            seen[probe["layer"]] += 1
    if require_layers:
        short = {name: seen[name] for name in LAYERS
                 if seen[name] < layer_counts[name]}
        if short:
            raise ValueError(
                f"分层配额不足（需 {layer_counts}，实得 {seen}）")
    return out


def probe_bank_is_current(existing: dict[str, Any], sha: str) -> bool:
    """题库缓存判定：手写题库（无 sha）视为冻结可复用；
    机器生成的题库须 transcript 未变且出题 prompt 版本一致。"""
    bank_sha = existing.get("transcript_sha256")
    if bank_sha is None:
        return True
    return bank_sha == sha and existing.get("gen_prompt_version") == GEN_PROMPT_VERSION


def generate_probes(
    fixture_id: str,
    *,
    n_questions: int = 15,
    layer_counts: dict[str, int] | None = None,
    model_id: str | None = None,
    keep_n: int | None = None,
    model_user: str | None = None,
    force: bool = False,
) -> Path:
    fixture = load_fixture(fixture_id)
    messages = parse_fixture_messages(fixture["messages"])
    if keep_n is None:
        from noesis.config.env import ModelConfig
        keep_n = int(ModelConfig.summarization_messages_to_keep or 4)
    region = compacted_region(fixture["messages"], keep_n=keep_n)
    counts = layer_counts or even_layer_split(n_questions)

    # 既有题库且 transcript 未变 → 直接复用（缓存语义）；
    # 无 sha 的旧手写题库视为已缓存（fixtures 冻结，编辑 fixture 须手动重新生成）
    sha = transcript_sha(fixture["messages"])
    if not force:
        try:
            existing = load_probes(fixture_id)
            if probe_bank_is_current(existing, sha):
                print(f"probes 缓存命中（transcript 未变或手写题库）: {fixture_id}")
                return PROBES_DIR / f"{fixture_id}.probes.json"
            print(
                "transcript 或出题 prompt 版本已变化，重新生成题库",
                file=sys.stderr,
            )
        except FileNotFoundError:
            pass

    from noesis.llm import get_llm

    if model_user:
        from evals.bootstrap import bind_user_model_sync
        model_id = bind_user_model_sync(model_user, model_id)

    region_text = "\n\n".join(
        f"[{m.get('type')}] {m.get('content', '')}" for m in region)
    region_text = sample_region_text(region_text)

    llm = get_llm(model_id=model_id)
    prompt = build_gen_prompt(region_text, layer_counts=counts)
    raw = str(llm.invoke(prompt).content or "")
    probes = parse_probes_response(raw, layer_counts=counts)

    payload = {
        "fixture_id": fixture_id,
        "transcript_sha256": sha,
        "gen_prompt_version": GEN_PROMPT_VERSION,
        "layer_split": counts,
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
    parser = argparse.ArgumentParser(description="为压缩 fixture 生成分层事实 recall 题库")
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--questions", type=int, default=15,
                        help="总题量（缺省按层数均分；与 --layer-split 同时给出时须等于其总和）")
    parser.add_argument("--layer-split", default=None,
                        help="三层配额 macro:meso:detail，如 10:5:5；缺省按 --questions 均分")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--keep-n", type=int, default=None,
                        help="压缩保留的最近消息数（默认取配置）")
    parser.add_argument("--model-user", default=None, help="自定义模型归属用户")
    parser.add_argument("--force", action="store_true",
                        help="忽略题库缓存强制重新生成")
    args = parser.parse_args()
    counts = parse_layer_split(args.layer_split) if args.layer_split else None
    if counts and sum(counts.values()) != args.questions:
        parser.error(
            f"--questions ({args.questions}) 与 --layer-split 总和 "
            f"({sum(counts.values())}) 不一致")
    generate_probes(args.fixture, n_questions=args.questions,
                    layer_counts=counts,
                    model_id=args.model_id or None, keep_n=args.keep_n,
                    model_user=args.model_user or None, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
