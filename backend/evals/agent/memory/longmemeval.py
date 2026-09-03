"""LongMemEval（v1，S 档）数据接入：下载、规范化、按题导入 Noesis 记忆存储。

数据：HuggingFace ``xiaowu0162/longmemeval-cleaned``（500 题，每题 haystack 多组
真实对话，`answer_session_ids` 原样对应 `haystack_session_ids` 中的条目）。
导入映射：一个 haystack session = 一条 experience 记忆条目（slug = session id），
每题一个隔离评测用户，upsert 幂等，不触碰真实用户数据。

S 档无拒答类题型，负例由自建配对场景构成（见 fixtures.NEGATIVE_QUERIES）。
"""

from __future__ import annotations

import json
import os
import random
import urllib.request
from pathlib import Path
from typing import Any

MEMORY_ROOT = Path(__file__).resolve().parent
DATA_DIR = MEMORY_ROOT / "data"
SOURCE_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/"
    "resolve/main/longmemeval_s_cleaned.json"
)
EXPECTED_COUNT = 500
# 会话正文导入上限（默认 4000 会截断长会话、伤检索）
SESSION_MAX_CHARS = 50_000
USER_PREFIX = "eval-longmemeval"


def eval_user_id(question_id: str) -> str:
    return f"{USER_PREFIX}-{question_id}"


def download_dataset(data_dir: Path | None = None) -> Path:
    """下载数据集到 gitignored 目录；已存在则直接复用。走 HTTPS_PROXY 等环境代理。"""
    target_dir = data_dir or DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "longmemeval_s_cleaned.json"
    if out.is_file() and out.stat().st_size > 10_000_000:
        return out
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    opener = urllib.request.build_opener(*handlers)
    print(f"下载 LongMemEval S 档 → {out}（约 270MB）")
    with opener.open(SOURCE_URL, timeout=600) as resp, out.open("wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    return out


def load_questions(
    *, sample: int | None = None, seed: int = 11, data_dir: Path | None = None
) -> list[dict[str, Any]]:
    """加载并规范化：sessions 与 session_ids 配对，answer_session_ids 原样保留。"""
    path = download_dataset(data_dir)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if len(raw) != EXPECTED_COUNT:
        raise ValueError(f"LongMemEval S 档应为 {EXPECTED_COUNT} 题，实际 {len(raw)}")
    questions = []
    for row in raw:
        sessions = [
            {"session_id": sid, "turns": sess}
            for sid, sess in zip(row["haystack_session_ids"], row["haystack_sessions"])
        ]
        # answer_session_ids 必须原样存在于 haystack（数据集一致性校验）
        known = set(row["haystack_session_ids"])
        if not set(row["answer_session_ids"]) <= known:
            raise ValueError(f"answer_session_ids 超出 haystack: {row['question_id']}")
        questions.append({
            "question_id": row["question_id"],
            "question": row["question"],
            "answer": row["answer"],
            "question_type": row["question_type"],
            "sessions": sessions,
            "answer_session_ids": list(row["answer_session_ids"]),
        })
    if sample:
        rng = random.Random(seed)
        questions = rng.sample(questions, min(sample, len(questions)))
    return questions


def session_body(turns: list[dict[str, Any]]) -> str:
    """对话轮次 → 条目正文（[user]/[assistant] 前缀拼接）。"""
    lines = []
    for turn in turns:
        role = "user" if str(turn.get("role")) == "user" else "assistant"
        lines.append(f"[{role}] {str(turn.get('content') or '').strip()}")
    return "\n\n".join(lines)


def import_question(question: dict[str, Any], *, user_id: str | None = None) -> str:
    """把一题的 haystack 会话幂等导入该题的隔离评测用户；返回 user_id。"""
    from noesis.services.memory.store import MemoryStore

    uid = user_id or eval_user_id(question["question_id"])
    for session in question["sessions"]:
        body = session_body(session["turns"])[:SESSION_MAX_CHARS]
        if not body.strip():
            continue
        MemoryStore.upsert_entry(
            uid,
            memory_type="experience",
            label=str(session["session_id"])[:80],
            body=body,
            description=f"longmemeval session {session['session_id']}",
            sources=["longmemeval-s"],
            slug=str(session["session_id"]),
            max_entry_chars=SESSION_MAX_CHARS,
        )
    return uid


def reset_eval_users(question_ids: list[str]) -> None:
    """清理评测用户的记忆目录（默认不清理：import 幂等，保留供复跑对账）。"""
    import shutil

    from noesis.services.memory.store import MemoryStore

    for qid in question_ids:
        root = MemoryStore.memory_root(eval_user_id(qid))
        if root.is_dir():
            shutil.rmtree(root)
