"""
测试用例生成主协调器

使用 LangChain 原生流式输出，事件格式为 dict：
- type: 事件类型
- content: 文本内容
- data: 自定义事件数据

流程：
1. 测试场景+测试点 → LangChain 结构化输出（依赖文档上下文）→ 等待用户勾选
2. 测试用例 → 并行生成（含 RAG） → 完成
"""
import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Dict, List, Optional

from config.checkpointer import get_checkpointer
from langgraph.types import Command

from agent.base.base_agent import DEFAULT_RECURSION_LIMIT
from agent.case_generate.case_graph import TestCaseState, build_test_case_graph
from config.env import LangfuseConfig
from utils.langfuse_tracing import merge_langfuse_runnable_config
from utils.log_util import logger


def _merge_langgraph_chunk(chunk: Any) -> Dict[str, Any]:
    """LangGraph astream 默认产出为 {{node_name: partial_state}}，合并为扁平 state 片段。"""
    if not isinstance(chunk, dict):
        return {}
    if any(
        k in chunk
        for k in ("current_phase", "error", "scenes_testpoints", "test_cases", "selected_point_names")
    ):
        return dict(chunk)
    merged: Dict[str, Any] = {}
    for v in chunk.values():
        if isinstance(v, dict):
            merged.update(v)
    return merged


async def resolve_document_context(file_list: Optional[Dict[str, Any]]) -> str:
    """
    将 file_dict 解析为 document_context。
    - 值为 TEST_CASE_KB_FILE_DICT_REF：从 requirement_docs 按 key(file_name) 拉取整篇
    - 长文本（>800 字符）：内联正文（兼容旧会话 / chat 主路径）
    """
    from agent.case_generate.rag import (
        TEST_CASE_KB_FILE_DICT_REF,
        extract_source_file_names,
        requirement_collection_name,
    )
    from kb.retrieval import KbRetrievalService

    if not file_list:
        return ""

    coll = requirement_collection_name()
    parts: List[str] = []

    for name, val in file_list.items():
        if val is None:
            continue
        file_name = str(name).strip()
        s = str(val).strip()
        if s == TEST_CASE_KB_FILE_DICT_REF:
            body = await asyncio.to_thread(
                KbRetrievalService.fetch_full_document_by_file_name,
                coll,
                file_name,
            )
            if not body.strip():
                logger.warning(
                    f"[CaseCoordinator] 知识库未找到文档 file_name={file_name} collection={coll}"
                )
                parts.append(f"### {file_name}\n（知识库中未找到该文档正文）")
            else:
                parts.append(f"### {file_name}\n{body}")
        elif len(s) > 800:
            parts.append(f"### {name}\n{s}")
        else:
            parts.append(f"### {name}\n（文件引用: {s}）")

    return "\n\n".join(parts)


# ============================================================================
# 事件类型定义
# ============================================================================
# 标准事件
EVENT_FINISH = "finish"
EVENT_ERROR = "error"

# 自定义事件（测试用例生成专用）
EVENT_SCENARIO_START = "scenario-start"
EVENT_TESTPOINT_CONFIRM_WAIT = "testpoints-confirm-required"
EVENT_SCENE_CASES = "scene-cases"

# SSE 阶段事件（对齐 OpenSpec test-case-phase-sse-events；仅限测试用例流）
PHASE_PARSE_REQUIREMENTS = "parse_requirements"
PHASE_GENERATE_TEST_POINTS = "generate_test_points"
PHASE_AWAIT_USER_CONFIRM = "await_user_confirm"
PHASE_PARALLEL_GENERATE_CASES = "parallel_generate_cases"

_PHASE_TITLES: Dict[str, str] = {
    PHASE_PARSE_REQUIREMENTS: "解析需求",
    PHASE_GENERATE_TEST_POINTS: "生成测试点",
    PHASE_AWAIT_USER_CONFIRM: "待确认",
    PHASE_PARALLEL_GENERATE_CASES: "并行生成",
}


class CaseCoordinator:
    """
    测试用例生成主协调器

    职责：
    - 管理 running_tasks（取消任务）
    - 管理 _graph_instances（保存 app 实例供 resume）
    - run_agent()：astream 主循环，发送事件，中断时 return
    - resume_agent()：通过 Command 传值，继续执行
    """

    def __init__(self):
        self.running_tasks: Dict[str, Dict[str, Any]] = {}
        self._graph_instances: Dict[str, Dict[str, Any]] = {}
        # 存储已完成的测试结果（不受 finally 清理影响，供导出接口读取）
        self._finished_results: Dict[str, Dict[str, Any]] = {}

    async def run_agent(
        self,
        query: str,
        conversation_id: Optional[str] = None,
        file_list: dict = None,
        qa_type: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        使用 LangGraph StateGraph 的交互式测试用例生成

        通过 astream 循环处理各阶段：
        - 场景+测试点完成后中断 → 前端只读勾选与二次确认 → resume
        - 用例生成完成后结束

        Args:
            file_list: 可选，文件名 -> 正文或 `__FROM_KB__`（从 requirement_docs 拉整篇）

        Yields:
            dict: 包含 type 及各业务字段
        """
        thread_id = conversation_id or str(uuid.uuid4())
        task_context: Dict[str, Any] = {"cancelled": False}
        self.running_tasks[thread_id] = task_context

        checkpointer = get_checkpointer()
        graph = build_test_case_graph()
        app = graph.compile(
            checkpointer=checkpointer,
            interrupt_before=["generate_test_cases"],
        )

        config_base = {
            "configurable": {"thread_id": f"case_graph_{thread_id}"},
            "recursion_limit": DEFAULT_RECURSION_LIMIT,
        }

        document_context = await resolve_document_context(file_list)
        source_file_names = extract_source_file_names(file_list)

        # 初始状态
        initial_state: TestCaseState = {
            "query": query,
            "document_context": document_context,
            "source_file_names": source_file_names,
            "scenes_testpoints": [],
            "selected_point_names": [],
            "test_cases": [],
            "retrieval_trace": None,
            "current_phase": "scenes_testpoints",
            "error": None,
        }

        cur_sse_phase: Dict[str, Optional[str]] = {"id": None}
        try:
            # 保存 app 实例供 resume 使用
            self._graph_instances[thread_id] = {
                "app": app,
                "config_base": config_base,
                "qa_type": qa_type or "",
                "current_phase": "scenes_testpoints",
                "query": query,
            }

            yield self._sse_phase_start(cur_sse_phase, PHASE_PARSE_REQUIREMENTS)
            n_files = len(file_list) if isinstance(file_list, dict) else 0
            ctx_snip = (document_context or "").strip()
            yield self._sse_phase_delta(
                PHASE_PARSE_REQUIREMENTS,
                f"已从知识库/请求合并需求上下文（{n_files} 个文件字段，约 {len(ctx_snip)} 字符）",
            )
            yield self._sse_phase_end(cur_sse_phase, PHASE_PARSE_REQUIREMENTS, ok=True)

            yield self._sse_phase_start(cur_sse_phase, PHASE_GENERATE_TEST_POINTS)

            yield self._event(
                EVENT_SCENARIO_START,
                {
                    "message_id": thread_id,
                    "message": "正在生成测试场景与测试点…",
                },
            )

            run_config = merge_langfuse_runnable_config(
                dict(config_base),
                langfuse_session_id=thread_id,
                qa_type=qa_type,
                enabled=LangfuseConfig.langfuse_tracing_enabled,
                langfuse_trace_id=thread_id,
            )

            async for raw in app.astream(initial_state, run_config):
                event = _merge_langgraph_chunk(raw)

                phase = event.get("current_phase", "")

                if task_context.get("cancelled"):
                    logger.info(
                        f"[CaseCoordinator] run_agent 检测到 cancelled，结束流 thread_id={thread_id}"
                    )
                    for ev in self._sse_phase_abort_any(cur_sse_phase):
                        yield ev
                    yield self._event(EVENT_ERROR, {"error": "任务已取消"})
                    yield self._event(EVENT_FINISH, {"finish_reason": "stop", "usage": {}})
                    break

                if event.get("error"):
                    for ev in self._sse_phase_abort_any(cur_sse_phase):
                        yield ev
                    yield self._event(EVENT_ERROR, {"error": event["error"]})
                    yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
                    return

                if phase == "testpoints_confirm":
                    scenes = event.get("scenes_testpoints", [])
                    self._graph_instances[thread_id]["current_phase"] = phase
                    self._graph_instances[thread_id]["scenes_testpoints"] = scenes

                    yield self._sse_phase_end(cur_sse_phase, PHASE_GENERATE_TEST_POINTS, ok=True)
                    yield self._sse_phase_start(cur_sse_phase, PHASE_AWAIT_USER_CONFIRM)

                    yield self._event(
                        EVENT_TESTPOINT_CONFIRM_WAIT,
                        {
                            "scenes": scenes,
                            "message": "请勾选要采纳的测试点，确认后在生成用例前进行二次确认",
                        },
                    )
                    yield self._sse_phase_end(cur_sse_phase, PHASE_AWAIT_USER_CONFIRM, ok=True)
                    return

        except asyncio.CancelledError:
            logger.info(f"[CaseCoordinator] run_agent CancelledError thread_id={thread_id}")
            for ev in self._sse_phase_abort_any(cur_sse_phase):
                yield ev
            yield self._event(EVENT_ERROR, {"error": "任务已停止"})
            yield self._event(EVENT_FINISH, {"finish_reason": "stop", "usage": {}})
        except Exception as e:
            logger.exception(f"[CaseCoordinator] 异常: {e}")
            for ev in self._sse_phase_abort_any(cur_sse_phase):
                yield ev
            yield self._event(EVENT_ERROR, {"error": f"协调器异常: {str(e)}"})
            yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
        finally:
            if thread_id in self.running_tasks:
                del self.running_tasks[thread_id]
            # 注意：不清理 _graph_instances，因为 resume_agent 需要访问它

    # =========================================================================
    # Resume：恢复中断的流程
    # =========================================================================

    async def resume_agent(
        self,
        conversation_id: str,
        selected_point_names: Optional[List[str]] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        在用户勾选测试点并经二次确认后，通过 app.astream(Command) 恢复图执行；
        阶段 B 增量事件经 LangGraph custom stream 转发为 scene-cases SSE。
        """
        thread_id = conversation_id
        task_context = self.running_tasks.get(thread_id, {})

        if task_context.get("cancelled"):
            logger.info(
                f"[CaseCoordinator] resume_agent 入口即 cancelled thread_id={thread_id}"
            )
            yield self._event(EVENT_ERROR, {"error": "任务已取消"})
            yield self._event(EVENT_FINISH, {"finish_reason": "stop", "usage": {}})
            return

        app_info = self._graph_instances.get(thread_id)
        if not app_info:
            yield self._event(EVENT_ERROR, {"error": "会话不存在或已过期，请重新开始"})
            yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
            return

        app = app_info["app"]
        config_base = app_info["config_base"]
        qa_type_stored = app_info.get("qa_type") or None
        current_phase = app_info.get("current_phase", "")
        self.running_tasks[thread_id] = {"cancelled": False}

        cur_resume_phase: Dict[str, Optional[str]] = {"id": None}

        try:
            if current_phase == "testpoints_confirm" and selected_point_names is not None:
                if not selected_point_names:
                    yield self._event(EVENT_ERROR, {"error": "请至少选择一个测试点后重试"})
                    yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
                    return

                logger.info(f"[CaseCoordinator] resume 测试点确认，thread_id: {thread_id}")

                total_points = len(selected_point_names)
                total_scenes = len(
                    {
                        str(sc.get("scene_name") or "").strip()
                        for sc in (app_info.get("scenes_testpoints") or [])
                        if str(sc.get("scene_name") or "").strip()
                        and any(
                            tp.get("point_name") in selected_point_names
                            for tp in (sc.get("test_points") or [])
                        )
                    }
                )
                query_stored = app_info.get("query") or ""

                yield self._sse_phase_start(cur_resume_phase, PHASE_PARALLEL_GENERATE_CASES)
                yield self._sse_phase_delta(
                    PHASE_PARALLEL_GENERATE_CASES,
                    f"按 {total_scenes or 1} 个场景批量生成 {total_points} 条用例…",
                )

                test_cases: List[Dict[str, Any]] = []
                completed_scenes = 0

                run_config = merge_langfuse_runnable_config(
                    dict(config_base),
                    langfuse_session_id=thread_id,
                    qa_type=qa_type_stored,
                    enabled=LangfuseConfig.langfuse_tracing_enabled,
                    langfuse_trace_id=thread_id,
                )

                async for mode, chunk in app.astream(
                    Command(
                        update={
                            "selected_point_names": selected_point_names,
                            "current_phase": "test_cases",
                        }
                    ),
                    run_config,
                    stream_mode=["updates", "custom"],
                ):
                    rtc = self.running_tasks.get(thread_id) or {}
                    if rtc.get("cancelled"):
                        logger.info(
                            f"[CaseCoordinator] resume_agent cancelled thread_id={thread_id}"
                        )
                        for ev in self._sse_phase_abort_any(cur_resume_phase):
                            yield ev
                        yield self._event(EVENT_ERROR, {"error": "任务已取消"})
                        yield self._event(EVENT_FINISH, {"finish_reason": "stop", "usage": {}})
                        return

                    if mode == "custom":
                        completed_ref = {"n": completed_scenes}
                        for ev in self._scene_progress_to_sse(
                            chunk,
                            total_scenes=total_scenes or 1,
                            completed_ref=completed_ref,
                            test_cases=test_cases,
                            phase_id=PHASE_PARALLEL_GENERATE_CASES,
                        ):
                            yield ev
                        completed_scenes = completed_ref["n"]
                        continue

                    if mode != "updates":
                        continue

                    event = _merge_langgraph_chunk(chunk)
                    if event.get("error"):
                        for ev in self._sse_phase_abort_any(cur_resume_phase):
                            yield ev
                        yield self._event(EVENT_ERROR, {"error": event["error"]})
                        yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
                        return

                    if event.get("current_phase") == "finish":
                        test_cases = event.get("test_cases") or test_cases
                        self._finished_results[thread_id] = {
                            "test_cases": test_cases,
                            "query": query_stored,
                        }
                        if cur_resume_phase["id"] == PHASE_PARALLEL_GENERATE_CASES:
                            yield self._sse_phase_end(
                                cur_resume_phase, PHASE_PARALLEL_GENERATE_CASES, ok=True
                            )
                        yield self._event(
                            EVENT_FINISH,
                            {"finish_reason": "stop", "usage": {}, "total": len(test_cases)},
                        )
                        return
            else:
                yield self._event(
                    EVENT_ERROR,
                    {"error": "当前会话状态不允许恢复，请重新发起测试用例生成"},
                )
                yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})

        except asyncio.CancelledError:
            logger.info(f"[CaseCoordinator] resume_agent CancelledError thread_id={thread_id}")
            for ev in self._sse_phase_abort_any(cur_resume_phase):
                yield ev
            yield self._event(EVENT_ERROR, {"error": "任务已停止"})
            yield self._event(EVENT_FINISH, {"finish_reason": "stop", "usage": {}})
        except Exception as e:
            logger.exception(f"[CaseCoordinator] resume 异常: {e}")
            for ev in self._sse_phase_abort_any(cur_resume_phase):
                yield ev
            yield self._event(EVENT_ERROR, {"error": f"恢复执行异常: {str(e)}"})
            yield self._event(EVENT_FINISH, {"finish_reason": "error", "usage": {}})
        finally:
            if thread_id in self.running_tasks:
                del self.running_tasks[thread_id]
            if thread_id in self._graph_instances:
                del self._graph_instances[thread_id]

    # -------------------------------------------------------------------------
    # SSE 阶段帧（phase-start / delta / end）
    # -------------------------------------------------------------------------

    def _sse_phase_abort_any(self, cur: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
        """未正常闭合的阶段：补发 phase-end(ok=false)，避免悬空。"""
        pid = cur.get("id")
        if not pid:
            return []
        cur["id"] = None
        return [{"type": "phase-end", "phase_id": pid, "ok": False}]

    def _sse_phase_start(self, cur: Dict[str, Optional[str]], phase_id: str) -> Dict[str, Any]:
        cur["id"] = phase_id
        return {
            "type": "phase-start",
            "phase_id": phase_id,
            "title": _PHASE_TITLES.get(phase_id, phase_id),
        }

    def _sse_phase_delta(self, phase_id: str, text_delta: str) -> Dict[str, Any]:
        return {"type": "phase-delta", "phase_id": phase_id, "text_delta": text_delta}

    def _sse_phase_end(self, cur: Dict[str, Optional[str]], phase_id: str, *, ok: bool) -> Dict[str, Any]:
        if cur.get("id") == phase_id:
            cur["id"] = None
        return {"type": "phase-end", "phase_id": phase_id, "ok": ok}

    def _scene_progress_to_sse(
        self,
        chunk: Any,
        *,
        total_scenes: int,
        completed_ref: Dict[str, int],
        test_cases: List[Dict[str, Any]],
        phase_id: str,
    ) -> List[Dict[str, Any]]:
        """将 LangGraph custom stream 的场景结果映射为 scene-cases SSE（成功/失败统一帧）。"""
        if not isinstance(chunk, dict):
            return []
        kind = chunk.get("kind")
        payload = chunk.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        if kind not in ("scene-cases", "scene-error"):
            return []

        scene_name = str(payload.get("scene_name") or "")
        cases = payload.get("cases") if isinstance(payload.get("cases"), list) else []
        for case in cases:
            if isinstance(case, dict):
                test_cases.append(case)

        completed = completed_ref["n"] + 1
        completed_ref["n"] = completed

        if kind == "scene-cases":
            delta = f"已完成 {completed}/{total_scenes} 个场景：{scene_name}（共 {len(cases)} 条用例）"
            body: Dict[str, Any] = {"sceneName": scene_name, "cases": cases}
        else:
            err = str(payload.get("error") or "生成失败")
            delta = f"已完成 {completed}/{total_scenes} 个场景（{scene_name} 失败）"
            body = {
                "sceneName": scene_name,
                "error": err,
                "pointNames": payload.get("point_names") or [],
                "cases": cases,
            }

        return [
            self._sse_phase_delta(phase_id, delta),
            self._event(EVENT_SCENE_CASES, body),
        ]

    # =========================================================================
    # 取消任务
    # =========================================================================

    async def cancel_task(self, thread_id: str) -> tuple:
        """取消指定的任务，同时清理相关状态"""
        had_running = thread_id in self.running_tasks
        had_graph = thread_id in self._graph_instances
        logger.info(
            f"[CaseCoordinator] cancel_task thread_id={thread_id} had_running={had_running} had_graph={had_graph}"
        )
        if thread_id in self.running_tasks:
            self.running_tasks[thread_id]["cancelled"] = True
        # 清理 graph 实例和已完成结果
        if thread_id in self._graph_instances:
            del self._graph_instances[thread_id]
        if thread_id in self._finished_results:
            del self._finished_results[thread_id]
        if thread_id in self.running_tasks:
            del self.running_tasks[thread_id]
        return True, "停止成功"

    def get_export_markdown(
        self,
        thread_id: str,
        *,
        test_cases: Optional[List[Dict[str, Any]]] = None,
        query: Optional[str] = None,
    ) -> Optional[str]:
        """将会话用例导出为 Markdown；无可导出数据时返回 None。"""
        cases: List[Dict[str, Any]] = list(test_cases) if test_cases else []
        q = (query or "").strip()
        if not cases:
            cached = self._finished_results.get(thread_id)
            if cached:
                cases = list(cached.get("test_cases") or [])
                if not q:
                    q = str(cached.get("query") or "").strip()
        if not cases:
            return None
        return export_cases(cases, q)

    # =========================================================================
    # 辅助方法
    # =========================================================================

    def _event(self, event_type: str, data: Dict[str, Any] = None) -> dict:
        """生成事件 dict"""
        event = {"type": event_type}
        if data:
            event.update(data)
        return event


# ============================================================================
# 导出工具
# ============================================================================


def export_cases(test_cases: List[Dict[str, Any]], query: str = "") -> str:
    """将测试用例导出为 Markdown 报告"""
    import datetime

    lines = [
        "# 测试用例报告",
        "",
        f"**需求**: {query or '未提供'}",
        f"**生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**用例数量**: {len(test_cases)}",
        "",
        "---",
        "",
    ]

    for idx, case in enumerate(test_cases, 1):
        lines.append(f"## {idx}. {case.get('point_name', '未命名')}")
        lines.append("")
        lines.append(f"- **用例编号**: {case.get('case_id', f'TC-{idx:03d}')}")
        lines.append(f"- **优先级**: {case.get('point_level', 'P1')}")
        lines.append(f"- **类型**: {case.get('point_type', 'functional')}")
        lines.append(f"- **场景**: {case.get('scene_name', '')}")

        preconditions = case.get("preconditions", [])
        if preconditions:
            lines.append(f"- **预置条件**: {', '.join(preconditions)}")

        test_steps = case.get("test_steps", [])
        if test_steps:
            lines.append("### 测试步骤")
            for i, step in enumerate(test_steps, 1):
                lines.append(f"{i}. {step}")

        expected_results = case.get("expected_results", [])
        if expected_results:
            lines.append("### 预期结果")
            for result in expected_results:
                lines.append(f"- {result}")

        lines.append("")
        lines.append("---")

    return "\n".join(lines)
