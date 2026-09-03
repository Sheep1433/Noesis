"""从 ERB 数据集生成 Agent E2E 评测集（fixtures/erb211.jsonl）。

用法（backend/ 下）:
    uv run python -m evals.agent.rag.build_dataset

字段：id / query / collection_names / expected_sources（文件名）/ expected_doc_ids /
gold_answer / answer_facts。只收 GT 全部在语料内的正样本题。
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.kb.erb import data_dir, load_data

ROOT = Path(__file__).resolve().parent
COLLECTION = "erb-eval"


def build_dataset(out_path: Path | None = None) -> Path:
    questions, name_to_dsid = load_data()
    corpus_dsids = set(name_to_dsid.values())
    dsid_to_file = {v: k for k, v in name_to_dsid.items()}

    rows = []
    for q in questions:
        if q["question_type"] == "info_not_found" or not q.get("expected_doc_ids"):
            continue
        if not all(d in corpus_dsids for d in q["expected_doc_ids"]):
            continue
        rows.append({
            "id": q["question_id"],
            "query": q["question"],
            "collection_names": [COLLECTION],
            "expected_sources": [dsid_to_file[d] for d in q["expected_doc_ids"]],
            "expected_doc_ids": q["expected_doc_ids"],
            "gold_answer": q.get("gold_answer") or "",
            "answer_facts": q.get("answer_facts") or [],
        })

    out = out_path or ROOT / "fixtures" / "erb211.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"dataset: {len(rows)} 题 → {out}（源 {data_dir()}）")
    return out


if __name__ == "__main__":
    build_dataset()
