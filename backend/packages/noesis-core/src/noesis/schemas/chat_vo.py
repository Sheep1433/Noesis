from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field


# ============================================================================
# Session Schemas (会话)
# ============================================================================

class CreateSessionRequest(BaseModel):
    """创建会话请求"""
    title: Optional[str] = Field(None, description='会话标题，不传则使用默认标题')
    parent_id: Optional[str] = Field(None, description='父会话 ID（subagent 场景）')
    extra: Optional[Dict[str, Any]] = Field(None, description='会话元数据')


class EnsureSessionRequest(BaseModel):
    """幂等物化会话（client session_id + get_or_create）"""
    title: Optional[str] = Field(None, description='会话标题，不传则使用默认标题')
    extra: Optional[Dict[str, Any]] = Field(None, description='会话元数据，如 qa_type')


class UpdateSessionTitleRequest(BaseModel):
    """更新会话标题请求"""
    title: str = Field(..., description='会话标题')


class UpdateSessionMetaRequest(BaseModel):
    """更新会话元信息（置顶 / 归档）请求"""
    pinned: Optional[bool] = Field(None, description='是否置顶；None 表示不变')
    archived: Optional[bool] = Field(None, description='是否归档；None 表示不变')


class ChatSessionResponse(BaseModel):
    """会话响应"""
    id: str = Field(..., description='会话 UUID')
    parent_id: Optional[str] = Field(None, description='父会话 ID')
    kind: str = Field('root', description='会话类型: root | subagent')
    created_by_run_id: Optional[str] = Field(None, description='创建该子会话的 Agent run ID')
    created_by_tool_call_id: Optional[str] = Field(None, description='创建该子会话的工具调用 ID')
    title: str = Field(..., description='会话标题')
    extra: Optional[Dict[str, Any]] = Field(None, description='会话元数据')
    created_at: int = Field(..., description='创建时间戳（Unix 毫秒）')
    updated_at: int = Field(..., description='更新时间戳（Unix 毫秒）')
    deleted_at: Optional[int] = Field(None, description='软删时间戳')
    pinned: bool = Field(False, description='是否置顶')
    archived: bool = Field(False, description='是否归档')


class SessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[ChatSessionResponse] = Field(..., description='会话列表')
    total: int = Field(..., description='总数')


class ChildSessionCatalogItem(BaseModel):
    """父会话中的子 Agent 目录项；正文仍通过标准 messages API 读取。"""
    session_id: str = Field(..., description='子会话 ID')
    parent_id: str = Field(..., description='父会话 ID')
    title: str = Field(..., description='子 Agent 标题')
    profile_id: str = Field('task-worker', description='子 Agent 配置标识')
    run_id: Optional[str] = Field(None, description='当前或最近一轮 Agent run ID')
    status: str = Field('completed', description='子 Agent 状态')
    turn_count: int = Field(0, description='对话轮数')
    step_count: int = Field(0, description='执行步数')
    started_at: Optional[int] = Field(None, description='最近一轮开始时间（Unix 毫秒）')
    finished_at: Optional[int] = Field(None, description='最近一轮结束时间（Unix 毫秒）')
    interrupt: Optional[Dict[str, Any]] = Field(None, description='待审批信息')


class ChildSessionCatalogResponse(BaseModel):
    sessions: List[ChildSessionCatalogItem] = Field(default_factory=list, description='子 Agent 会话目录')
    total: int = Field(0, description='子 Agent 会话总数')


# ============================================================================
# Message Schemas (消息)
# ============================================================================

class MessagePart(BaseModel):
    """消息内容片段"""
    type: Literal['text', 'reasoning', 'tool'] = Field(..., description='片段类型')
    content: Optional[str] = Field(None, description='文本内容或推理内容')
    name: Optional[str] = Field(None, description='工具名称')
    input: Optional[Dict[str, Any]] = Field(None, description='工具输入参数')
    output: Optional[str] = Field(None, description='工具输出结果')
    tool_call_id: Optional[str] = Field(None, description='工具调用ID')
    status: Optional[str] = Field(None, description='工具运行状态: running | success | error')
    error: Optional[str] = Field(None, description='工具错误信息')
    duration_ms: Optional[int] = Field(None, description='工具执行耗时（毫秒）')
    parent_task_call_id: Optional[str] = Field(None, description='归属的 task 工具调用 ID')


class MessageContent(BaseModel):
    """消息内容（multipart 格式）"""
    parts: List[MessagePart] = Field(default_factory=list, description='消息片段列表')


class MessageMetadata(BaseModel):
    """消息元数据"""
    model: Optional[str] = Field(None, description='模型名称')
    input_tokens: Optional[int] = Field(None, description='输入 token 数')
    output_tokens: Optional[int] = Field(None, description='输出 token 数')
    finish_reason: Optional[str] = Field(None, description='结束原因: stop | length')
    error: Optional[str] = Field(None, description='异常信息')


class CreateMessageRequest(BaseModel):
    """发送消息请求"""
    content: str = Field(..., description='消息内容（文本）')
    parent_id: Optional[str] = Field(None, description='父消息 ID')
    extra: Optional[Dict[str, Any]] = Field(None, description='消息元数据')


class ChatMessageResponse(BaseModel):
    """消息响应"""
    id: str = Field(..., description='消息 UUID')
    session_id: str = Field(..., description='所属会话 ID')
    parent_id: Optional[str] = Field(None, description='父消息 ID')
    role: str = Field(..., description='角色: user | assistant')
    content: Dict[str, Any] = Field(..., description='消息内容，JSON multipart 格式')
    extra: Optional[Dict[str, Any]] = Field(None, description='消息元数据')
    status: str = Field(..., description='状态: completed | partial')
    message_sequence: int = Field(..., description='会话内严格递增的消息序号')
    created_at: int = Field(..., description='创建时间戳（Unix 毫秒）')
    run_started_at: Optional[int] = Field(None, description='关联 Agent run 的启动时间戳（Unix 毫秒）；仅 assistant 消息有值')
    run_finished_at: Optional[int] = Field(None, description='关联 Agent run 的终态时间戳（Unix 毫秒）；未完成/无 run 为 None')


class MessageListResponse(BaseModel):
    """消息列表响应"""
    messages: List[ChatMessageResponse] = Field(..., description='消息列表')
    total: int = Field(..., description='总数')


# ============================================================================
# API Schemas (API 层级)
# ============================================================================

class SendMessageRequest(BaseModel):
    """直接写入会话消息请求。"""
    session_id: Optional[str] = Field(None, description='会话 ID')
    content: str = Field(..., description='消息内容')
    parent_id: Optional[str] = Field(None, description='父消息 ID')
    role: Literal['user', 'assistant'] = Field('user', description='角色: user | assistant')
    extra: Optional[Dict[str, Any]] = Field(None, description='额外元数据')


class SubagentFollowupRequest(BaseModel):
    """向现有 child session 发起下一轮对话。"""
    message: str = Field(..., min_length=1, description='补充要求')
    model_id: Optional[str] = Field(None, description='该轮使用的模型（缺省沿用当前模型）')
    reasoning_effort: Optional[str] = Field(
        None, description='该轮推理档位 low/medium/high（缺省沿用任务创建时的档位）',
    )


class CreateRunRequest(BaseModel):
    """创建独立 Agent run。"""

    session_id: str = Field(..., min_length=1, description='会话 ID')
    content: str = Field(..., description='用户消息')
    client_request_id: str = Field(..., min_length=8, max_length=64, description='客户端幂等键')
    extra: Optional[Dict[str, Any]] = Field(None, description='模型、qa_type、文件等本轮参数')


class RunCreatedResponse(BaseModel):
    run_id: str = Field(..., description='Agent run ID')
    assistant_message_id: str = Field(..., description='本轮 assistant 消息 ID')
    session_id: str = Field(..., description='会话 ID')
    status: str = Field(..., description='queued | running')
    session_title: str = Field(..., description='服务端最终会话标题')


class RunSnapshotResponse(BaseModel):
    run_id: str = Field(..., description='Agent run ID')
    assistant_message_id: str = Field(..., description='assistant 消息 ID')
    session_id: str = Field(..., description='会话 ID')
    qa_type: str = Field(..., description='问答类型')
    origin: str = Field(..., description='run 来源')
    status: str = Field(..., description='run 状态')
    snapshot_sequence: int = Field(..., description='快照覆盖的最后业务事件序号')
    attempt_id: int = Field(..., description='当前模型 attempt')
    content: Dict[str, Any] = Field(..., description='当前 assistant multipart 快照')
    finish_reason: Optional[str] = Field(None, description='终态原因')
    error_code: Optional[str] = Field(None, description='稳定错误码')
    message: Optional[str] = Field(None, description='用户安全提示')


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    message_id: str = Field(..., description='消息 UUID')
    session_id: str = Field(..., description='会话 ID')
    status: str = Field(..., description='消息状态')
