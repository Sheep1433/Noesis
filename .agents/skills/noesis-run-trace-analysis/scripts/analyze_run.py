#!/usr/bin/env python3
"""Analyze one Noesis run from Postgres and local logs, optionally enriched by Langfuse JSON.

    The DB is authoritative for persisted messages/run state. Langfuse is optional
    and is needed for per-generation provider usage; session.extra.context only
    contains the last persisted main-agent input snapshot, not a cumulative token
    total or a guaranteed final request snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_EVENTS = (
    "agent_run_registered",
    "agent_run_terminal_candidate",
    "agent_run_reclaimed",
    "agent run stopped by limit",
    "agent run producer failed",
    "Langfuse retrieval span 失败",
)
LOG_SIGNALS = (
    "RunDurationExceeded",
    "RunOutputExceeded",
    "CancelledError",
    "agent_run_checkpoint_failure",
    "Failed to fetch dynamically imported module",
    "ModuleNotFoundError",
    "Permission denied",
)


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list, int, float, bool, str)):
        return value
    return str(value)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _chars(value: Any) -> int:
    return len(_text(value))


def _approx_tokens(chars: int) -> int:
    # This is a rough display-only estimate. It must never be presented as
    # provider accounting.
    return math.ceil(chars / 4) if chars else 0


def _ms(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _iso_ms(value: Any) -> str:
    timestamp = _ms(value)
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).isoformat()


def _parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content") or {}
    parts = content.get("parts") if isinstance(content, dict) else None
    return [part for part in parts or [] if isinstance(part, dict)]


def _default_log_paths() -> list[Path]:
    # analyze_run.py lives at <repo>/.agents/skills/.../scripts/.
    repo_root = Path(__file__).resolve().parents[4]
    return sorted((repo_root / ".noesis" / "logs").glob("*.log"))


def _log_metrics(session_id: str, paths: list[Path] | None) -> dict[str, Any]:
    """Scan only lines containing this session id; never dump raw log lines."""
    log_paths = paths if paths else _default_log_paths()
    levels: Counter[str] = Counter()
    events: Counter[str] = Counter()
    signals: Counter[str] = Counter()
    run_ids: set[str] = set()
    matched_lines = 0
    scanned_files = 0
    last_timestamp = ""

    import re

    level_pattern = re.compile(r"\|\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\|")
    run_pattern = re.compile(r"\brun_id=([^\s]+)")
    timestamp_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2}[^|]+)")

    for path in log_paths:
        if not path.is_file():
            continue
        scanned_files += 1
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if session_id not in line:
                continue
            matched_lines += 1
            level_match = level_pattern.search(line)
            if level_match:
                levels[level_match.group(1)] += 1
            timestamp_match = timestamp_pattern.search(line)
            if timestamp_match:
                last_timestamp = timestamp_match.group(1).strip()
            run_ids.update(run_pattern.findall(line))
            for event in LOG_EVENTS:
                if event in line:
                    events[event] += 1
            for signal in LOG_SIGNALS:
                if signal in line:
                    signals[signal] += 1

    return {
        "source": "local backend logs",
        "files_scanned": scanned_files,
        "matched_lines": matched_lines,
        "levels": dict(levels),
        "events": dict(events),
        "signals": dict(signals),
        "run_ids": sorted(run_ids),
        "last_matched_timestamp": last_timestamp,
    }


def _connect(args: argparse.Namespace):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "缺少 psycopg。请使用 backend/.venv/bin/python，或在 backend 环境执行 uv run。"
        ) from exc

    dsn = args.dsn or os.getenv("NOESIS_DATABASE_URL") or os.getenv("DATABASE_URL")
    if dsn:
        return psycopg.connect(dsn, row_factory=dict_row)

    kwargs: dict[str, Any] = {
        "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "user": os.getenv("POSTGRES_USER", "noesis"),
        "dbname": os.getenv("POSTGRES_DATABASE", "noesis"),
        "row_factory": dict_row,
    }
    password = os.getenv("POSTGRES_PASSWORD")
    if password:
        kwargs["password"] = password
    return psycopg.connect(**kwargs)


def _load_run(conn, session_id: str | None) -> dict[str, Any]:
    with conn.cursor() as cur:
        if session_id:
            cur.execute(
                "SELECT id FROM t_chat_session WHERE id = %s AND deleted_at IS NULL",
                (session_id,),
            )
        else:
            cur.execute(
                """
                SELECT id
                FROM t_chat_session
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """
            )
        session = cur.fetchone()
        if not session:
            raise SystemExit("未找到目标 session。请传入 --session-id。")
        sid = session["id"]

        cur.execute(
            "SELECT * FROM t_chat_session WHERE id = %s",
            (sid,),
        )
        session_row = cur.fetchone()
        cur.execute(
            """
            SELECT id, session_id, parent_id, role, content, extra, status,
                   message_sequence, created_at
            FROM t_chat_message
            WHERE session_id = %s AND deleted_at IS NULL
            ORDER BY message_sequence, created_at
            """,
            (sid,),
        )
        messages = list(cur.fetchall())
        cur.execute(
            """
            SELECT id, session_id, assistant_message_id, qa_type, status,
                   last_sequence, attempt_id, finish_reason, error_code,
                   user_error_message,
                   created_at, started_at, updated_at, finished_at, snapshot
            FROM t_agent_run
            WHERE session_id = %s
            ORDER BY created_at
            """,
            (sid,),
        )
        runs = list(cur.fetchall())
    return {"session": session_row, "messages": messages, "runs": runs}


def _usage_number(usage: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = usage.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _langfuse_metrics(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    observations = data.get("observations") or []
    generations = [obs for obs in observations if obs.get("type") == "GENERATION"]
    rows: list[dict[str, Any]] = []
    for obs in generations:
        usage = obs.get("usage") or obs.get("usageDetails") or {}
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = _usage_number(usage, "input_tokens", "prompt_tokens", "input")
        output_tokens = _usage_number(usage, "output_tokens", "completion_tokens", "output")
        total_tokens = _usage_number(usage, "total_tokens", "total")
        rows.append(
            {
                "id": obs.get("id"),
                "name": obs.get("name"),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "parent_observation_id": obs.get("parentObservationId"),
            }
        )
    input_values = [row["input_tokens"] for row in rows if row["input_tokens"] is not None]
    output_values = [row["output_tokens"] for row in rows if row["output_tokens"] is not None]
    total_values = [row["total_tokens"] for row in rows if row["total_tokens"] is not None]
    return {
        "trace_id": data.get("id"),
        "session_id": data.get("sessionId"),
        "generation_count": len(generations),
        "usage_rows": len(input_values),
        "input_tokens_sum": sum(input_values),
        "output_tokens_sum": sum(output_values),
        "total_tokens_sum": sum(total_values),
        "max_input_tokens": max(input_values, default=None),
        "last_input_tokens": input_values[-1] if input_values else None,
        "generations": rows,
    }


def _metrics(run: dict[str, Any], logs: dict[str, Any] | None = None) -> dict[str, Any]:
    session = run["session"] or {}
    messages = run["messages"]
    all_parts = [part for message in messages for part in _parts(message)]
    tools = [part for part in all_parts if part.get("type") == "tool"]
    retrievals = [part for part in all_parts if part.get("type") == "retrieval"]
    task_tools = [part for part in tools if part.get("name") == "task"]

    output_lengths = [_chars(part.get("output")) for part in tools]
    input_lengths = [_chars(part.get("input")) for part in tools]
    durations = [int(part["duration_ms"]) for part in tools if part.get("duration_ms") is not None]
    retrieval_result_count = sum(
        len(part.get("results") or []) for part in retrievals if isinstance(part.get("results"), list)
    )
    context = (session.get("extra") or {}).get("context") or {}
    run_rows = run["runs"]
    latest_run = run_rows[-1] if run_rows else None

    return {
        "session": {
            "id": session.get("id"),
            "title": session.get("title"),
            "qa_type": (session.get("extra") or {}).get("qa_type"),
            "created_at": _iso_ms(session.get("created_at")),
            "updated_at": _iso_ms(session.get("updated_at")),
        },
        "messages": {
            "count": len(messages),
            "roles": dict(Counter(message.get("role") for message in messages)),
            "statuses": dict(Counter(message.get("status") for message in messages)),
            "parts": len(all_parts),
            "part_types": dict(Counter(part.get("type") for part in all_parts)),
        },
        "tools": {
            "count": len(tools),
            "names": dict(Counter(part.get("name") or "<unknown>" for part in tools)),
            "statuses": dict(Counter(part.get("status") or "<unknown>" for part in tools)),
            "failed": sum(1 for part in tools if part.get("status") not in ("success", "completed", "done")),
            "input_chars": sum(input_lengths),
            "output_chars": sum(output_lengths),
            "output_chars_max": max(output_lengths, default=0),
            "output_chars_avg": round(statistics.mean(output_lengths), 1) if output_lengths else 0,
            "output_chars_p95": round(statistics.quantiles(output_lengths, n=20)[18], 1)
            if len(output_lengths) >= 2
            else (output_lengths[0] if output_lengths else 0),
            "duration_ms_sum": sum(durations),
            "duration_ms_avg": round(statistics.mean(durations), 1) if durations else 0,
            "duration_ms_max": max(durations, default=0),
        },
        "subagents": {
            "task_tool_calls": len(task_tools),
            "statuses": dict(Counter(part.get("status") or "<unknown>" for part in task_tools)),
            "failed": sum(1 for part in task_tools if part.get("status") not in ("success", "completed", "done")),
            "cancelled": sum(1 for part in task_tools if part.get("state") == "cancelled" or part.get("outcome") == "cancelled"),
        },
        "retrieval": {
            "parts": len(retrievals),
            "result_items": retrieval_result_count,
            "avg_results_per_part": round(retrieval_result_count / len(retrievals), 1) if retrievals else 0,
        },
        "context": {
            "source": "session.extra.context",
            "meaning": "last persisted main-agent provider input snapshot; not cumulative usage",
            "current_tokens": context.get("current_tokens"),
            "max_tokens": context.get("max_tokens"),
            "used_percentage": context.get("used_percentage"),
            "updated_at": context.get("updated_at"),
        },
        "run": {
            "count": len(run_rows),
            "latest": {
                key: _json(latest_run.get(key))
                for key in (
                    "id", "status", "last_sequence", "attempt_id", "finish_reason",
                    "error_code", "user_error_message",
                )
            }
            if latest_run
            else None,
        },
        "logs": logs,
    }


def _print_markdown(report: dict[str, Any], langfuse: dict[str, Any] | None) -> None:
    session = report["session"]
    messages = report["messages"]
    tools = report["tools"]
    subagents = report["subagents"]
    retrieval = report["retrieval"]
    context = report["context"]
    latest = report["run"]["latest"]

    print("## Noesis 单轮运行分析")
    print(f"- session：`{session['id']}`")
    print(f"- qa_type：`{session.get('qa_type')}`")
    print(f"- 消息：{messages['count']} 条；parts：{messages['parts']} 个")
    print(f"- run 终态：`{(latest or {}).get('status')}` / `{(latest or {}).get('error_code')}`")
    print()

    print("## 调用统计")
    print(f"- 工具调用：{tools['count']} 次；失败：{tools['failed']} 次")
    print(f"- 工具分布：`{json.dumps(tools['names'], ensure_ascii=False)}`")
    print(f"- subagent `task`：{subagents['task_tool_calls']} 次；失败：{subagents['failed']} 次；取消：{subagents['cancelled']} 次")
    print(f"- retrieval parts：{retrieval['parts']} 个；来源项：{retrieval['result_items']} 个")
    print()

    print("## 工具结果体量")
    print(f"- 输入：{tools['input_chars']:,} chars（约 {_approx_tokens(tools['input_chars']):,} tokens，仅粗略估算）")
    print(f"- 输出：{tools['output_chars']:,} chars（约 {_approx_tokens(tools['output_chars']):,} tokens，仅粗略估算）")
    print(f"- 单次输出：平均 {tools['output_chars_avg']:,} chars，P95 {tools['output_chars_p95']:,} chars，最大 {tools['output_chars_max']:,} chars")
    print(f"- 工具耗时：合计 {tools['duration_ms_sum']:,} ms，平均 {tools['duration_ms_avg']:,} ms，最大 {tools['duration_ms_max']:,} ms")
    print()

    logs = report.get("logs")
    print("## 本地后台日志")
    if not logs or not logs.get("files_scanned"):
        print("- 未找到可扫描的 `.noesis/logs/*.log`。可通过 `--log-path` 指定日志文件。")
    elif not logs.get("matched_lines"):
        print(f"- 扫描 {logs['files_scanned']} 个日志文件，但没有找到该 session 的日志行。")
    else:
        print(f"- 扫描 {logs['files_scanned']} 个文件，命中 {logs['matched_lines']} 行")
        print(f"- 日志级别：`{json.dumps(logs['levels'], ensure_ascii=False)}`")
        print(f"- 运行事件：`{json.dumps(logs['events'], ensure_ascii=False)}`")
        print(f"- 错误信号：`{json.dumps(logs['signals'], ensure_ascii=False)}`")
        print(f"- 关联 run_id：`{json.dumps(logs['run_ids'], ensure_ascii=False)}`")
        print(f"- 最后命中时间：`{logs['last_matched_timestamp']}`")
    print()

    print("## 上下文长度")
    if context.get("current_tokens") is None:
        print("- 数据库没有保存 context snapshot；不能从消息 parts 反推出 provider 的精确 input_tokens。")
    else:
        print(f"- 最后一次已落库主 Agent input：{context['current_tokens']:,} tokens / {context.get('max_tokens'):,}，占用 {context.get('used_percentage')}%")
        print(f"- 来源：`session.extra.context`，更新时间：`{context.get('updated_at')}`")
        print("- 注意：这是最后一次成功写入的主 Agent provider input 快照，不是整轮累计 token，也不包含 subagent 每次请求的独立上下文；快照时间可能早于 run 终态。")
    if langfuse:
        print(f"- Langfuse generations：{langfuse['generation_count']} 次，含 usage：{langfuse['usage_rows']} 次")
        if langfuse["usage_rows"]:
            print(f"- Langfuse input 总和：{langfuse['input_tokens_sum']:,}；output 总和：{langfuse['output_tokens_sum']:,}")
            print(f"- Langfuse 最大单次 input：{langfuse['max_input_tokens']:,}；最后一次 input：{langfuse['last_input_tokens']:,}")
        else:
            print("- Langfuse generation 没有可解析的 usage 字段。")
    else:
        print("- 未提供 Langfuse JSON；无法得到每次模型请求、尤其是 subagent 请求的精确 provider usage。")
    print()

    print("## 解释边界")
    print("- `tool output chars` 是数据库中保存的工具结果体量，不等于每次模型请求实际接收的 token。")
    print("- 只有 provider usage/Langfuse generation 才能作为精确 token accounting；字符除以 4 只是排查体量的粗估。")
    print("- 工具 part 的 `status` 只说明该 part 的落库状态；父 run 是否失败要看 `t_agent_run` 终态和 error_code。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", help="目标 Noesis session ID；省略时分析最近更新的 session")
    parser.add_argument("--dsn", help="Postgres DSN；默认读取 NOESIS_DATABASE_URL/DATABASE_URL 或 POSTGRES_* 环境变量")
    parser.add_argument("--log-path", action="append", type=Path, help="可重复传入；默认自动扫描 .noesis/logs/*.log")
    parser.add_argument("--langfuse-json", type=Path, help="可选的 Langfuse trace JSON，用于补充每次 generation usage")
    parser.add_argument("--json", action="store_true", dest="as_json", help="输出机器可读 JSON")
    args = parser.parse_args()

    with _connect(args) as conn:
        run = _load_run(conn, args.session_id)
    logs = _log_metrics(run["session"]["id"], args.log_path)
    report = _metrics(run, logs)
    langfuse = _langfuse_metrics(args.langfuse_json) if args.langfuse_json else None

    if args.as_json:
        output = {"report": report, "langfuse": langfuse}
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    else:
        _print_markdown(report, langfuse)
    return 0


if __name__ == "__main__":
    sys.exit(main())
