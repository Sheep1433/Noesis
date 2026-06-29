"""
测试用例生成 - LangGraph StateGraph

交互式流程（无独立策略阶段，与 PRD 对齐）：
1. 测试场景+测试点 → LangChain 结构化输出（依据 query + document_context）→ interrupt 等待用户勾选
2. resume → 按场景 RAG + 单次 LLM 批量生成用例（经 get_stream_writer 按场景推送）→ 结束

关键约束：
- 阶段 B 节点为 async，返回 Command；增量事件经 LangGraph custom stream 透出
- case_coordinator.resume_agent 通过 app.astream(Command, stream_mode=[\"updates\", \"custom\"]) 转发 SSE
"""
import asyncio
import json
from collections.abc import Callable
from typing import Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import StateGraph, END
from langgraph.types import Command

from agent.case_generate.rag import build_scene_rag_context
from llm import get_llm
from schemas.case_generate_vo import SceneTestCasesOutput, ScenesTestPointsOutput, TestCaseOutput
from common.logging import logger


class TestCaseState(TypedDict):
    """测试用例生成状态"""

    # 用户输入
    query: str  # 用户补充说明
    document_context: str  # Word/文档正文或解析结果（由协调器从 file_dict 等拼装）
    source_file_names: List[str]  # 当前会话关联的需求文档 file_name（阶段 B RAG 过滤）

    # 阶段 A 产出
    scenes_testpoints: List[Dict]  # 场景+测试点（JSON）
    selected_point_names: List[str]  # 用户采纳的测试点 point_name

    # 阶段 B 产出
    test_cases: List[Dict]  # 测试用例列表
    retrieval_trace: Optional[Dict[str, Dict]]  # scene_name -> 分 channel 命中 trace

    # 阶段标记
    current_phase: Literal[
        "scenes_testpoints",  # 场景+测试点生成中
        "testpoints_confirm",  # 等待只读勾选后 resume
        "test_cases",  # 用例生成中
        "finish",
    ]

    # 错误信息
    error: Optional[str]


# ============================================================================
# 节点函数
# ============================================================================


def _usable_test_point_count(scenes: List[Dict]) -> int:
    """统计可勾选测试点数量（必须有非空 point_name）。"""
    n = 0
    for s in scenes:
        for tp in s.get("test_points") or []:
            if str(tp.get("point_name") or "").strip():
                n += 1
    return n


def _structured_output(llm, schema, **kwargs):
    """结构化输出：OpenCode Zen 等代理不接受 tool_choice=required，须用 auto。"""
    return llm.with_structured_output(schema, **kwargs).bind(tool_choice="auto")


def generate_scenes_testpoints_node(
    state: TestCaseState,
    config: RunnableConfig | None = None,
) -> Command:
    """
    阶段 A：测试场景+测试点单次生成（同步节点）

    依据 query + document_context 调用 LLM 生成场景+测试点 JSON。
    """
    query = state["query"]
    document_context = state.get("document_context") or ""

    try:
        llm = get_llm()
        prompt = _build_scenes_testpoints_prompt(query, document_context)
        structured_llm = _structured_output(llm, ScenesTestPointsOutput)
        run_config = dict(config or {})
        run_config.setdefault("run_name", "case_generate:scenes_testpoints")
        result: ScenesTestPointsOutput = structured_llm.invoke(prompt, config=run_config)
        scenes_testpoints = [scene.model_dump() for scene in result.scenes]

        logger.info(f"[CaseGraph] 场景+测试点生成完成，共 {len(scenes_testpoints)} 个场景")

        if _usable_test_point_count(scenes_testpoints) == 0:
            logger.warning("[CaseGraph] 结构化输出中无可勾选测试点")
            return Command(
                update={
                    "error": (
                        "未能生成有效测试点：请确认需求文档内容充分，"
                        "或缩短文档后重试。"
                    ),
                    "current_phase": "finish",
                },
            )

        return Command(
            update={
                "scenes_testpoints": scenes_testpoints,
                "selected_point_names": [],  # 等待用户选择
                "current_phase": "testpoints_confirm",
            },
        )

    except Exception as e:
        logger.exception(f"[CaseGraph] 场景+测试点生成异常: {e}")
        return Command(
            update={"error": f"场景+测试点生成异常: {str(e)}", "current_phase": "finish"},
        )


def _build_scenes_map(
    scenes_testpoints: List[Dict],
    selected_names: List[str],
) -> Dict[str, Dict[str, Any]]:
    """按场景收集已选测试点。"""
    selected = set(selected_names)
    scenes_map: Dict[str, Dict[str, Any]] = {}
    for scene in scenes_testpoints:
        scene_name = str(scene.get("scene_name") or "").strip()
        if not scene_name:
            continue
        scene_points = []
        for tp in scene.get("test_points", []):
            if tp.get("point_name") in selected:
                scene_points.append(
                    {
                        "point_name": tp.get("point_name", ""),
                        "point_level": tp.get("point_level", "P1"),
                        "point_type": tp.get("point_type", "functional"),
                        "scene_name": scene_name,
                        "scene_description": scene.get("scene_description", ""),
                        "risk_level": scene.get("risk_level", "medium"),
                    }
                )
        if scene_points:
            scenes_map[scene_name] = {
                "scene": scene,
                "points": scene_points,
            }
    return scenes_map


SceneProgressEmitter = Callable[[str, Any], None]


def _try_stream_writer() -> Optional[SceneProgressEmitter]:
    """图内节点可推送 custom stream；离线直调节点时无 runnable 上下文则跳过。"""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return None

    def emit(kind: str, payload: Any) -> None:
        writer({"kind": kind, "payload": payload})

    return emit


async def _generate_cases_streaming(
    scenes_map: Dict[str, Dict[str, Any]],
    *,
    document_context: str = "",
    source_file_names: Optional[List[str]] = None,
    emit: Optional[SceneProgressEmitter] = None,
    config: RunnableConfig | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict]]:
    """
    按场景并行生成用例；每场景一次 RAG + 一次 LLM，完成后推送 scene-cases / scene-error。

    emit 事件：
        ("scene-cases", {scene_name, cases})
        ("scene-error", {scene_name, point_names, error, cases?})
    """
    if not scenes_map:
        return [], {}

    rag_semaphore = asyncio.Semaphore(3)
    llm_semaphore = asyncio.Semaphore(3)
    case_idx = {"n": 0}
    retrieval_trace: Dict[str, Dict] = {}
    queue: asyncio.Queue = asyncio.Queue()
    total_scenes = len(scenes_map)

    async def process_scene(scene_name: str, bundle: Dict[str, Any]) -> None:
        scene = bundle["scene"]
        points = bundle["points"]
        point_names = [str(p.get("point_name") or "").strip() for p in points]
        point_names = [n for n in point_names if n]

        adopted_names = list(point_names)
        try:
            async with rag_semaphore:
                scene_rag_context, trace_entry = await build_scene_rag_context(
                    scene,
                    adopted_point_names=adopted_names,
                    source_file_names=source_file_names or [],
                )
        except Exception as e:
            logger.exception(
                f"[CaseGraph] 场景 RAG 异常 scene={scene_name}: {e}"
            )
            await queue.put(
                (
                    "scene-error",
                    {
                        "scene_name": scene_name,
                        "point_names": point_names,
                        "error": f"场景 RAG 召回失败: {e}",
                    },
                ),
            )
            return

        retrieval_trace[scene_name] = trace_entry

        try:
            async with llm_semaphore:
                cases, err = await _generate_scene_cases(
                    scene_name,
                    points,
                    scene_rag_context,
                    case_idx["n"],
                    document_context=document_context,
                    config=config,
                )
            case_idx["n"] += len(cases)
            if err:
                await queue.put(
                    (
                        "scene-error",
                        {
                            "scene_name": scene_name,
                            "point_names": point_names,
                            "error": err,
                            "cases": cases,
                        },
                    ),
                )
            else:
                await queue.put(
                    ("scene-cases", {"scene_name": scene_name, "cases": cases}),
                )
        except Exception as e:
            logger.exception(f"[CaseGraph] 场景用例生成异常 scene={scene_name}: {e}")
            await queue.put(
                (
                    "scene-error",
                    {
                        "scene_name": scene_name,
                        "point_names": point_names,
                        "error": str(e),
                    },
                ),
            )

    scene_tasks = [
        asyncio.create_task(process_scene(scene_name, bundle))
        for scene_name, bundle in scenes_map.items()
    ]

    test_cases: List[Dict[str, Any]] = []
    finished_scenes = 0
    try:
        while finished_scenes < total_scenes:
            kind, payload = await queue.get()
            if emit:
                emit(kind, payload)
            if kind == "scene-cases":
                cases = payload.get("cases") if isinstance(payload, dict) else []
                if isinstance(cases, list):
                    test_cases.extend(c for c in cases if isinstance(c, dict))
                finished_scenes += 1
            elif kind == "scene-error":
                cases = payload.get("cases") if isinstance(payload, dict) else []
                if isinstance(cases, list):
                    test_cases.extend(c for c in cases if isinstance(c, dict))
                finished_scenes += 1
    finally:
        await asyncio.gather(*scene_tasks, return_exceptions=True)

    return test_cases, retrieval_trace


async def generate_test_cases_node(
    state: TestCaseState,
    config: RunnableConfig | None = None,
) -> Command:
    """
    阶段 B：测试用例并行生成（async 节点）

    - 按场景：每场景一次 RAG + 一次 LLM 批量生成
    - 增量进度经 get_stream_writer → coordinator 转发为 scene-cases SSE
    """
    selected_names = list(state.get("selected_point_names") or [])
    scenes_testpoints = state.get("scenes_testpoints", [])

    if not selected_names or not scenes_testpoints:
        logger.warning(
            f"[CaseGraph] 无选中的测试点或场景数据缺失 selected={len(selected_names)} scenes={len(scenes_testpoints)}"
        )
        return Command(
            update={
                "error": "未找到已选测试点或场景数据，请重新生成测试点后再试",
                "test_cases": [],
                "retrieval_trace": {},
                "current_phase": "finish",
            }
        )

    scenes_map = _build_scenes_map(scenes_testpoints, selected_names)

    if not scenes_map:
        logger.warning("[CaseGraph] 选中的测试点列表为空")
        return Command(
            update={"test_cases": [], "retrieval_trace": {}, "current_phase": "finish"}
        )

    total_points = sum(len(v["points"]) for v in scenes_map.values())
    logger.info(
        f"[CaseGraph] 开始生成 {total_points} 个测试用例（{len(scenes_map)} 个场景，每场景 1 次 LLM）"
    )

    emit = _try_stream_writer()
    test_cases, retrieval_trace = await _generate_cases_streaming(
        scenes_map,
        document_context=str(state.get("document_context") or ""),
        source_file_names=state.get("source_file_names") or [],
        emit=emit,
        config=config,
    )

    logger.info(f"[CaseGraph] 测试用例生成完成，共 {len(test_cases)} 个")

    return Command(
        update={
            "test_cases": test_cases,
            "retrieval_trace": retrieval_trace,
            "current_phase": "finish",
        },
    )


# ============================================================================
# 图构建
# ============================================================================


def build_test_case_graph() -> StateGraph:
    """
    构建测试用例生成 StateGraph

    流程:
    generate_scenes_testpoints → generate_test_cases → END

    resume 经 CaseCoordinator 发 Command(update={selected_point_names, current_phase}) 进入阶段 B。
    """
    graph = StateGraph(TestCaseState)

    graph.add_node("generate_scenes_testpoints", generate_scenes_testpoints_node)
    graph.add_node("generate_test_cases", generate_test_cases_node)

    graph.add_edge("generate_scenes_testpoints", "generate_test_cases")
    graph.add_edge("generate_test_cases", END)

    graph.set_entry_point("generate_scenes_testpoints")

    return graph


# ============================================================================
# 内部工具
# ============================================================================


def _build_scenes_testpoints_prompt(query: str, document_context: str) -> str:
    """构建场景+测试点生成的提示词（仅依赖需求文档与补充说明，无独立策略阶段）"""
    doc_section = document_context.strip() if document_context else "（未提供文档正文，请仅根据补充说明设计；若信息不足请合理假设并标注假设）"
    return f"""
# Role: 测试场景和测试点设计专家

## Profile
- language: 中文
- description: 专业的测试场景和测试点设计专家。
- target_audience: 测试工程师

## 需求文档（Word 解析或正文）
{doc_section}

## 用户补充说明（可为空）
{query}

## Task
请**仅**依据上述需求文档与补充说明，设计测试场景及每个场景下的测试点。

## 字段要求
- scene_name: 场景名称（简洁明了）
- scene_description: 场景详细描述
- risk_level: high/medium/low（风险等级）
- point_name: 测试点标题（简短、全局唯一便于勾选；**不含**完整测试步骤）
- point_level: P0/P1/P2（重要性，P0最高）
- point_type: functional/performance/security/reliability

## 测试点与用例边界
- 阶段 A 仅输出**标题级**测试点，不要在此输出 test_steps / expected_results
- 完整用例（步骤、预期结果）由阶段 B 在用户勾选后生成

## 场景设计原则
1. 覆盖核心功能、异常流程、边界条件
2. 场景之间相互独立
3. 测试点应具备可执行性
"""


def _format_scene_points_block(points: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for p in points:
        pn = str(p.get("point_name") or "").strip()
        if not pn:
            continue
        lines.append(
            f"- point_name: {pn}\n"
            f"  point_level: {p.get('point_level', 'P1')}\n"
            f"  point_type: {p.get('point_type', 'functional')}"
        )
    return "\n".join(lines)


def _build_scene_cases_prompt(
    scene_name: str,
    points: List[Dict[str, Any]],
    scene_rag_context: str,
    document_context: str = "",
) -> str:
    doc_text = (document_context or "").strip()
    doc_section = f"\n\n## 当前需求文档\n{doc_text}" if doc_text else ""
    rag_section = (
        f"\n\n## 参考文档片段（本场景共享）\n{scene_rag_context}"
        if scene_rag_context
        else ""
    )
    context_section = doc_section + rag_section
    points_block = _format_scene_points_block(points)
    return f"""
# Role: 测试用例生成专家

## Profile
- language: 中文
- description: 根据**标题级测试点**批量展开详细测试用例。

## 场景
- scene_name: {scene_name}
{context_section}

## 待展开测试点（须全部输出对应用例）
{points_block}

## Task
为上述**每一个**测试点各生成一条完整用例；`cases` 数组长度须与测试点数量一致，且每条 `point_name` 须与输入完全一致。

## 字段说明
- point_name: 与测试点标题一致
- point_level: P0/P1/P2/P3（优先级）
- point_type: functional/performance/security/reliability
- preconditions: 前置条件列表
- test_steps: 测试步骤列表（不要加序号，已自动编号）
- expected_results: 预期结果列表（与关键步骤对应）
"""


def _normalize_scene_cases(
    scene_name: str,
    points: List[Dict[str, Any]],
    raw_cases: List[TestCaseOutput],
    case_id_start: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """将 LLM 批量输出对齐到采纳测试点，并分配 case_id。"""
    expected = [str(p.get("point_name") or "").strip() for p in points]
    expected = [n for n in expected if n]
    by_name: Dict[str, TestCaseOutput] = {}
    for item in raw_cases:
        name = str(item.point_name or "").strip()
        if name and name not in by_name:
            by_name[name] = item

    point_meta = {str(p.get("point_name") or "").strip(): p for p in points}
    cases: List[Dict[str, Any]] = []
    missing: List[str] = []
    for i, pn in enumerate(expected):
        src = by_name.get(pn)
        meta = point_meta.get(pn) or {}
        if not src:
            missing.append(pn)
            continue
        case = src.model_dump()
        case["case_id"] = f"TC-{case_id_start + len(cases) + 1:03d}"
        case["scene_name"] = scene_name
        if not case.get("point_name"):
            case["point_name"] = pn
        if not case.get("point_level"):
            case["point_level"] = meta.get("point_level", "P1")
        if not case.get("point_type"):
            case["point_type"] = meta.get("point_type", "functional")
        cases.append(case)

    if missing:
        return cases, f"缺少以下测试点的用例：{', '.join(missing)}"
    return cases, None


def _loads_case_array_string(text: str) -> List[Any]:
    """
    解析 LLM 将 cases 序列化成的 JSON 数组字符串。
    模型偶发产出 `[{...}]}`（对象前多一个 `]`），在此做一次修正后再解析。
    """
    s = text.strip()
    candidates = [s]
    if s.endswith("]}]"):
        candidates.append(s[:-3] + "}]")
    last_err: Optional[json.JSONDecodeError] = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError as exc:
            last_err = exc
            continue
    preview = s[:120] + ("..." if len(s) > 120 else "")
    raise ValueError(f"cases JSON 无法解析: {preview}") from last_err


def _scene_cases_from_tool_args(args: Any) -> SceneTestCasesOutput:
    if not isinstance(args, dict):
        raise TypeError(f"tool args 须为 dict，实际: {type(args)!r}")
    cases = args.get("cases")
    if isinstance(cases, str):
        cases = _loads_case_array_string(cases)
    return SceneTestCasesOutput.model_validate({"cases": cases})


def _scene_cases_from_tool_message(message: AIMessage) -> SceneTestCasesOutput:
    tool_name = SceneTestCasesOutput.__name__
    for tc in message.tool_calls or []:
        if tc.get("name") == tool_name:
            return _scene_cases_from_tool_args(tc.get("args") or {})
    raise ValueError(f"未找到 {tool_name} tool call")


async def _generate_scene_cases(
    scene_name: str,
    points: List[Dict[str, Any]],
    context: str,
    case_id_start: int,
    *,
    document_context: str = "",
    config: RunnableConfig | None = None,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """单场景一次 LLM 调用，批量生成该场景全部采纳测试点的用例。"""
    if not points:
        return [], None
    try:
        llm = get_llm()
        prompt = _build_scene_cases_prompt(
            scene_name, points, context, document_context=document_context
        )
        structured_llm = _structured_output(llm, SceneTestCasesOutput, include_raw=True)
        run_config = dict(config or {})
        run_config.setdefault("run_name", f"case_generate:{scene_name}")
        resp = await structured_llm.ainvoke(prompt, config=run_config)
        result = resp.get("parsed")
        if result is None:
            raw_msg = resp.get("raw")
            if not isinstance(raw_msg, AIMessage):
                parsing_error = resp.get("parsing_error")
                raise ValueError(str(parsing_error) if parsing_error else "structured output 解析失败")
            result = _scene_cases_from_tool_message(raw_msg)
        return _normalize_scene_cases(scene_name, points, result.cases, case_id_start)
    except Exception as e:
        logger.exception(f"[CaseGraph] 场景用例生成异常 scene={scene_name}: {e}")
        return [], str(e)
