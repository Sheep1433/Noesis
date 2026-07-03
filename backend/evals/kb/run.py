"""CLI: uv run python -m evals.kb.run --collection <name> [--dataset fixtures/sample.jsonl]"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

KB_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = KB_ROOT.parents[1]


def _ensure_path() -> None:
    root = str(BACKEND_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            print(f"[warn] 跳过第 {lineno} 行无效 JSON: {exc}")
            continue
        if not row.get("query"):
            print(f"[warn] 跳过第 {lineno} 行：缺少 query")
            continue
        if not row.get("relevant_chunk_ids") and not (
            row.get("file_name") or row.get("header_path") or row.get("gold_snippet")
        ):
            print(f"[warn] 跳过第 {lineno} 行：缺少标注字段")
            continue
        rows.append(row)
    return rows


def _hit_ids_from_hits(hits: List[Any]) -> Set[str]:
    return {str(h.id) for h in hits if getattr(h, "id", None)}


def _match_by_metadata(hit: Any, row: Dict[str, Any]) -> bool:
    fn = (row.get("file_name") or "").strip()
    hp = (row.get("header_path") or "").strip()
    snippet = (row.get("gold_snippet") or "").strip()
    if fn and getattr(hit, "file_name", "") != fn:
        return False
    if hp and (getattr(hit, "header_path", "") or "") != hp:
        return False
    if snippet and snippet not in (getattr(hit, "content", "") or ""):
        return False
    return bool(fn or hp or snippet)


def _resolve_relevant_ids(row: Dict[str, Any], hits: List[Any]) -> Set[str]:
    explicit = row.get("relevant_chunk_ids")
    if isinstance(explicit, list) and explicit:
        return {str(x) for x in explicit if str(x).strip()}

    matched = {str(h.id) for h in hits if _match_by_metadata(h, row)}
    return matched


def compute_recall_hit(
    relevant: Set[str], retrieved: Set[str], k: int
) -> tuple[float, float]:
    if not relevant:
        return 0.0, 0.0
    top = set(list(retrieved)[:k])
    inter = relevant & top
    recall = len(inter) / len(relevant)
    hit = 1.0 if inter else 0.0
    return recall, hit


async def _run_eval(args: argparse.Namespace) -> int:
    _ensure_path()

    from config.database import AsyncSessionLocal
    from config.get_db import init_database
    from kb.chunk import normalize_query_execution_params
    from kb.retrieval import KbRetrievalService
    from services.kb_collection_config_service import KbCollectionConfigService
    from services.qdrant_service import QdrantService, init_qdrant_client, is_qdrant_connected

    await init_database()
    if not await init_qdrant_client():
        print("Qdrant 未连接，评测终止")
        return 1

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = KB_ROOT / dataset_path
    if not dataset_path.exists():
        print(f"数据集不存在: {dataset_path}")
        return 1

    rows = _load_jsonl(dataset_path)
    if not rows:
        print("无有效评测样本")
        return 1

    service = QdrantService()
    col = service.get_collection(args.collection)
    if not col:
        print(f"Collection 不存在: {args.collection}")
        return 1
    vd = int(col.get("vector_dimension") or 1024)

    async with AsyncSessionLocal() as db:
        cfg = await KbCollectionConfigService.get_config(db, args.collection)
    collection_query = (cfg or {}).get("query_params")

    request_overrides = None
    if args.query_params:
        request_overrides = json.loads(args.query_params)

    exec_params = normalize_query_execution_params(
        collection_query=collection_query,
        request_overrides=request_overrides,
    )
    k = int(args.k or exec_params.get("final_top_k") or 10)

    recalls: List[float] = []
    hits: List[float] = []
    failures: List[Dict[str, Any]] = []

    for row in rows:
        query = str(row["query"]).strip()
        try:
            result_hits = KbRetrievalService.search(
                collection_name=args.collection,
                query=query,
                query_execution_params=exec_params,
                vector_dimension=vd,
            ).hits
        except Exception as exc:
            print(f"[error] query={query!r}: {exc}")
            failures.append({"query": query, "error": str(exc)})
            continue

        retrieved_ids = _hit_ids_from_hits(result_hits)
        relevant = _resolve_relevant_ids(row, result_hits)
        recall, hit = compute_recall_hit(relevant, retrieved_ids, k)
        recalls.append(recall)
        hits.append(hit)
        if hit < 1.0:
            failures.append(
                {
                    "query": query,
                    "recall_at_k": recall,
                    "hit_at_k": hit,
                    "retrieved": list(retrieved_ids)[:k],
                    "relevant": list(relevant),
                }
            )

    avg_recall = sum(recalls) / len(recalls) if recalls else 0.0
    avg_hit = sum(hits) / len(hits) if hits else 0.0

    summary = {
        "collection": args.collection,
        "dataset": str(dataset_path),
        "samples": len(recalls),
        "k": k,
        "recall_at_k": round(avg_recall, 4),
        "hit_at_k": round(avg_hit, 4),
        "failures": failures[:20],
        "qdrant_connected": is_qdrant_connected(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if recalls else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="知识库单集合检索评测")
    parser.add_argument("--collection", required=True, help="目标 Qdrant collection")
    parser.add_argument(
        "--dataset",
        default="fixtures/sample.jsonl",
        help="JSONL 基准集路径（相对 evals/kb）",
    )
    parser.add_argument("--k", type=int, default=None, help="覆盖 final_top_k")
    parser.add_argument("--query-params", default=None, help="JSON 字符串覆盖 query_params")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_eval(args)))


if __name__ == "__main__":
    main()
