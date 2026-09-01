# 决策：harness 彻底与平台解耦（deps 绑定）

状态：implemented
日期：2026-07-25
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** 仅搬目录不够；`domain.attachments` / Langfuse / ORM / `kb` 仍让 noesis 粘平台。目标是评测可只依赖 noesis+config，平台单向注入。

**How to apply：**
- 附件输入适配：`noesis.runtime.attachments.*`；旧 domain 路径已移除
- case VO：`noesis.agents.case_generate.vo`；`schemas.case_generate_vo` shim
- **禁止** noesis → `domain` / `services` / `models` / `api` / `kb`（静态）；LLM 内聚为 `noesis.llm`
- 一律经 `noesis.runtime.deps` + `services.harness_wiring.wire_harness_platform_deps`
- 运行时配置与日志内聚：`noesis.config`、`noesis.runtime.logging`；禁止反向依赖 backend 顶层 `config/common`
- 宿主/评测的稳定公共入口为 `noesis.config` / `noesis.runtime`；二者通过惰性导出避免配置与 logging 的循环初始化，细分模块路径仅用于内部实现和精确 patch。
- 静态检查：`rg 'from (domain|services|models|api|kb)\\.' packages/harness/noesis` 应为空
