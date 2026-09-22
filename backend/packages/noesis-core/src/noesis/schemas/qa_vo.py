
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional


class MentionItem(BaseModel):
    """Composer @ / 结构化引用（本轮问答）。"""

    type: Literal["skill", "file", "folder", "subagent"] = Field(..., description="引用类型")
    id: Optional[str] = Field(None, description="skill 包名或 subagent 类型名")
    path: Optional[str] = Field(None, description="相对用户数据根或会话的文件/目录路径")
    source: Optional[Literal["platform", "user"]] = Field(None, description="skill 来源")
    virtual_path: Optional[str] = Field(None, description="可选 Agent 虚拟路径提示")


class QaQueryRequest(BaseModel):
    query: str = Field(..., description="查询内容")
    qa_type: str = Field(..., description="问答类型")
    chat_id: Optional[str] = Field(None, description="对话ID，标识同一会话")
    file_dict: Optional[Dict[str, str]] = Field(None, description="文件列表")
    kb_collections: Optional[List[str]] = Field(
        None,
        description="限定检索的知识库 Collection 列表；空列表表示不限制（检索全部可用库）",
    )
    kb_search_enabled: Optional[bool] = Field(
        None,
        description="是否启用知识库检索；未传时沿用会话设置，默认启用",
    )
    model_id: Optional[str] = Field(
        None,
        description="对话模型目录 id；省略时使用会话 extra 或默认模型",
    )
    reasoning_effort: Optional[str] = Field(
        None,
        description="推理档位（off/low/medium/high/max，仅 OpenAI 协议族透传 reasoning_effort）；None=自动（不传参）",
    )
    extra: Optional[Dict[str, Any]] = Field(
        None,
        description="透传元数据（如 bg_continuation 自动续跑标记）；不进模型输入",
    )
    mcp_servers: Optional[List[str]] = Field(
        None,
        description="本轮启用的 MCP server id；省略时读会话 extra（FAULT 缺省回退 profile）",
    )
    enabled_skills: Optional[List[str]] = Field(
        None,
        description="本轮启用的 skill 包名；省略时读会话 extra；键缺失表示全部",
    )
    mentions: Optional[List[MentionItem]] = Field(
        None,
        description="本轮 @ / 结构化引用；省略则不注入",
    )


class HitlDecisionItem(BaseModel):
    type: Literal["approve", "reject", "respond"] = Field(..., description="HITL 决策类型")
    message: Optional[str] = Field(None, description="reject 说明或 respond 回答文本")


class HitlResumeRequest(BaseModel):
    """SuperAgent HITL：审批 / 澄清后继续同一 thread（返回新 SSE）"""

    interrupt_id: str = Field(..., description="hitl-required 中的 interrupt_id")
    decisions: List[HitlDecisionItem] = Field(..., min_length=1, description="与 action_requests 等长的决策列表")
    grant_scope: Optional[Literal["once", "session"]] = Field(
        None,
        description="网络类 execute：once 仅本次；session 本会话同类放行",
    )


class QueryUserRecordRequest(BaseModel):
    """与前端 query_user_qa_record 对齐：page / limit / search_text / chat_id"""

    page: int = Field(1, ge=1, description="页码")
    limit: int = Field(10, ge=1, le=1_000_000, description="每页条数")
    search_text: Optional[str] = Field(None, description="按会话标题模糊搜索")
    chat_id: Optional[str] = Field(None, description="仅返回指定会话")
    archived: Optional[str] = Field(
        None,
        description="'only' 仅返回归档会话；None/'exclude' 排除归档会话（默认）",
    )


class QaStopRequest(BaseModel):
    # model_config = ConfigDict(alias_generator=to_camel)
    session_id: str
    qa_type: str = Field(..., description="问答类型，例如 common_qa")
    csrf_token: Optional[str] = Field(None, description="页面卸载 Beacon 使用的 CSRF 凭据")
