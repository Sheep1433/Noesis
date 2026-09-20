"""任务状态机与公开快照（subagent / shell 两类任务共享）。

状态机六值与占槽集合见 BgTaskStatus；模块级常量为共享配置默认值。
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# 协作停止宽限默认值（stop_grace_seconds 配置可覆盖）
STOP_GRACE_SECONDS = 30.0
# 硬杀后强制终态对账延迟：CancelledError 可能在深层执行链（langgraph/langchain/
# httpx）被吸收，_arun 的 except CancelledError 收口不保证执行。终态不能依赖
# 被取消协程的配合——宽限超时硬杀后再给协程这么多秒自行收口，仍未收口则由
# reconcile 定时器强制落终态
STOP_RECONCILE_SECONDS = 30.0


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


class BgTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def is_terminal(self) -> bool:
        return self in {
            BgTaskStatus.COMPLETED,
            BgTaskStatus.FAILED,
            BgTaskStatus.CANCELLED,
            BgTaskStatus.TIMED_OUT,
        }


# 占用会话并发槽的状态：排队（QUEUED）只占队列不占槽。
# 停止是乐观终态（受理即 CANCELLED），无中间收口态占槽
_SLOT_STATUSES = frozenset({
    BgTaskStatus.RUNNING,
})


@dataclass
class BackgroundTask:
    """一个后台任务的公开快照（可安全序列化给 API / 工具）。"""

    # 子 Agent 会话 ID；执行状态与会话历史使用同一身份。
    task_id: str
    session_id: str
    user_id: str
    description: str
    # 完整任务指令（子 Agent 首轮输入）；缺省回退 description（旧调用兼容）
    prompt: Optional[str] = None
    child_session_id: Optional[str] = None
    created_by_tool_call_id: Optional[str] = None
    # 标准 child session 对应的 AgentRun；shell job 无此字段。
    run_id: Optional[str] = None
    assistant_message_id: Optional[str] = None
    turn_count: int = 1
    projection_sequence: int = field(default=0, repr=False)
    # subagent 任务均可经 deliver_followup 追加 turn；shell 任务使用独立 kind。
    kind: str = "subagent"
    # 任务的角色类型（start_async_task 的 subagent_type）；shell 任务为 None。
    # 投影与任务卡展示用——worker 编译配方由角色注册表在启动前解析，
    # 执行器不感知类型差异。
    subagent_type: Optional[str] = None
    # kind="shell" 的原始命令（任务详情展示执行内容用）；subagent 任务为 None
    command: Optional[str] = None
    # worker 的 model_id：上下文窗口上限解析用（主对话同源 model_limits）
    model_id: Optional[str] = None
    # 最近一次上下文快照（worker usage 提取；变更才发布/落库）
    context_snapshot: Optional[dict[str, Any]] = None
    status: BgTaskStatus = BgTaskStatus.RUNNING
    result: Optional[str] = None
    error: Optional[str] = None
    # 协作停止请求的终止原因（cancelled / timed_out）；非 None 即停止已受理
    stop_reason: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    # 步数：与 progress 分离的权威计数，口径 = 模型调用次数（与会话统计条
    # usage.steps / TTFT/步 同源）——progress 是有界预览（maxlen=50），
    # 用其长度当步数会在 50 步后封顶（所有长任务都显示「50 步」）
    step_count: int = 0
    # 执行过程摘要（有界，前端任务卡展开显示）；lock 保护跨线程读写
    progress: "collections.deque[dict[str, Any]]" = field(
        default_factory=lambda: collections.deque(maxlen=MAX_PROGRESS_ENTRIES),
    )
    progress_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 子会话检索来源（来源身份 → result dict，插入序即首见序）：终态通知与
    # check_async_task 携带的去重清单；完整数据以子会话落库 retrieval parts 为准
    retrieval_sources: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def to_dict(self, *, include_progress: bool = True) -> dict[str, Any]:
        data = {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "child_session_id": self.child_session_id,
            "created_by_tool_call_id": self.created_by_tool_call_id,
            "user_id": self.user_id,
            "description": self.description,
            "run_id": self.run_id,
            "assistant_message_id": self.assistant_message_id,
            "turn_count": self.turn_count,
            "kind": self.kind,
            "subagent_type": self.subagent_type,
            "command": self.command,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "stop_reason": self.stop_reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            # UI 只显示步数；SSE/列表负载裁掉明细，详情走 messages API
            "progress_count": self.step_count,
        }
        if include_progress:
            data["progress"] = list(self.progress)
        return data


# 默认值；装配方（super_agent）可用 config 覆盖
MAX_CONCURRENT_PER_SESSION = 3
TASK_TIMEOUT_SECONDS = 900.0
# 后台命令任务超时：默认 0=不限时（长命令正是后台化动机，防泄漏靠
# cancel_task + 会话容器生命周期兜底）
SHELL_TASK_TIMEOUT_SECONDS = 0.0
# followup 消息上限（超出丢最旧）
MAX_FOLLOWUPS = 10
# 执行过程摘要上限（超出丢最旧）
MAX_PROGRESS_ENTRIES = 50
_PROGRESS_PREVIEW_CHARS = 120
# shell 任务结果中 stdout/stderr 尾部保留长度
_SHELL_RESULT_TAIL_CHARS = 4000
# 后台命令默认命令级超时（模型未显式传 timeout 时）：对齐 deepagents
# execute 工具的 max_execute_timeout；docker runner 侧 0=不限时由模型显式传
_SHELL_DEFAULT_COMMAND_TIMEOUT = 3600


# 协作停止的部分成果前缀与上限（settle 通知预览 / agent_kernel 回收共用）
_PARTIAL_OUTPUT_PREFIX = "中止前部分产出"
_PARTIAL_RESULT_MAX_CHARS = 4000

