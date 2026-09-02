"""文档契约测试：钉住「文档说的事 = 代码做的事」的可枚举面。

覆盖两个漂移最高发的清单面：
  1. SSE run 内容流事件词表——从 langgraph_bridge.py 提取（直接字面量 +
     守卫集转发），断言 chat-streaming.md §4.2b 全部收录；
  2. config.example.yaml——与 AppYamlConfig 模型双向比对：示例不得含
     模型没有的键（改名/删除即红）；模型字段必须进示例，除非在下方
     豁免清单中显式登记并给理由（新字段静默不写文档即红）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

REPO = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# 1. SSE 事件词表
# ---------------------------------------------------------------------------

BRIDGE = (
    BACKEND / "packages/noesis-core/src/noesis/chat/event_mapping/langgraph_bridge.py"
)
STREAMING_DOC = REPO / "docs/engineering/platform/chat-streaming.md"


def _bridge_events() -> set[str]:
    src = BRIDGE.read_text()
    events = set(re.findall(r'_format_sse\(\s*\n?\s*"([a-z-]+)"', src))
    # 动态转发：`if t in (...)` / `if t in {...}` 守卫集里的字面量同样是
    # 事件词表的一部分
    for grp in re.findall(r"\bt in (\([^)]+\)|\{[^}]+\})", src):
        events.update(re.findall(r'"([a-z-]+)"', grp))
    return events


def test_sse_events_documented() -> None:
    events = _bridge_events()
    assert events, "事件提取为空——提取模式与 bridge 实现已漂移，先修本测试"
    doc = STREAMING_DOC.read_text()
    missing = sorted(e for e in events if f"`{e}`" not in doc)
    assert not missing, (
        f"以下 SSE 事件已在 langgraph_bridge 发射但未收录于 "
        f"chat-streaming.md §4.2b：{missing}；请更新文档（或确认事件应删除）"
    )


# ---------------------------------------------------------------------------
# 2. config.example.yaml ↔ AppYamlConfig
# ---------------------------------------------------------------------------

from noesis.config.yaml_config import AppYamlConfig  # noqa: E402

EXAMPLE = BACKEND / "config.example.yaml"

# 模型字段不进示例的显式豁免（登记即 reviewable：新增豁免须给理由）
_EXEMPT_FROM_EXAMPLE = {
    # 有运行时默认值、非用户常规配置面；进示例徒增噪音
    "model.context_window",
    "skills_market.featured_skills",
    "stream.run_max_duration_seconds_super_agent",
    "stream.run_max_subscriptions_global",
    "stream.run_max_subscriptions_per_run",
    "stream.run_max_subscriptions_per_user",
    "subagents.auto_continue",
    "subagents.auto_continue_debounce_seconds",
    "subagents.foreground_max_wait_seconds",
    "subagents.max_concurrent_per_session",
    "subagents.shell_task_timeout_seconds",
    "subagents.stop_grace_seconds",
    "subagents.stop_reconcile_seconds",
    "subagents.task_timeout_seconds",
}


def _model_leaf_paths(model: type[BaseModel], prefix: str = "") -> set[str]:
    out: set[str] = set()
    for name, field in model.model_fields.items():
        path = f"{prefix}{name}"
        ann = field.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.update(_model_leaf_paths(ann, f"{path}."))
        else:
            out.add(path)
    return out


def _yaml_leaf_paths(data: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(_yaml_leaf_paths(value, f"{path}."))
        else:
            out.add(path)
    return out


def test_config_example_matches_model() -> None:
    model_paths = _model_leaf_paths(AppYamlConfig)
    yaml_paths = _yaml_leaf_paths(yaml.safe_load(EXAMPLE.read_text()))

    unknown = sorted(yaml_paths - model_paths)
    assert not unknown, (
        f"config.example.yaml 含模型没有的键（字段已改名/删除？）：{unknown}"
    )

    undocumented = sorted(
        model_paths - yaml_paths - _EXEMPT_FROM_EXAMPLE
    )
    assert not undocumented, (
        f"AppYamlConfig 新字段未进 config.example.yaml：{undocumented}；"
        f"要么补进示例，要么在 _EXEMPT_FROM_EXAMPLE 登记并给理由"
    )
