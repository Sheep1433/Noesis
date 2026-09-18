"""ERB 语料补充导入：把后续补充的官方文档导入 erb-eval 集合（生产入库管道）。

背景：首批 532 篇（312 GT + 220 干扰）只覆盖 erb211 题池；补齐官方 Conflicting Info
题型需要新文档（17 道题的 34 篇金标，陷阱在金标文档内部——旧建议与现行值同篇）。
本脚本复用平台文档入库管道（deepdoc 解析分块 + 与既有 532 篇一致的处理参数），
产物元数据（document_id/file_hash 等）与首批同构。

输入：官方语料 JSON 文件目录（含 dataset_doc_uuid 与 content_field_names，
来自 EnterpriseRAG-Bench 仓库 generated_data/sources）。

用法（backend/ 下）:
    uv run python -m evals.kb.erb_supplement --docs-dir /tmp/erb_new_docs

幂等：dsid 已在 ingest_plan.json 中的文档自动跳过。
副作用：更新 evals/kb/erb_data/ingest_plan.json；语料 txt 落
.noesis/erb_eval/extracted/<源目录>/dsid__<名>.txt（与首批同布局）。
金标题目行与 fixtures 的扩充不在本脚本（见 evals/README「语料与题池扩充」）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND / "evals" / "kb" / "erb_data"
INGEST_PLAN = DATA_DIR / "ingest_plan.json"
COLLECTION = "erb-eval"
EXTRACTED_ROOT = BACKEND.parent / ".noesis" / "erb_eval" / "extracted"

# 与 erb-eval 既有 532 篇（2026-09-01 批次）的处理参数逐字一致，
# 保证新旧 chunk 的分块/血缘口径相同
ERB_EVAL_PROCESSING_PARAMS = {
    "chunk_preset_id": "general",
    "chunk_template_id": "general",
    "chunk_parser_config": {"chunk_size": 2000, "chunk_overlap": 200},
    "parser_id": "deepdoc",
    "chunk_engine_version": "1",
    "deepdoc_version": "828c5789f651",
}

_DSID_RE = re.compile(r"^(dsid_[0-9a-f]{32})\.json$")


def load_doc_text(doc: dict) -> str:
    """官方文档 JSON → 纯文本：标题行（若有）+ content_field_names 指向的字段内容。"""
    parts = []
    for header_field in ("title", "summary"):
        value = str(doc.get(header_field) or "").strip()
        if value:
            parts.append(value)
            break
    for field in doc.get("content_field_names") or []:
        value = doc.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            parts.append("\n".join(str(item) for item in value))
        else:
            parts.append(str(value))
    return "\n\n".join(p for p in parts if p.strip())


async def run(args: argparse.Namespace) -> int:
    plan = json.loads(INGEST_PLAN.read_text(encoding="utf-8"))
    known_dsids = {re.match(r"^(dsid_[0-9a-f]{32})__", Path(d["path"]).name).group(1)
                   for d in plan["docs"]
                   if re.match(r"^(dsid_[0-9a-f]{32})__", Path(d["path"]).name)}

    docs = []
    for path in sorted(Path(args.docs_dir).glob("dsid_*.json")):
        m = _DSID_RE.match(path.name)
        if not m:
            continue
        dsid = m.group(1)
        if dsid in known_dsids:
            print(f"跳过（已在语料）: {dsid}")
            continue
        docs.append((dsid, json.loads(path.read_text(encoding="utf-8"))))
    if not docs:
        print("没有待导入文档")
        return 0

    from noesis.knowledge.runtime import init_knowledge_base, knowledge_base

    if not await init_knowledge_base():
        raise RuntimeError("知识库初始化失败（Qdrant 不可用？）")
    service = knowledge_base.service()

    # manifest 恢复官方原始文件名（dsid → rel_path）
    manifest_path = Path(args.docs_dir) / "manifest.json"
    rel_by_dsid: dict[str, str] = {}
    if manifest_path.is_file():
        for entry in json.loads(manifest_path.read_text(encoding="utf-8")):
            rel_by_dsid[entry["dsid"]] = entry["rel_path"]

    results = []
    for dsid, doc in docs:
        rel = rel_by_dsid.get(dsid, f"supplement/{dsid}.json")
        rel_path = Path(rel)
        stem = rel_path.stem
        rel_dir = rel_path.parent
        kb_name = f"{stem}~{dsid[6:14]}.txt"

        disk_path = EXTRACTED_ROOT / rel_dir / f"{dsid}__{stem}.txt"
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        disk_path.write_text(load_doc_text(doc), encoding="utf-8")

        result = service.upload_document(
            collection_name=COLLECTION,
            file_name=kb_name,
            file_path=str(disk_path),
            vector_dim=1024,
            effective_processing_params=ERB_EVAL_PROCESSING_PARAMS,
        )
        ok = bool(result.get("success"))
        print(f"{'OK ' if ok else 'FAIL'} {kb_name}: {result.get('message', '')[:80]}")
        if ok:
            results.append({"dsid": dsid, "rel": str(rel_path), "kb_name": kb_name,
                            "disk": str(disk_path)})

    if results:
        for r in results:
            plan["docs"].append({"path": r["disk"], "name": r["kb_name"]})
        INGEST_PLAN.write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ingest_plan.json 已追加 {len(results)} 篇（现共 {len(plan['docs'])} 篇）")
    return 0 if len(results) == len(docs) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="ERB 语料补充导入 erb-eval 集合")
    parser.add_argument("--docs-dir", required=True,
                        help="官方语料 JSON 目录（dsid_*.json）")
    args = parser.parse_args()
    import asyncio

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
