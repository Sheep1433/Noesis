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


class UpdateSessionTitleRequest(BaseModel):
    """更新会话标题请求"""
    title: str = Field(..., description='会话标题')


class ChatSessionResponse(BaseModel):
    """会话响应"""
    id: str = Field(..., description='会话 UUID')
    parent_id: Optional[str] = Field(None, description='父会话 ID')
    user_id: str = Field(..., description='用户 ID')
    title: str = Field(..., description='会话标题')
    extra: Optional[Dict[str, Any]] = Field(None, description='会话元数据')
    created_at: int = Field(..., description='创建时间戳（Unix 毫秒）')
    updated_at: int = Field(..., description='更新时间戳（Unix 毫秒）')
    deleted_at: Optional[int] = Field(None, description='软删时间戳')


class SessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[ChatSessionResponse] = Field(..., description='会话列表')
    total: int = Field(..., description='总数')


# ============================================================================
# Message Schemas (消息)
# ============================================================================

class MessagePart(BaseModel):
    """消息内容片段"""
    type: Literal['text', 'reasoning', 'tool'] = Field(..., description='片段类型')
    content: Optional[str] = Field(None, description='文本内容或推理内容')
    name: Optional[str] = Field(None, description='工具名称')
    arguments: Optional[Dict[str, Any]] = Field(None, description='工具输入参数')
    output: Optional[str] = Field(None, description='工具输出结果')
    tool_call_id: Optional[str] = Field(None, description='工具调用ID')


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
    user_id: str = Field(..., description='用户 ID')
    role: str = Field(..., description='角色: user | assistant')
    content: Dict[str, Any] = Field(..., description='消息内容，JSON multipart 格式')
    extra: Optional[Dict[str, Any]] = Field(None, description='消息元数据')
    status: str = Field(..., description='状态: completed | partial')
    created_at: int = Field(..., description='创建时间戳（Unix 毫秒）')


class MessageListResponse(BaseModel):
    """消息列表响应"""
    messages: List[ChatMessageResponse] = Field(..., description='消息列表')
    total: int = Field(..., description='总数')


# ============================================================================
# API Schemas (API 层级)
# ============================================================================

class SendMessageRequest(BaseModel):
    """发送消息请求（POST /api/chat/sessions/stream）"""
    session_id: Optional[str] = Field(None, description='会话 ID')
    content: str = Field(..., description='消息内容')
    parent_id: Optional[str] = Field(None, description='父消息 ID')
    role: Literal['user', 'assistant'] = Field('user', description='角色: user | assistant')
    extra: Optional[Dict[str, Any]] = Field(None, description='额外元数据')


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    message_id: str = Field(..., description='消息 UUID')
    session_id: str = Field(..., description='会话 ID')
    status: str = Field(..., description='消息状态')
