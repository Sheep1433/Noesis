#!/usr/bin/env python3
"""Scan Langfuse trace JSON or Noesis DB message content for tool/search issues."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

FAIL_PAT = re.compile(
    r"(Command failed|exit code [1-9]|No such file or directory|command not found|"
    r"Permission denied|Read-only|EACCES|ModuleNotFoundError|Cannot find module|"
    r"ENOENT|Traceback|PEP 668|break-system-packages|"
    r"failed to run command|NOT_FOUND)",
    re.I,
)
ENV_PAT = re.compile(r"\b(pip3?|npm|npx|node|nodejs|python3?)\b", re.I)


def _brief(x: Any, n: int = 240) -> str:
    if x is None:
        return ""
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return s.replace("\n", " ")[:n]


def _as_dict(x: Any) -> dict:
    if isinstance(x, dict):
        return x
    if isinstance(x, str):
        try:
            v = json.loads(x)
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def scan_langfuse(data: dict) -> None:
    obs = data.get("observations") or []
    print(
        f"trace={data.get('id')} session={data.get('sessionId')} "
        f"latency={data.get('latency')} output_null={data.get('output') is None}"
    )
    print(f"observations={len(obs)} types={Counter(o.get('type') for o in obs)}")
    errors = [o for o in obs if o.get("level") == "ERROR" or o.get("statusMessage")]
    if errors:
        print("\n## ERROR / statusMessage")
        for o in errors:
            print(
                f"- {o.get('startTime')} {o.get('type')} {o.get('name')} "
                f"level={o.get('level')}"
            )
            print(f"  {(o.get('statusMessage') or '')[:300]}")

    tools = [o for o in obs if o.get("type") == "TOOL"]
    print(f"\n## TOOL ({len(tools)})")
    queries: list[str] = []
    for o in sorted(tools, key=lambda x: x.get("startTime") or ""):
        name = o.get("name")
        inp = o.get("input")
        out = o.get("output")
        out_s = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        flags = []
        if o.get("level") == "ERROR":
            flags.append("LEVEL_ERROR")
        if FAIL_PAT.search(out_s or ""):
            flags.append("FAIL_TEXT")
        if name in ("execute", "shell", "bash") and ENV_PAT.search(
            _brief(inp, 500) + (out_s or "")
        ):
            flags.append("ENV_HINT")
        mark = f" flags={flags}" if flags else ""
        print(f"- {o.get('startTime')} {name}{mark}")
        print(f"  in:  {_brief(inp)}")
        if flags:
            print(f"  out: {_brief(out_s, 400)}")
        if name == "web_search":
            q = _as_dict(inp).get("query")
            if isinstance(q, str):
                queries.append(q)
            _print_search_quality(q, out_s)

    if queries:
        print(f"\n## web_search queries ({len(queries)}, unique={len(set(queries))})")
        for q in queries:
            print(f"- {q}")
        dups = len(queries) - len(set(queries))
        if dups:
            print(f"exact_dup_count={dups}")


def _print_search_quality(query: Any, out_s: str) -> None:
    data = _as_dict(out_s)
    if not data and out_s:
        # Langfuse sometimes nests JSON string in content
        nested = _as_dict(_as_dict(out_s).get("content")) if False else None
        try:
            inner = json.loads(out_s)
            if isinstance(inner, dict) and "results" in inner:
                data = inner
            elif isinstance(inner, dict) and isinstance(inner.get("content"), str):
                data = _as_dict(inner.get("content"))
        except json.JSONDecodeError:
            pass
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return
    weak = 0
    for r in results:
        if not isinstance(r, dict):
            weak += 1
            continue
        title = r.get("title") or ""
        url = r.get("url") or ""
        host = urlparse(url).netloc
        text = f"{title} {r.get('snippet') or ''}"
        if "深圳" not in text and "shenzhen" not in text.lower() and "sz." not in host:
            weak += 1
    provider = data.get("provider")
    backends = data.get("ddg_backends")
    print(
        f"  search: provider={provider} backends={backends} "
        f"n={len(results)} weakish≈{weak} q={query!r}"
    )


def scan_db_parts(data: dict) -> None:
    parts = data.get("parts") or []
    print(f"parts={len(parts)} types={Counter(p.get('type') for p in parts)}")
    tools = [p for p in parts if p.get("type") == "tool"]
    print(f"\n## tool parts ({len(tools)}) status={Counter(p.get('status') for p in tools)}")
    queries: list[str] = []
    for i, p in enumerate(tools):
        name = p.get("name")
        inp = p.get("input")
        out = p.get("output")
        out_s = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        flags = []
        if p.get("status") not in (None, "success", "completed", "done"):
            flags.append(f"status={p.get('status')}")
        if FAIL_PAT.search(out_s or ""):
            flags.append("FAIL_TEXT")
        if ENV_PAT.search(_brief(inp, 500) + (out_s or "")) and (
            name in ("execute", "shell", "bash") or FAIL_PAT.search(out_s or "")
        ):
            flags.append("ENV_HINT")
        if flags or name in ("execute", "shell", "bash", "web_search"):
            print(f"- [{i}] {name} db_status={p.get('status')} flags={flags}")
            print(f"  in:  {_brief(inp)}")
            if "FAIL_TEXT" in flags or name == "execute":
                print(f"  out: {_brief(out_s, 500)}")
        if name == "web_search":
            q = _as_dict(inp).get("query")
            if isinstance(q, str):
                queries.append(q)
            _print_search_quality(q, out_s if isinstance(out_s, str) else "")
            # DB often stores search JSON string directly as output
            if isinstance(out, str) and out.strip().startswith("{"):
                _print_search_quality(q, out)
    if queries:
        print(f"\n## web_search queries ({len(queries)}, unique={len(set(queries))})")
        for q in queries:
            print(f"- {q}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path, help="Langfuse trace JSON or DB message content JSON")
    ap.add_argument(
        "--db-parts",
        action="store_true",
        help="Treat file as t_chat_message.content (has parts[])",
    )
    args = ap.parse_args()
    raw = args.path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if args.db_parts or ("parts" in data and "observations" not in data):
        scan_db_parts(data)
    else:
        scan_langfuse(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
