"""CLI: 压缩评测（三组对照，真 Agent 路径）。

用法（backend/ 下）:
    uv run python -m evals.compression --tag t1 \
        --model-id <作答模型> --judge-model-id <判卷模型> [--arms uncompacted,current,recovery]
    uv run python -m evals.compression --tag t1 --fixture debug_session --runs 3 --compare-to results/baseline

作答侧即生产 SuperAgent（真实压缩 + 生产 search_history），链路见
``evals.compression.agent_path``；fixture 以生产持久化服务落库为真实会话。

产物: evals/compression/results/<tag>/{manifest.json, summary.json, summary.md,
runs/*.json, manual_review_queue.json}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any


from evals.compression.fixture_loader import filter_fixtures, list_fixture_ids, load_fixture, load_probes
from evals.compression.rubric import JUDGE_PROMPT_VERSION
from evals.compression.report import (
    CLOSED_BOOK,
    RECOVERY,
    UNCOMPACTED,
    build_summary,
    results_dir_for_tag,
    summarize_arm_runs,
    write_summary,
)
from evals.langfuse_env import eval_langfuse_run, load_eval_langfuse_settings
from evals.manifest import (
    build_manifest,
    init_results_dir,
    require_judge_separation,
    write_manifest,
    write_manual_review_queue,
)

ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = ROOT / "results"
DEFAULT_ARMS = "uncompacted,current"


class _UsageTrackingLLM:
    """包一层 LLM 记录 usage_metadata（manifest 成本看板的数据源）。

    ``bind_tools`` 返回共享同一组计数器的包装：recovery 组的检索循环经
    bind_tools 后调用，token 消耗仍计入本实例。
    """

    def __init__(self, inner, counters: dict | None = None):
        self.inner = inner
        self._counters = counters if counters is not None else {"in": 0, "out": 0}

    @property
    def input_tokens(self) -> int:
        return self._counters["in"]

    @property
    def output_tokens(self) -> int:
        return self._counters["out"]

    def _track(self, response):
        usage = getattr(response, "usage_metadata", None)
        if isinstance(usage, dict):
            self._counters["in"] += int(usage.get("input_tokens") or 0)
            self._counters["out"] += int(usage.get("output_tokens") or 0)
            self._counters["last_in"] = int(usage.get("input_tokens") or 0)
            self._counters["last_out"] = int(usage.get("output_tokens") or 0)
        return response

    def invoke(self, prompt):
        response = self._track(self.inner.invoke(prompt))
        _record_generation(self, prompt, response)
        return response

    def bind_tools(self, tools):
        return _UsageTrackingLLM(self.inner.bind_tools(tools), counters=self._counters)


# Langfuse 过程记录的截断口径：input 保留消息数组结构（UI 按角色分条渲染），
# 单条超长截内容、消息条数超限采首尾并标注；609K fixture 的摘要 prompt 有
# 3600+ 条消息，全文入库会撑爆存储——结构 + 采样标注已够诊断，完整原文在
# 本地 runs 文件里都有。
_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
_MSG_HEAD_CHARS = 800
_MSG_TAIL_CHARS = 300
_MAX_MESSAGES = 120
_GEN_OUTPUT_CHARS = 10_000

# 作答/判卷期间记录当前题号：generation 名带 [probe_id]，Langfuse 里可按题定位
_current_probe_id: ContextVar[str] = ContextVar("eval_current_probe_id", default="")


def _clip_text(text: str, *, head: int = _MSG_HEAD_CHARS, tail: int = _MSG_TAIL_CHARS) -> str:
    if len(text) <= head + tail:
        return text
    return (
        f"[截断：原文 {len(text):,} 字符]\n{text[:head]}\n[……省略……]\n{text[-tail:]}"
    )


def _truncate_messages(prompt) -> list:
    """消息列表 → Langfuse 消息数组；单条截内容、超量采首尾（保角色结构）。"""
    if not isinstance(prompt, list):
        return [{"role": "user", "content": _clip_text(str(prompt))}]
    items = []
    for m in prompt:
        raw_role = str(getattr(m, "type", "") or "user")
        role = _ROLE_MAP.get(raw_role, raw_role)
        entry = {"role": role, "content": _clip_text(str(getattr(m, "content", "") or ""))}
        calls = getattr(m, "tool_calls", None) or []
        if calls:
            entry["tool_calls"] = [
                {"name": c.get("name"), "args": c.get("args")} for c in calls]
        items.append(entry)
    if len(items) <= _MAX_MESSAGES:
        return items
    half = _MAX_MESSAGES // 2
    omitted = len(items) - 2 * half
    return (
        items[:half]
        + [{"role": "system", "content": f"[……中间 {omitted} 条消息省略……]"}]
        + items[-half:]
    )


def _output_payload(response) -> Any:
    """输出载荷，对齐 LangChain AIMessage 真实形态：

    - 纯文本轮：content 字符串（UI 按文本渲染）；
    - 调工具轮：{content, tool_calls}——原生 function calling 下 content 本就
      为空、载荷在 tool_calls（API 结构化字段而非对话文本），空就是空，
      不造占位文本冒充模型输出；tool_calls 完整保留 name/args/id/type。
    """
    content = str(getattr(response, "content", "") or "")[:_GEN_OUTPUT_CHARS]
    calls = getattr(response, "tool_calls", None) or []
    if calls:
        return {
            "content": content,
            "tool_calls": [
                {"name": c.get("name"), "args": c.get("args"),
                 "id": c.get("id"), "type": "tool_call"}
                for c in calls],
        }
    return content


def _record_generation(wrapper: "_UsageTrackingLLM", prompt, response) -> None:
    """每次评测 LLM 调用记一条 GENERATION（作答/判卷/摘要全覆盖）。"""
    try:
        from server.langfuse import record_eval_generation

        probe_id = _current_probe_id.get()
        name = "llm/" + (getattr(wrapper.inner, "model_name", "") or "model")
        if probe_id:
            name += f" [{probe_id}]"
        record_eval_generation(
            name=name,
            input_messages=_truncate_messages(prompt),
            output_payload=_output_payload(response),
            usage={
                "input": wrapper._counters.get("last_in", 0),
                "output": wrapper._counters.get("last_out", 0),
            },
            model=str(getattr(wrapper.inner, "model_name", "") or ""),
        )
    except Exception:
        pass  # Langfuse 过程记录为尽力而为，不阻断评测


def select_probes(
    probes: list[dict[str, Any]],
    *,
    layer: str | None = None,
    max_probes: int | None = None,
) -> list[dict[str, Any]]:
    """选题：先按层过滤再截题量；过滤后为空视为参数错误（而非跑空题库）。"""
    from evals.compression.gen_probes import LAYERS

    if layer is not None and layer not in LAYERS:
        raise ValueError(f"未知 layer: {layer!r}（可选 {'/'.join(LAYERS)}）")
    selected = (
        [p for p in probes if p.get("layer") == layer] if layer else list(probes)
    )
    if not selected:
        raise ValueError(
            f"按 layer={layer!r} 过滤后无题目（题库共 {len(probes)} 题，"
            f"分层: {sorted({str(p.get('layer')) for p in probes})}）")
    if max_probes:
        selected = selected[:max_probes]
    return selected


def _resolve_tag(args: argparse.Namespace) -> str:
    return args.tag or os.environ.get("NOESIS_COMPRESSION_EVAL_TAG") or ""


def _resolve_runs(args: argparse.Namespace) -> int:
    if args.runs is not None:
        return max(1, int(args.runs))
    env_runs = os.environ.get("NOESIS_COMPRESSION_EVAL_RUNS")
    if not env_runs:
        return 1
    try:
        return max(1, int(env_runs))
    except ValueError:
        raise SystemExit(
            f"NOESIS_COMPRESSION_EVAL_RUNS 须为正整数，当前值: {env_runs!r}")


def _write_run_payload(tag: str, fixture_id: str, arm: str, run_index: int, payload: dict) -> None:
    out_dir = results_dir_for_tag(tag) / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{fixture_id}.{arm}.r{run_index}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _eval_langfuse_context(*, tag: str, session_id: str):
    """evals/.env 的 Langfuse 项目 + 生产 merge 函数：真 Agent 事件流层的
    回调由 stream_agent_events 自动挂接；判卷调用经 _record_generation 进同项目。

    OTLP 导出器须打 server 同款直连补丁（绕过系统代理），否则 span 导出
    走代理全部超时丢失（与 uvicorn worker 启动路径对齐）。
    """
    settings = load_eval_langfuse_settings()
    if settings is None or not settings.tracing_enabled:
        yield False
        return
    from noesis.runtime.deps import bind_langfuse
    from server import langfuse as lf

    lf._patch_langfuse_otel_direct_http()
    bind_langfuse(
        tracing_enabled=lambda: True,
        merge_runnable_config=lf.merge_langfuse_runnable_config,
    )
    try:
        with eval_langfuse_run(line="compression", tag=tag, session_id=session_id) as ok:
            yield bool(ok)
    finally:
        # 评测 CLI 是短命进程：SDK/OTLP 的后台批量导出器在进程退出时
        # 不会自动刷盘（服务端常驻无此问题），退出前显式 flush
        try:
            from langfuse import get_client
            get_client().flush()
        except Exception:  # noqa: BLE001
            pass  # 观测尽力而为


def _run_agent_flow(
    fixture: dict[str, Any],
    fixture_id: str,
    probes: list[dict[str, Any]],
    arms: list[str],
    *,
    model_id: str | None,
    user_uuid: str,
    eval_run_id: str,
    run_index: int,
) -> dict[str, Any]:
    """真 Agent 路径一次编排（落库 → 触发压缩 → 每题每组分 thread 作答）。"""
    import asyncio

    from evals.bootstrap import eval_runtime
    from evals.compression.agent_path import run_fixture_arms

    async def _flow():
        async with eval_runtime(no_attachments=True):
            return await run_fixture_arms(
                fixture, fixture_id, probes, arms,
                model_id=model_id, user_id=user_uuid,
                eval_run_id=f"{eval_run_id}-r{run_index}",
            )

    return asyncio.run(_flow())


def _judge_arm_probes(
    *,
    fixture_id: str,
    arm: str,
    run_index: int,
    eval_run_id: str,
    flow: dict[str, Any],
    probes: list[dict[str, Any]],
    judge_llm,
) -> dict[str, Any]:
    """真 Agent 作答结果 → 判卷 → 与旧报告同形状的 arm payload。"""
    from evals.compression.grader import grade_probe

    arm_out = flow["arms"][arm]
    compression = dict(flow["compression"])
    compression["arm"] = arm
    if arm == UNCOMPACTED:
        # 上限参照组：完整原文历史，无压缩动作
        pre_tokens = compression["pre_tokens"]
        compression.update(
            compressed=False, compression_ratio=0.0,
            post_tokens=pre_tokens,
            pre_message_count=compression["pre_message_count"],
            post_message_count=compression["pre_message_count"],
            summary_text="", summary_marker_found=True,
        )

    probe_results = []
    for i, probe in enumerate(probes):
        run_out = arm_out["probes"][i]
        tok_probe = _current_probe_id.set(str(probe.get("id") or ""))
        try:
            judged = grade_probe(
                probe_question=str(probe["question"]),
                probe_type=str(probe["type"]),
                reference_answer=str(probe["reference_answer"]),
                continuation_text=str(run_out["continuation_text"] or ""),
                llm=judge_llm,
            )
        finally:
            _current_probe_id.reset(tok_probe)
        probe_results.append({
            "probe_id": probe["id"],
            "type": probe["type"],
            "layer": probe.get("layer"),
            "question": probe["question"],
            "reference_answer": probe["reference_answer"],
            "continuation_text": run_out["continuation_text"],
            "completed": run_out["completed"],
            "error": run_out["error"],
            "tool_stats": run_out["tool_stats"],
            "recovery_tool_calls": (run_out["tool_stats"] or {}).get("search_history", 0),
            "input_tokens": run_out["input_tokens"],
            "output_tokens": run_out["output_tokens"],
            **judged,
        })

    return {
        "fixture_id": fixture_id,
        "arm": arm,
        "policy": None if arm == UNCOMPACTED else arm,
        "eval_run_id": eval_run_id,
        "session_id": arm_out.get("session_id"),
        "compression": compression,
        "probes": probe_results,
        "usage": arm_out["usage"],
    }


def rejudge(args: argparse.Namespace) -> int:
    """从已有 runs 重判：跳过作答与压缩，用新 judge 对已存的 continuation_text 重新打分。

    逐题作答文本在首跑时已落盘（runs/*.json），重判只花 judge 的少量 token；
    用于 judge 档位升级后在不重跑昂贵作答的前提下刷新基线。
    """
    src = Path(args.rejudge_from)
    src_manifest_path = src / "manifest.json"
    if not src_manifest_path.is_file():
        print(f"源目录缺 manifest.json: {src}", file=sys.stderr)
        return 2
    src_manifest = json.loads(src_manifest_path.read_text(encoding="utf-8"))
    subject_model = (src_manifest.get("models") or {}).get("subject")
    require_judge_separation(subject_model, args.judge_model_id)

    run_paths = sorted((src / "runs").glob("*.r*.json"))
    if not run_paths:
        print(f"源目录无 runs/*.r*.json: {src}", file=sys.stderr)
        return 2

    from evals.compression.grader import grade_probe
    from noesis.llm import get_llm

    judge_user = args.judge_model_user or args.model_user
    if judge_user:
        from evals.bootstrap import bind_user_model_sync
        judge_llm = get_llm(model_id=bind_user_model_sync(judge_user, args.judge_model_id))
    else:
        judge_llm = get_llm(model_id=args.judge_model_id)

    out_dir = init_results_dir(RESULTS_ROOT, args.tag)
    review_records: list[dict[str, Any]] = []
    # 按 fixture×arm 聚合后再汇总（与首跑同口径）：逐 run 文件各出一行会让
    # 逐 fixture 表重复，且 attach_*_deltas 同 key 后行覆盖前行
    grouped_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    print(f"rejudge: {len(run_paths)} 个 run ← {src}（judge={args.judge_model_id}）")

    for path in run_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        judged_probes = []
        for probe in payload["probes"]:
            judged = grade_probe(
                probe_question=probe["question"],
                probe_type=probe["type"],
                reference_answer=probe["reference_answer"],
                continuation_text=probe["continuation_text"],
                llm=judge_llm,
            )
            judged_probes.append({**probe, **judged})
            review_records.append({
                "fixture_id": payload["fixture_id"], "arm": payload["arm"],
                "run_index": payload.get("run_index", 0), **judged})
        new_payload = {**payload, "probes": judged_probes}
        # 重判不改写源产物：新 runs 落在新 tag 下
        _write_run_payload(args.tag, payload["fixture_id"], payload["arm"],
                           payload.get("run_index", 0), new_payload)
        grouped_runs.setdefault(
            (payload["fixture_id"], payload["arm"]), []).append(new_payload)

    arm_summaries = [summarize_arm_runs(payloads) for payloads in grouped_runs.values()]
    runs_per_arm = max(len(payloads) for payloads in grouped_runs.values())

    full_summary = build_summary(args.tag, arm_summaries, runs_per_arm=runs_per_arm)
    json_path, md_path = write_summary(args.tag, full_summary)
    out_dir = results_dir_for_tag(args.tag)
    write_manifest(out_dir, build_manifest(
        eval_line="compression", tag=args.tag,
        subject_model=subject_model,
        judge_model=args.judge_model_id,
        dataset={"rejudge_from": str(src), "runs": len(run_paths)},
        config={"token_counter": "chars/4(content+tool_calls)",
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "rejudge": True},
        usage={"input_tokens": 0, "output_tokens": 0},
        notes=f"rejudge from {src}（作答复用首跑原文，未重新运行 agent/压缩）",
    ))
    write_manual_review_queue(out_dir, review_records, seed=11)
    print(f"Results: {out_dir}\nSummary: {json_path}\nReport:  {md_path}")
    return 0


def run_eval(args: argparse.Namespace) -> int:
    tag = _resolve_tag(args)
    if not tag:
        print("缺少 --tag 或 NOESIS_COMPRESSION_EVAL_TAG", file=sys.stderr)
        return 2
    require_judge_separation(args.model_id or "", args.judge_model_id)

    fixture_filter = args.fixture or os.environ.get("NOESIS_COMPRESSION_EVAL_FIXTURE")
    runs = _resolve_runs(args)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    try:
        fixture_ids = filter_fixtures(list_fixture_ids(), fixture=fixture_filter)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    from noesis.llm import get_llm
    from evals.bootstrap import resolve_user_uuid_sync

    # 自定义模型：分别绑定后构造（端点随对象固定）；未提供 model-user 走内置目录
    if args.model_user:
        from evals.bootstrap import bind_user_model_sync
        judge_user = args.judge_model_user or args.model_user
        judge = _UsageTrackingLLM(get_llm(
            model_id=bind_user_model_sync(judge_user, args.judge_model_id)))
        # 被测绑定同时注入 summarization purpose：真实压缩中间件的摘要引擎
        # 与作答同模型（judge 分离只约束 judge ≠ 作答/摘要）
        subject_snapshot_id = bind_user_model_sync(
            args.model_user, args.model_id, include_summarization=True)
        user_uuid = resolve_user_uuid_sync(args.model_user)
    else:
        subject_snapshot_id = args.model_id
        user_uuid = resolve_user_uuid_sync("test")
        judge = _UsageTrackingLLM(get_llm(model_id=args.judge_model_id))
    # judge 分离同时约束摘要模型：未提供 model-user 时摘要走平台默认模型，
    # 可能与 judge 撞车（model-user 路径下摘要与作答同模型，已被上方校验覆盖）
    summarizer_model = str(getattr(
        get_llm(purpose="summarization"), "model_name", "") or "")
    if summarizer_model and summarizer_model == args.judge_model_id:
        raise ValueError(
            f"judge 模型不得与摘要模型相同（均为 {args.judge_model_id}）；换一个不同档位的 judge")

    eval_run_id = uuid.uuid4().hex[:16]
    all_arm_summaries = []
    review_records: list[dict[str, Any]] = []
    probe_bank_versions: set[str] = set()
    subject_usage = {"input_tokens": 0, "output_tokens": 0}

    print(f"Compression eval tag={tag} fixtures={len(fixture_ids)} arms={arms} runs={runs}")
    for fixture_id in fixture_ids:
        fixture = load_fixture(fixture_id)
        try:
            probes_doc = load_probes(fixture_id)
        except FileNotFoundError:
            print(
                f"fixture {fixture_id} 缺 probe 题库：先运行 "
                f"`uv run python -m evals.compression.gen_probes --fixture {fixture_id}`",
                file=sys.stderr,
            )
            return 2
        probe_bank_versions.add(
            str(probes_doc.get("gen_prompt_version") or "handwritten"))
        # 题目切片在 run 循环外做一次：多次 run 必须同题，保证可比
        try:
            probes = select_probes(
                probes_doc["probes"], layer=args.layer, max_probes=args.max_probes)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2

        # 真 Agent 路径：Langfuse 上下文罩住整个 fixture 流程（触发轮 +
        # 三组作答 + 判卷），trace 由生产事件流层回调自动产出
        with _eval_langfuse_context(
            tag=tag,
            session_id=f"eval-compression-{fixture_id}-{eval_run_id}",
        ):
            payloads_by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in arms}
            for run_idx in range(runs):
                print(f"  {fixture_id} run {run_idx + 1}/{runs} ...", flush=True)
                flow = _run_agent_flow(
                    fixture, fixture_id, probes, arms,
                    model_id=subject_snapshot_id or None,
                    user_uuid=user_uuid,
                    eval_run_id=eval_run_id,
                    run_index=run_idx,
                )
                for arm in arms:
                    payload = _judge_arm_probes(
                        fixture_id=fixture_id, arm=arm, run_index=run_idx,
                        eval_run_id=eval_run_id, flow=flow, probes=probes,
                        judge_llm=judge,
                    )
                    payload["run_index"] = run_idx
                    payloads_by_arm[arm].append(payload)
                    _write_run_payload(tag, fixture_id, arm, run_idx, payload)
                    review_records.extend(
                        {"fixture_id": fixture_id, "arm": arm,
                         "run_index": run_idx, **p}
                        for p in payload["probes"])

            for arm in arms:
                arm_usage = [p.get("usage") or {} for p in payloads_by_arm[arm]]
                subject_usage["input_tokens"] += sum(
                    u.get("input_tokens", 0) for u in arm_usage)
                subject_usage["output_tokens"] += sum(
                    u.get("output_tokens", 0) for u in arm_usage)
                summary = summarize_arm_runs(payloads_by_arm[arm])
                all_arm_summaries.append(summary)
                print(f"    [{arm}] recall%={summary.get('recall_pct')} "
                      f"retained={summary.get('retained_tokens')}")

    full_summary = build_summary(tag, all_arm_summaries, runs_per_arm=runs,
                                 compare_to=args.compare_to)
    json_path, md_path = write_summary(tag, full_summary)
    out_dir = results_dir_for_tag(tag)
    write_manifest(out_dir, build_manifest(
        eval_line="compression", tag=tag,
        subject_model=args.model_id or "(platform-default)",
        judge_model=args.judge_model_id,
        dataset={"fixtures": len(fixture_ids), "arms": arms, "runs_per_arm": runs,
                 "max_probes": args.max_probes or None, "layer": args.layer or None},
        config={"arms": arms, "token_counter": "chars/4(content+tool_calls)",
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "probe_bank_version": ",".join(sorted(probe_bank_versions)),
                "answering_path": "real-agent（生产 SuperAgent + 真实压缩 + 生产 search_history）",
                "recovery_shares_compression_with_current": RECOVERY in arms},
        usage={"input_tokens": subject_usage["input_tokens"] + judge.input_tokens,
               "output_tokens": subject_usage["output_tokens"] + judge.output_tokens},
        notes=(f"触发=/compact 宿主路径（acompact_state，摘要调用无事件流、usage 不计，"
               f"609K fixture 实测约 75 万 in）；作答三组 in={subject_usage['input_tokens']:,} "
               f"out={subject_usage['output_tokens']:,}"),
    ))
    write_manual_review_queue(out_dir, review_records, seed=11)
    print(f"Results: {out_dir}")
    print(f"Summary: {json_path}")
    print(f"Report:  {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Noesis 消息压缩离线评测（三组对照）")
    parser.add_argument("--tag", type=str, default=None, help="本次 run 标签（必填）")
    parser.add_argument("--fixture", type=str, default=None, help="仅跑指定 fixture id")
    parser.add_argument("--runs", type=int, default=None, help="同一 fixture×arm 重复次数（取中位数）")
    parser.add_argument("--arms", type=str, default=DEFAULT_ARMS,
                        help=f"逗号分隔评测组：uncompacted / current / recovery"
                             f"（检索兜底对照，压缩配置同 current，作答额外挂 search_history；"
                             f"默认 {DEFAULT_ARMS}）")
    parser.add_argument("--model-id", type=str, default=None, help="作答（continuation）模型")
    parser.add_argument("--judge-model-id", type=str, required=True,
                        help="判卷模型（须与作答模型不同）")
    parser.add_argument("--model-user", type=str, default="",
                        help="自定义模型归属用户（用户名或 id）；提供时经用户模型解析")
    parser.add_argument("--judge-model-user", type=str, default="",
                        help="judge 模型归属用户（缺省同 --model-user）")
    parser.add_argument("--compare-to", type=Path, default=None,
                        help="与历史 results/<tag> 目录对比")
    parser.add_argument("--rejudge-from", type=Path, default=None,
                        help="重判模式：从已有 results/<tag> 的 runs 复用作答原文，仅用新 judge 重新打分")
    parser.add_argument("--max-probes", type=int, default=None,
                        help="只取题库前 N 题（三组同题切片；成本试跑用）")
    parser.add_argument("--layer", default=None,
                        help="只跑指定层的题（macro/meso/detail；分层题库可用）")
    args = parser.parse_args(argv)
    if args.rejudge_from:
        return rejudge(args)
    return run_eval(args)


if __name__ == "__main__":
    raise SystemExit(main())
