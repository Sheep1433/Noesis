"""Codex 对照评测：与 Noesis 同题库/同模型/同判分的受控对比。

用法（backend/ 下）:
    uv run python -m evals.agent.deepresearch.codex_runner --tag codex-final-20p

- 被测对象：本机 codex CLI（config.toml 已配 glm-5.3-flash + Volcengine 端点，
  与 Noesis 被测模型同款）；每题独立工作目录 + `codex exec` 子进程。
- 上下文纯净性（受控对比的关键）：codex 会加载全局 AGENTS.md，并从工作目录
  向上收集各级 AGENTS.md 注入上下文——工作目录放仓库内曾把 Noesis 开发指南
  与全局个人记忆塞进每题约 16K 字符的注入（2026-09-15 实测）。因此工作目录
  放 /tmp，并构造专用 CODEX_HOME（仅复制 config.toml/auth.json，无
  AGENTS.md），任务上下文只剩题目本身。
- 交付契约与 Noesis 线同款（最后一条消息为主形态 / final-report.md 备选），
  仅去掉子 Agent 前台等待等 Noesis 侧环境说明。
- 产物：results/<tag>/{articles.jsonl, raw/<id>.jsonl}——articles.jsonl 结构
  与 Noesis 线一致，可直接用 `--tag <tag> --score` 走同一 RACE 判分。
- raw 为 codex --json 事件流原样落盘（工具统计与 token 的可重放底账）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import time
from pathlib import Path

from evals.agent.deepresearch.__main__ import DEFAULT_TASKS, load_tasks

SUITE_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = SUITE_ROOT / "results"
BACKEND_DIR = SUITE_ROOT.parents[2]
#: 工作目录与专用 CODEX_HOME 的根：必须在任何 AGENTS.md 目录树之外
EVAL_ROOT = Path("/tmp/noesis-codex-eval")

CODEX_CONTRACT_SUFFIX = (
    "\n\n（评测模式说明：本次运行为单回合评测。"
    "交付契约——最终报告必须以以下两种形态之一交付，缺一即视为未完成：\n"
    "1. 主形态：报告全文写在你的最后一条消息里（判分只读最后一条消息，"
    "过程中的计划/叙述不会被采集）；\n"
    "2. 备选形态：报告全文写入当前工作目录下的 final-report.md，"
    "并在最后一条消息中注明「报告已写入该文件」。\n"
    "不要把「已转入后台/稍后交付」作为最终输出。）"
)

_HARVEST_MIN_CHARS = 1000
_REPORT_FILENAME = "final-report.md"
_MAX_STREAM_LINE_BYTES = 16 * 1024 * 1024


def prepare_codex_home(tag: str) -> Path:
    """构造专用 CODEX_HOME：复制模型配置与凭据，不带全局 AGENTS.md。"""
    home = EVAL_ROOT / tag.replace("/", "_") / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    src = Path.home() / ".codex"
    for name in ("config.toml", "auth.json"):
        f = src / name
        if f.is_file():
            shutil.copy2(f, home / name)
    # 工作根目录的信任声明（对齐此前 /private/tmp/codex-dr-eval 的 trusted 配置）
    config = home / "config.toml"
    trust = f'\n[projects."{EVAL_ROOT}"]\ntrust_level = "trusted"\n'
    if config.is_file() and str(EVAL_ROOT) not in config.read_text(encoding="utf-8"):
        config.write_text(config.read_text(encoding="utf-8") + trust, encoding="utf-8")
    return home


def _codex_env(codex_home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    # 认证优先级：evals/.env 的 NOESIS_API_KEY（显式可复现）> codex auth.json
    from dotenv import dotenv_values

    env_file = BACKEND_DIR / "evals" / ".env"
    raw = dotenv_values(env_file) if env_file.is_file() else {}
    api_key = os.environ.get("NOESIS_API_KEY") or str(raw.get("NOESIS_API_KEY") or "")
    if api_key.strip():
        env["OPENAI_API_KEY"] = api_key.strip()
    return env


def _collect_codex_stats(events_path: Path) -> dict:
    """从 codex --json 事件流提取工具统计与 token（可重放）。"""
    tool_stats: dict[str, int] = {}
    input_tokens = cached_input = output_tokens = 0
    if not events_path.is_file():
        return {"tool_stats": tool_stats, "input_tokens": 0, "output_tokens": 0,
                "cached_input_tokens": 0}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if obj.get("type") == "item.completed":
            item = obj.get("item") or {}
            itype = str(item.get("type") or "")
            # agent_message/error/reasoning 之外均为工具类执行项
            if itype and itype not in ("agent_message", "error", "reasoning"):
                tool_stats[itype] = tool_stats.get(itype, 0) + 1
        elif obj.get("type") == "turn.completed":
            usage = obj.get("usage") or {}
            input_tokens += int(usage.get("input_tokens") or 0)
            cached_input += int(usage.get("cached_input_tokens") or 0)
            output_tokens += int(usage.get("output_tokens") or 0)
    return {
        "tool_stats": tool_stats,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": cached_input,
    }


async def run_codex_task(
    *,
    prompt: str,
    workdir: Path,
    events_path: Path,
    time_budget_seconds: int,
    codex_home: Path,
) -> dict:
    """单题：spawn codex exec，落事件流，按交付契约收终稿。"""
    workdir.mkdir(parents=True, exist_ok=True)
    last_message = workdir / "last-message.md"
    argv = [
        "codex", "exec",
        "--skip-git-repo-check",
        "-C", str(workdir),
        "-s", "workspace-write",
        "--color", "never",
        "--json",
        "-o", str(last_message),
        prompt + CODEX_CONTRACT_SUFFIX,
    ]
    t0 = time.perf_counter()
    timeout_error: str | None = None
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(workdir),
        env=_codex_env(codex_home),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=_MAX_STREAM_LINE_BYTES,
    )

    async def consume() -> str:
        stderr_tail = ""
        chunks: list[bytes] = []

        async def drain_stderr() -> None:
            assert proc.stderr is not None
            async for chunk in proc.stderr:
                chunks.append(chunk)

        stderr_task = asyncio.create_task(drain_stderr())
        try:
            assert proc.stdout is not None
            with events_path.open("a", encoding="utf-8") as rf:
                async for raw_line in proc.stdout:
                    rf.write(raw_line.decode("utf-8", errors="replace"))
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            stderr_tail = b"".join(chunks).decode("utf-8", errors="replace")[-2000:]
        return stderr_tail

    try:
        stderr_tail = await asyncio.wait_for(consume(), timeout=time_budget_seconds)
    except asyncio.TimeoutError:
        timeout_error = f"timeout after {time_budget_seconds}s"
        _kill_process_group(proc)
        stderr_tail = ""
    except Exception as exc:  # noqa: BLE001
        timeout_error = f"driver failure: {exc!r}"
        _kill_process_group(proc)
        stderr_tail = ""
    exit_code = await proc.wait()

    stats = _collect_codex_stats(events_path)
    article = ""
    if last_message.is_file():
        article = last_message.read_text(encoding="utf-8").strip()
    article_source = "last_message"
    if len(article) < _HARVEST_MIN_CHARS:
        report = workdir / _REPORT_FILENAME
        if report.is_file() and report.stat().st_size > _HARVEST_MIN_CHARS:
            article = report.read_text(encoding="utf-8")
            article_source = f"workspace_file:{_REPORT_FILENAME}"
    error = timeout_error
    if error is None and exit_code != 0:
        error = f"codex exited {exit_code}: {stderr_tail.strip()[:200]}"
    if error is None and not article:
        error = "empty completion: 无终稿（查 raw 事件流与 codex 日志）"
    return {
        "engine": "codex",
        "article": article,
        "article_source": article_source,
        "elapsed_seconds": round(time.perf_counter() - t0, 1),
        "error": error,
        **stats,
    }


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


async def _run(args, tasks: list[dict], out: Path, articles_path: Path) -> int:
    codex_home = prepare_codex_home(args.tag)
    completed: list[dict] = []
    if articles_path.is_file():
        for line in articles_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                completed.append(json.loads(line))
    done_ids = {r["id"] for r in completed}
    if completed:
        print(f"续跑：已有 {len(completed)} 题（{sorted(done_ids)}），跳过")
    pending = [t for t in tasks if t["id"] not in done_ids]

    for i, task in enumerate(pending, 1):
        tid = task["id"]
        print(f"[{i}/{len(pending)}] id={tid} ...", flush=True)
        record = await run_codex_task(
            prompt=task["prompt"],
            workdir=EVAL_ROOT / args.tag.replace("/", "_") / "work" / str(tid),
            events_path=out / "raw" / f"{tid}.jsonl",
            time_budget_seconds=args.time_budget,
            codex_home=codex_home,
        )
        record.update({
            "id": tid,
            "topic": task["topic"],
            "language": task["language"],
            "prompt": task["prompt"],
        })
        with articles_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(
            f"    article {len(record['article'])} 字，耗时 {record['elapsed_seconds']}s，"
            f"工具 {sum(record['tool_stats'].values())} 次，"
            f"in {record['input_tokens']} / out {record['output_tokens']} tok，"
            f"error={record['error']}",
            flush=True,
        )
    print(f"完成：{articles_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DeepResearch Bench Codex 对照评测")
    p.add_argument("--tag", required=True)
    p.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    p.add_argument("--limit", type=int, default=None, help="只跑前 N 题（冒烟）")
    p.add_argument("--time-budget", type=int, default=2700, help="单题时间预算（秒）")
    args = p.parse_args(argv)

    tasks = load_tasks(args.tasks, args.limit)
    out = RESULTS_ROOT / args.tag.replace("/", "_")
    (out / "raw").mkdir(parents=True, exist_ok=True)
    articles_path = out / "articles.jsonl"
    return asyncio.run(_run(args, tasks, out, articles_path))


if __name__ == "__main__":
    raise SystemExit(main())
