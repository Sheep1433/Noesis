
from pydantic import BaseModel, Field
from typing import Optional, Dict, List


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
    mcp_servers: Optional[List[str]] = Field(
        None,
        description="本轮启用的 MCP server id；省略时读会话 extra（FAULT 缺省回退 profile）",
    )
    enabled_skills: Optional[List[str]] = Field(
        None,
        description="本轮启用的 skill 包名；省略时读会话 extra；键缺失表示全部",
    )


class TestCaseResumeRequest(BaseModel):
    """测试用例生成：采纳测试点后继续生成具体用例"""
    selected_point_names: List[str] = Field(..., description="用户采纳的测试点 point_name 列表，至少一项")


class TestCaseExportCaseItem(BaseModel):
    """导出用例条目（与阶段 B 产出字段对齐）"""

    point_name: str = Field(description="测试点名称")
    case_id: Optional[str] = Field(default=None, description="用例编号")
    point_level: Optional[str] = Field(default=None, description="优先级")
    point_type: Optional[str] = Field(default=None, description="测试类型")
    scene_name: Optional[str] = Field(default=None, description="所属场景")
    preconditions: List[str] = Field(default_factory=list, description="前置条件")
    test_steps: List[str] = Field(default_factory=list, description="测试步骤")
    expected_results: List[str] = Field(default_factory=list, description="预期结果")


class TestCaseExportRequest(BaseModel):
    """测试用例导出为 Markdown；未传 test_cases 时从协调器缓存读取"""

    test_cases: Optional[List[TestCaseExportCaseItem]] = Field(
        default=None,
        description="客户端组装的用例列表；省略或为空时尝试读取本会话最近一次生成结果",
    )
    query: Optional[str] = Field(default=None, description="需求说明，写入报告头部")


class QueryUserRecordRequest(BaseModel):
    """与前端 query_user_qa_record 对齐：page / limit / search_text / chat_id"""

    page: int = Field(1, ge=1, description="页码")
    limit: int = Field(10, ge=1, le=1_000_000, description="每页条数")
    search_text: Optional[str] = Field(None, description="按会话标题模糊搜索")
    chat_id: Optional[str] = Field(None, description="仅返回指定会话")


class QaStopRequest(BaseModel):
    # model_config = ConfigDict(alias_generator=to_camel)
    session_id: str
    qa_type: str = Field(..., description="问答类型，例如 common_qa")
    csrf_token: Optional[str] = Field(None, description="页面卸载 Beacon 使用的 CSRF 凭据")
