"""Telegram HITL 审批卡片：Inline Keyboard + 短 token（callback_data ≤64 字节）。"""
from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


_NETWORK_RE = re.compile(
    r"\b(?:curl|wget|ssh|scp|nc|ncat|netcat|git\s+push|pip3?\s+install|"
    r"npm\s+install|pnpm\s+(?:i|install|add)|yarn\s+add)\b"
    r"|\|\s*(?:ba)?sh\b",
    re.I,
)


@dataclass
class TelegramHitlPrompt:
    token: str
    session_id: str
    interrupt_id: str
    user_id: str
    chat_id: str
    kind: str
    action_requests: list[dict[str, Any]] = field(default_factory=list)
    message_id: Optional[int] = None
    created_at: float = field(default_factory=time.time)


class TelegramHitlPromptStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_token: dict[str, TelegramHitlPrompt] = {}
        self._by_session: dict[str, str] = {}

    def put(self, prompt: TelegramHitlPrompt) -> None:
        with self._lock:
            old = self._by_session.get(prompt.session_id)
            if old and old in self._by_token:
                del self._by_token[old]
            self._by_token[prompt.token] = prompt
            self._by_session[prompt.session_id] = prompt.token

    def get(self, token: str) -> Optional[TelegramHitlPrompt]:
        with self._lock:
            return self._by_token.get(token)

    def get_by_session(self, session_id: str) -> Optional[TelegramHitlPrompt]:
        with self._lock:
            tok = self._by_session.get(session_id)
            if not tok:
                return None
            return self._by_token.get(tok)

    def pop(self, token: str) -> Optional[TelegramHitlPrompt]:
        with self._lock:
            prompt = self._by_token.pop(token, None)
            if prompt and self._by_session.get(prompt.session_id) == token:
                del self._by_session[prompt.session_id]
            return prompt

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            tok = self._by_session.pop(session_id, None)
            if tok:
                self._by_token.pop(tok, None)


telegram_hitl_prompts = TelegramHitlPromptStore()


def is_network_execute(name: str, args: Any) -> bool:
    if name != "execute":
        return False
    cmd = ""
    if isinstance(args, dict):
        cmd = str(args.get("command") or "")
    return bool(_NETWORK_RE.search(cmd))


def _preview_action(action: dict[str, Any], limit: int = 280) -> str:
    name = str(action.get("name") or "tool")
    args = action.get("args") if isinstance(action.get("args"), dict) else {}
    desc = action.get("description")
    body = ""
    if isinstance(args, dict):
        for key in ("command", "path", "question", "query", "url", "file_path"):
            if args.get(key):
                body = str(args[key]).strip()
                break
        if not body and args:
            try:
                import json

                body = json.dumps(args, ensure_ascii=False)
            except Exception:
                body = str(args)
    if not body and desc:
        body = str(desc)
    body = (body or "").strip()
    if len(body) > limit:
        body = body[: limit - 1] + "…"
    return f"{name}\n{body}".strip() if body else name


def format_hitl_card_text(payload: Dict[str, Any]) -> str:
    kind = str(payload.get("kind") or "approval")
    actions = list(payload.get("action_requests") or [])
    if kind == "clarification":
        lines = ["需要澄清后继续："]
        for i, ar in enumerate(actions, 1):
            lines.append(f"\n[{i}] {_preview_action(ar)}")
        lines.append("\n请直接回复文字作为回答。")
        return "\n".join(lines).strip()

    lines = ["需要审批后继续："]
    for i, ar in enumerate(actions, 1):
        lines.append(f"\n[{i}] {_preview_action(ar)}")
    lines.append("\n请点击下方按钮批准或拒绝。")
    return "\n".join(lines).strip()


def build_approval_keyboard(token: str, *, allow_session_grant: bool) -> Dict[str, Any]:
    """callback_data: th:{token}:{a|r|s}（token 12 hex ≈ 总长 < 24）。"""
    row = [
        {"text": "✅ 批准", "callback_data": f"th:{token}:a"},
        {"text": "❌ 拒绝", "callback_data": f"th:{token}:r"},
    ]
    rows: List[List[Dict[str, str]]] = [row]
    if allow_session_grant:
        rows.append(
            [{"text": "本会话放行", "callback_data": f"th:{token}:s"}]
        )
    return {"inline_keyboard": rows}


def parse_hitl_callback_data(data: str) -> Optional[tuple[str, str]]:
    """返回 (token, op) ；op ∈ {a,r,s}。"""
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != "th":
        return None
    token, op = parts[1], parts[2]
    if not token or op not in ("a", "r", "s"):
        return None
    return token, op


def register_hitl_prompt(
    *,
    session_id: str,
    user_id: str,
    chat_id: str,
    payload: Dict[str, Any],
    message_id: Optional[int] = None,
) -> TelegramHitlPrompt:
    token = secrets.token_hex(6)
    prompt = TelegramHitlPrompt(
        token=token,
        session_id=session_id,
        interrupt_id=str(payload.get("interrupt_id") or ""),
        user_id=str(user_id),
        chat_id=str(chat_id),
        kind=str(payload.get("kind") or "approval"),
        action_requests=list(payload.get("action_requests") or []),
        message_id=message_id,
    )
    telegram_hitl_prompts.put(prompt)
    return prompt


def decisions_for_op(
    op: str,
    action_count: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """op a/r/s → (decisions, grant_scope)。"""
    n = max(1, int(action_count))
    if op == "r":
        return (
            [{"type": "reject", "message": "用户拒绝了该操作"} for _ in range(n)],
            None,
        )
    if op == "s":
        return ([{"type": "approve"} for _ in range(n)], "session")
    return ([{"type": "approve"} for _ in range(n)], None)


def allow_session_grant_for_actions(actions: list[dict[str, Any]]) -> bool:
    return any(
        is_network_execute(str(a.get("name") or ""), a.get("args")) for a in actions
    )
