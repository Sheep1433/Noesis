"""Fixture 与 probe 加载。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

COMPRESSION_ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = COMPRESSION_ROOT / "fixtures"
REAL_FIXTURES_DIR = FIXTURES_DIR / "real"
PROBES_DIR = COMPRESSION_ROOT / "probes"

PROBE_TYPES = frozenset({"recall", "artifact", "continuation", "decision"})


def _fixture_path(fixture_id: str) -> Path:
    for base in (FIXTURES_DIR, REAL_FIXTURES_DIR):
        path = base / f"{fixture_id}.json"
        if path.is_file():
            return path
    return FIXTURES_DIR / f"{fixture_id}.json"


def list_fixture_ids() -> List[str]:
    ids = {p.stem for p in FIXTURES_DIR.glob("*.json")}
    ids |= {p.stem for p in REAL_FIXTURES_DIR.glob("*.json")}
    return sorted(ids)


def load_fixture(fixture_id: str) -> Dict[str, Any]:
    path = _fixture_path(fixture_id)
    if not path.is_file():
        raise FileNotFoundError(f"fixture 不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("id") != fixture_id:
        raise ValueError(f"fixture id 不匹配: {path}")
    if not isinstance(data.get("messages"), list) or not data["messages"]:
        raise ValueError(f"fixture messages 为空: {path}")
    return data


def load_probes(fixture_id: str) -> Dict[str, Any]:
    path = PROBES_DIR / f"{fixture_id}.probes.json"
    if not path.is_file():
        raise FileNotFoundError(f"probe 文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("fixture_id") != fixture_id:
        raise ValueError(f"probe fixture_id 不匹配: {path}")
    probes = data.get("probes")
    if not isinstance(probes, list) or not probes:
        raise ValueError(f"probes 为空: {path}")
    for probe in probes:
        if probe.get("type") not in PROBE_TYPES:
            raise ValueError(f"非法 probe type: {probe.get('type')}")
        for key in ("id", "question", "reference_answer"):
            if not str(probe.get(key) or "").strip():
                raise ValueError(f"probe 缺少 {key}: {probe.get('id')}")
    return data


def filter_fixtures(
    fixture_ids: List[str],
    *,
    fixture: Optional[str] = None,
) -> List[str]:
    if fixture:
        if fixture not in fixture_ids:
            raise ValueError(f"未找到 fixture={fixture!r}")
        return [fixture]
    return fixture_ids


def parse_fixture_messages(raw: List[Dict[str, Any]]) -> List[Any]:
    """fixture 消息 → LangChain 消息（AnyMessage）。

    tool 消息带占位 tool_call_id：真实配对由
    ``agent_path.normalize_fixture_for_state`` 回填（生产状态形状）。
    """
    from langchain_core.messages import convert_to_messages

    lc_payload: List[Dict[str, Any]] = []
    for msg in raw:
        mtype = str(msg.get("type") or "")
        content = msg.get("content", "")
        if mtype == "human":
            lc_payload.append({"role": "user", "content": content})
        elif mtype in ("ai", "assistant"):
            lc_payload.append({"role": "assistant", "content": content})
        elif mtype == "system":
            lc_payload.append({"role": "system", "content": content})
        elif mtype == "tool":
            lc_payload.append(
                {
                    "role": "tool",
                    "content": content,
                    "tool_call_id": msg.get("tool_call_id") or "call_tool",
                    "name": msg.get("name") or "tool",
                }
            )
        else:
            raise ValueError(f"未知 message type: {mtype}")
    return convert_to_messages(lc_payload)


def _approx_token_counter(messages: List[Any]) -> int:
    """chars/4 口径：content + tool_calls 序列化长度（与评分口径一致，进 manifest）。"""
    import json as _json

    total = 0
    for m in messages:
        content = m.content
        if isinstance(content, str):
            total += len(content)
        else:
            total += len(str(content or ""))
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls:
            total += len(_json.dumps(tool_calls, default=str, ensure_ascii=False))
    return total // 4
