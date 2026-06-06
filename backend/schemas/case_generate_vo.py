"""测试用例生成 — LangChain 结构化输出模型。"""

import json
from typing import Any, List

from pydantic import BaseModel, Field, field_validator


def _coerce_str_list(value: Any) -> List[str]:
    """LLM 偶发将 list 字段序列化为 JSON 字符串，在此统一解析。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except json.JSONDecodeError:
                pass
        return [s]
    text = str(value).strip()
    return [text] if text else []


class TestPointItem(BaseModel):
    """单个测试点（标题级，阶段 A 产出）。"""

    point_name: str = Field(description="测试点标题，简短、全局唯一、便于勾选，不含完整测试步骤")
    point_level: str = Field(description="优先级：P0/P1/P2，P0 最高")
    point_type: str = Field(
        description="测试类型：functional/performance/security/reliability",
    )


class TestSceneItem(BaseModel):
    """测试场景及其下属测试点。"""

    scene_name: str = Field(description="场景名称，简洁明了")
    scene_description: str = Field(description="场景详细描述")
    risk_level: str = Field(description="风险等级：high/medium/low")
    test_points: List[TestPointItem] = Field(description="该场景下的测试点列表")


class ScenesTestPointsOutput(BaseModel):
    """阶段 A：场景 + 测试点结构化输出。"""

    scenes: List[TestSceneItem] = Field(description="测试场景列表")


class TestCaseOutput(BaseModel):
    """阶段 B：单个测试点展开为可执行用例。"""

    point_name: str = Field(description="与测试点标题一致")
    point_level: str = Field(description="优先级：P0/P1/P2/P3")
    point_type: str = Field(
        description="测试类型：functional/performance/security/reliability",
    )
    preconditions: List[str] = Field(description="前置条件列表")
    test_steps: List[str] = Field(description="测试步骤列表，不要加序号")
    expected_results: List[str] = Field(description="预期结果列表，与关键步骤对应")

    @field_validator("preconditions", "test_steps", "expected_results", mode="before")
    @classmethod
    def _parse_list_fields(cls, value: Any) -> List[str]:
        return _coerce_str_list(value)


class SceneTestCasesOutput(BaseModel):
    """阶段 B：单场景内全部采纳测试点一次性展开为可执行用例。"""

    cases: List[TestCaseOutput] = Field(
        description="本场景各测试点对应的完整用例，须与输入测试点一一对应",
    )
