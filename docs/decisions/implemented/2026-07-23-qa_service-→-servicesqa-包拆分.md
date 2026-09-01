# 决策：qa_service → services/qa 包拆分

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 单文件 ~1700 行难维护；按职责拆成 facade 包，行为不变。

**How to apply：**
- 新包：`services/qa/{agents,resolve,persist,stream,service}.py` + `__init__.py` facade
- `services/qa_service.py` 仅兼容 re-export（含 `AsyncSessionLocal` / `ChatService` / `LangfuseConfig` 供 patch）
- `QaService._active_streams` ≡ `stream.ACTIVE_STREAMS`（同一 dict）
- 跨模块调用经 `from services import qa_service as qs` lazy 解析，保留 `patch("services.qa_service._persist_assistant")` 等路径
