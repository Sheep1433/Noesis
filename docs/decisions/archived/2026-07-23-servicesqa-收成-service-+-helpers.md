# 决策：services/qa 收成 service + helpers

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 先前拆成 agents/resolve/persist/stream 过碎，且包内绕 `qa_service` shim 保 patch，设计差。

**How to apply：**
- 仅保留 `services/qa/{__init__,service,helpers}.py`；shim `qa_service.py` 仍兼容旧 import
- `__init__` 只导出 `QaService` + 4 agent 单例
- 去掉包内 `from services import qa_service as qs`；测试 patch 打到 `services.qa.helpers` / `services.qa.service`
