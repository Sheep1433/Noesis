"""Run-scoped Provider usage attribution (task 3.2–3.3).

Collects per-model-call usage into cumulative, by_caller, by_model and a
bounded steps list, deduplicated by model run id. Sub-agent usage is counted
into the run total exactly once; the parent task only references attribution,
it does not re-sum child calls (spec §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from noesis_server.domain.chat.streaming.usage_normalize import normalize_usage

#: caller 枚举（spec §5）
CALLER_LEAD_AGENT = "lead_agent"
CALLER_SUBAGENT = "subagent"
CALLER_MIDDLEWARE = "middleware"

#: steps 有界上限（spec §5：禁止按 token delta 无界增长）
MAX_STEPS = 200


@dataclass
class ModelCallAttribution:
    """单次模型调用的归属元数据。"""

    model_run_id: str
    caller: str = CALLER_LEAD_AGENT
    model_id: str = ""
    step_kind: str = "model"
    parent_tool_call_id: str = ""


@dataclass
class RunUsageCollector:
    """run 内 Provider usage 归属聚合。

    - ``cumulative``：run 内总 usage（input/output/total + details）
    - ``by_caller``：按 lead_agent/subagent/middleware 汇总
    - ``by_model``：按 model_id 汇总
    - ``steps``：有界调用记录，供调试视图按需展示
    """

    cumulative: Dict[str, Any] = field(default_factory=dict)
    by_caller: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    by_model: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    steps: list[Dict[str, Any]] = field(default_factory=list)
    _seen_run_ids: set[str] = field(default_factory=set)
    _anon_counter: int = 0

    def record(
        self,
        raw_usage: Any,
        *,
        attribution: ModelCallAttribution,
    ) -> Optional[Dict[str, Any]]:
        """记录一次 model call 的 usage，按 model_run_id 去重。

        返回规范化后的 usage dict（供 SSE 发送），若该 run_id 已记录过则返回 None。
        """
        rid = attribution.model_run_id
        if not rid:
            # 无 run_id 时生成稳定匿名 id（同一 collector 内唯一）
            self._anon_counter += 1
            rid = f"anon-{self._anon_counter}"
            attribution = ModelCallAttribution(
                model_run_id=rid,
                caller=attribution.caller,
                model_id=attribution.model_id,
                step_kind=attribution.step_kind,
                parent_tool_call_id=attribution.parent_tool_call_id,
            )
        if rid in self._seen_run_ids:
            return None
        usage = normalize_usage(raw_usage)
        if not usage:
            return None
        self._seen_run_ids.add(rid)

        self._merge_into(self.cumulative, usage)
        self._merge_into(self.by_caller.setdefault(attribution.caller, {}), usage)
        if attribution.model_id:
            self._merge_into(self.by_model.setdefault(attribution.model_id, {}), usage)

        if len(self.steps) < MAX_STEPS:
            self.steps.append({
                "model_run_id": attribution.model_run_id,
                "caller": attribution.caller,
                "model_id": attribution.model_id,
                "step_kind": attribution.step_kind,
                "parent_tool_call_id": attribution.parent_tool_call_id,
                "usage": dict(usage),
            })
        return usage

    @staticmethod
    def _merge_into(target: Dict[str, Any], incoming: Dict[str, Any]) -> None:
        """合并 usage dict 到目标桶；detail 子项递归累计。"""
        for key, value in incoming.items():
            if key in ("input_token_details", "output_token_details"):
                bucket = target.get(key)
                if not isinstance(bucket, dict):
                    bucket = {}
                    target[key] = bucket
                if isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        if sub_val is None:
                            continue
                        bucket[sub_key] = bucket.get(sub_key, 0) + int(sub_val)
            else:
                try:
                    target[key] = target.get(key, 0) + int(value)
                except (TypeError, ValueError):
                    target[key] = value

    def summary(self) -> Dict[str, Any]:
        """生成给 SSE/前端的摘要（cumulative + by_caller + by_model；steps 按需）。"""
        out: Dict[str, Any] = {"cumulative": dict(self.cumulative)}
        if self.by_caller:
            out["by_caller"] = {k: dict(v) for k, v in self.by_caller.items()}
        if self.by_model:
            out["by_model"] = {k: dict(v) for k, v in self.by_model.items()}
        return out


def resolve_caller(parent_tool_call_id: Optional[str]) -> str:
    """从 parent_task_call_id 判定 caller：有父 task → subagent，否则 lead_agent。"""
    return CALLER_SUBAGENT if parent_tool_call_id else CALLER_LEAD_AGENT
