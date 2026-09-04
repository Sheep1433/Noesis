"""压缩策略预设：policy 名 → compress_options 覆盖（叠加在 fixture 自身配置之上）。

- current：线上行为（force 压缩 + 配置的 keep_n）
- aggressive：更少保留消息，压得更狠
- keep-10：多保留 10 条近期消息（长 keep 对照档）
"""

from __future__ import annotations

from typing import Any, Dict

POLICIES: Dict[str, Dict[str, Any]] = {
    "current": {},
    "aggressive": {"summarization_messages_to_keep": 2},
    "keep-10": {"summarization_messages_to_keep": 10},
}


def resolve_policy_options(
    fixture_options: Dict[str, Any] | None, policy: str
) -> Dict[str, Any]:
    if policy not in POLICIES:
        raise ValueError(f"未知 policy: {policy!r}（可用: {', '.join(POLICIES)}）")
    merged = dict(fixture_options or {})
    merged.update(POLICIES[policy])
    return merged
