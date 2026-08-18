"""工具运行追踪：subagent 归属、step 分组、工具计时。

从 LangGraphSseBridge 拆出的 ctx 状态操作集合。ctx 是 astream 事件循环里
逐 run 重建的 dict，这里集中管理它承载的工具运行状态键：
- ``run_id_to_tool_call_id``：回调 run_id → 模型 tool_call_id
- ``task_tool_call_stack``：活跃 task 工具栈（subagent 归属）
- ``pending_model_step_scopes`` / ``step_counters`` / ``current_step_ids``：
  model step 内并行工具的 step_id 分组
- ``tool_start_times``：工具调用耗时起点
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from noesis.chat.message_builder import AssistantMessageBuilder


class ToolRunTracker:
    """围绕 stream ctx 的工具运行状态操作；无自身状态，全部读写 ctx。"""

    @staticmethod
    def ensure_subagent_ctx(ctx: Dict[str, Any]) -> None:
        if "run_id_to_tool_call_id" not in ctx:
            ctx["run_id_to_tool_call_id"] = {}
        if "task_tool_call_stack" not in ctx:
            ctx["task_tool_call_stack"] = []

    @staticmethod
    def ensure_metrics_ctx(ctx: Dict[str, Any]) -> None:
        if "tool_start_times" not in ctx:
            ctx["tool_start_times"] = {}

    @classmethod
    def resolve_parent_task_call_id(cls, item: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[str]:
        """子 Agent 内部 tool 归属到当前活跃的 task tool_call_id（支持 parent_ids 与并行 task）。"""
        cls.ensure_subagent_ctx(ctx)
        stack: List[str] = ctx["task_tool_call_stack"]
        run_map: Dict[str, str] = ctx["run_id_to_tool_call_id"]
        parent_ids = item.get("parent_ids")
        if isinstance(parent_ids, (list, tuple)):
            for pid in reversed(parent_ids):
                if pid is None:
                    continue
                tid = run_map.get(str(pid))
                if tid:
                    return tid
        return stack[-1] if stack else None

    @classmethod
    def register_tool_run(cls, item: Dict[str, Any], tool_call_id: str, ctx: Dict[str, Any]) -> None:
        cls.ensure_subagent_ctx(ctx)
        run_id = item.get("run_id")
        if run_id and str(run_id).strip():
            ctx["run_id_to_tool_call_id"][str(run_id)] = tool_call_id

    @staticmethod
    def mint_step_id(ctx: Dict[str, Any], parent_task_call_id: Optional[str]) -> Optional[str]:
        """为当前 model step 的并行工具调用 mint 分组 step_id。

        ``on_chat_model_start`` 把 scope 加入 ``pending_model_step_scopes``；
        本方法在首个 ``on_tool_start`` 时 mint 新 step_id（按 scope 递增），后续同
        step 的工具复用同一 step_id。model step 若不产 tool 则永不 mint（不分组）。
        scope = parent_task_call_id or "root"，顶层与每个子 Agent 独立计数。
        """
        scope = parent_task_call_id or "root"
        pending = ctx.get("pending_model_step_scopes")
        if pending and scope in pending:
            pending.discard(scope)
            counters = ctx.setdefault("step_counters", {})
            n = counters.get(scope, 0) + 1
            counters[scope] = n
            ctx.setdefault("current_step_ids", {})[scope] = f"{scope}:{n}"
        return ctx.get("current_step_ids", {}).get(scope)

    @staticmethod
    def resolve_tool_step_id(
        builder: Optional[AssistantMessageBuilder], tool_call_id: str, ctx: Dict[str, Any],
    ) -> Optional[str]:
        """tool-output-available 回传与 start 一致的 step_id。

        优先读 builder 里 ToolPart 已记录的 step_id（跨乱序 on_tool_end 仍精确匹配）。
        ``on_tool_end`` 总在 ``on_tool_start`` 之后触发，ToolPart 已带 step_id，
        故此 fallback 极少命中；命中则返回 None（不带 step_id）而非猜测 root scope，
        避免把子 Agent 工具错挂到顶层 step_id。
        """
        if builder is not None:
            tool_part = builder.get_tool(tool_call_id)
            if tool_part is not None and tool_part.step_id:
                return tool_part.step_id
        return None

    @classmethod
    def on_task_tool_start(cls, tool_call_id: str, ctx: Dict[str, Any]) -> None:
        cls.ensure_subagent_ctx(ctx)
        ctx["task_tool_call_stack"].append(tool_call_id)

    @classmethod
    def on_task_tool_end(cls, tool_call_id: str, ctx: Dict[str, Any]) -> None:
        cls.ensure_subagent_ctx(ctx)
        stack: List[str] = ctx["task_tool_call_stack"]
        if not stack:
            return
        if stack[-1] == tool_call_id:
            stack.pop()
            return
        if tool_call_id in stack:
            stack.remove(tool_call_id)

    @classmethod
    def tool_duration_ms(cls, ctx: Dict[str, Any], tool_call_id: str) -> Optional[int]:
        cls.ensure_metrics_ctx(ctx)
        start = ctx["tool_start_times"].pop(tool_call_id, None)
        if start is None:
            return None
        return max(0, int((time.perf_counter() - start) * 1000))
