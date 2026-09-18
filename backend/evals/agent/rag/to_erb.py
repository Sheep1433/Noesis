"""把评测 run 的 raw 记录转换成 ERB 官方判分脚本的 answers.jsonl 格式。

官方要求（github.com/onyx-dot-app/EnterpriseRAG-Bench，answer_evaluation/README.md）：
    {"question_id": "qst_0001", "answer": "...", "document_ids": [...]}

- answer：被测系统最终回答原文
- document_ids：检索/阅读过的文档的 ERB 官方 dsid（Document Recall 与
  Invalid Extra Documents 都按它算）。raw 记录里只有短文件名，
  经 evals/kb/erb_data/ingest_plan.json 的路径映射回 dsid。

用法（backend/ 下）:
    uv run python -m evals.agent.rag.to_erb \
        --raw-file evals/agent/rag/results/<tag>/raw.jsonl \
        --output evals/agent/rag/results/<tag>/erb_answers.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

INGEST_PLAN = Path("evals/kb/erb_data/ingest_plan.json")
_DSID_RE = re.compile(r"^(dsid_[0-9a-f]{32})__")


def load_name_to_dsid() -> dict[str, str]:
    """ingest_plan 的 docs 列表：路径含 dsid_XXX__ 前缀，文件名即语料短名。"""
    plan = json.loads(INGEST_PLAN.read_text(encoding="utf-8"))
    docs = plan["docs"] if isinstance(plan, dict) else plan
    mapping: dict[str, str] = {}
    for doc in docs:
        dsid = _DSID_RE.match(Path(doc["path"]).name)
        if dsid:
            mapping[doc["name"]] = dsid.group(1)
    if not mapping:
        raise ValueError(f"{INGEST_PLAN} 中没有解析出任何 dsid 映射")
    return mapping


def collected_files(tool_outputs: list[dict[str, Any]] | None) -> list[str]:
    """去重保序收集该题检索/阅读过的文档短名。"""
    names: list[str] = []
    for item in tool_outputs or []:
        if item.get("name") not in ("search_knowledge_base", "get_knowledge_document"):
            continue
        raw = item.get("output")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if item["name"] == "get_knowledge_document":
            name = str(payload.get("file_name") or "").strip()
            if name:
                names.append(name)
            continue
        for hit in payload.get("results") or []:
            name = str(hit.get("file_name") or "").strip()
            if name:
                names.append(name)
    return list(dict.fromkeys(names))


def records_to_erb(
    records: list[dict[str, Any]], name_to_dsid: dict[str, str]
) -> tuple[list[dict[str, Any]], set[str]]:
    """raw 记录 → 官方 answers 记录列表；返回 (records, 无映射文件名集合)。

    输入需已完成 last-record-wins 去重（断点续跑的 raw.jsonl 由调用方负责）。
    """
    erb_records: list[dict[str, Any]] = []
    unmapped: set[str] = set()
    for record in records:
        if not record.get("completed") or not (record.get("final_text") or "").strip():
            continue
        # 文档优先取 runner 已从引用标记解析的 KB 文件名（HTTP 路径 SSE 工具
        # 输出是渲染摘要，不含文件名）；进程内路径回落 tool_outputs 提取。
        names = list(record.get("kb_refs") or []) or collected_files(
            record.get("tool_outputs"))
        doc_ids = []
        for name in names:
            short = name.split("/", 1)[1] if "/" in name else name
            dsid = name_to_dsid.get(short) or name_to_dsid.get(name)
            if dsid:
                doc_ids.append(dsid)
            else:
                unmapped.add(name)
        erb_records.append({
            "question_id": record["question_id"],
            "answer": record["final_text"],
            "document_ids": sorted(set(doc_ids)),
        })
    return erb_records, unmapped


def write_erb_answers(
    erb_records: list[dict[str, Any]], unmapped: set[str], output: Path
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for record in erb_records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"写出 {len(erb_records)} 条 → {output}")
    if unmapped:
        print(f"警告：{len(unmapped)} 个文件名无 dsid 映射（未计入 document_ids）:")
        for name in sorted(unmapped):
            print(f"  {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="raw.jsonl → ERB 官方 answers.jsonl")
    parser.add_argument("--raw-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    # 同 sample_id 后写覆盖先写（断点续跑的增量日志语义，与 __main__ 一致）
    latest: dict[str, dict[str, Any]] = {}
    for line in Path(args.raw_file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            latest[str(record.get("sample_id"))] = record
    erb_records, unmapped = records_to_erb(
        list(latest.values()), load_name_to_dsid())
    write_erb_answers(erb_records, unmapped, Path(args.output))


if __name__ == "__main__":
    main()
