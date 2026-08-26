## Why

Noesis 当前的机器经验记忆只从“工具失败后恢复成功”提取，导致大量没有显式工具失败、或以 partial/error 结束但包含决策、工作流、环境陷阱、用户纠正和验证结果的 Run 永远不会进入记忆。现有读取路径又以向量候选直接生成 action card，缺少文件化 Run 证据、主动证据检索、结构化引用和完整的效果评测，无法证明记忆真正改善后续任务。

本变更将现有 Cortex 重新定义为完整的 Run-aware 经验系统：所有具有稳定持久化证据的终态主 Run 都进入异步处理，原始 Run 证据保持不可变，后台生成有来源的 decision、experience、workflow 和 gotcha；读取时先进行低成本候选筛选，再按需执行有界的主动证据检索，最终只注入带来源的短 Memory Bulletin。

## What Changes

- 将提取入口从“出现工具失败”改为“所有启用记忆且具有稳定证据的终态主 Agent Run”；completed、partial、error 和包含有效工作证据的 interrupted 均可进入，工具失败恢复仅作为一种高置信信号。
- 新增不可变 Run evidence 与派生 memory workspace：保存目标、消息、工具调用、产物、验证、用户纠正、compaction 摘要和 source span；生成 manifest、Run summary、workflow/gotcha/decision 文档供检索与人工审查。
- 将写入拆成 `capture → extract → consolidate → index` 四个可恢复阶段；长 Run 先按消息、工具调用和产物边界做 token-aware 分块，不允许因单次上下文超限静默丢弃整条 Run。
- 将机器记忆统一为 `decision|experience|workflow|gotcha` 四类，并保留 candidate、active、superseded、disabled、invalidated 等状态、scope、有效期、版本、provenance 与 source span。
- PostgreSQL 继续作为权威事实源；文件 workspace 和 Qdrant 均为可重建派生视图。文件提供 manifest/summary/证据导航，Qdrant 只提供语义候选，不参与状态裁决。
- 新增两段式读取：确定性的用户/项目/scope 过滤与混合候选筛选；仅在需要历史证据且低成本结果不足时运行有 timeout、step、token 和 source-span 预算的主动检索器。
- 注入内容从 raw top-k/action card 改为 Memory Bulletin。自动 Bulletin 必须包含可执行结论、适用范围、置信状态和稳定 memory id；source run/span 保存在 private metadata 并由来源工具或 Deep Query 展开，原始工具输出与整段 Run 不得自动注入。
- Memory Bulletin SHALL 采用 cache-aware prompt 装配：稳定 system/tool/history prefix 在前，动态 Bulletin late-insert；可见文本使用稳定序列化且不包含当前 run id、时间、source span、evidence count 等动态字段，同一内容跨调用产生相同 hash/text，并评测 cache-read/write、TTFT 和成本。
- 增加来源信任分类与 recall-loop 防护：用户输入、Agent 推导、工具/外部内容和系统脚手架分别记录；已从记忆召回的内容不得再次作为新记忆证据。
- 保留单一用户 `enabled` 开关，默认关闭，同时控制自动 capture/extraction/consolidation 和新 Run 自动注入；关闭后已有记忆仍可查看、显式搜索、编辑、失效和删除。
- 扩展设置页和 `/api/user/memory/cortex`，展示类型、状态、scope、来源、处理健康、最后整理时间和注入使用情况；后台失败、超限、dead job 与索引延迟必须可诊断。
- 重建离线评测：使用冻结的 Run evidence 数据集分别测提取、冲突修订、检索、Bulletin、端到端任务结果、安全、延迟和成本，并引入 memory-on/off paired A/B。
- 实现 SHALL 先删除旧 experience-only、专用 RecoveryAdapter、失败恢复 identity、旧 raw action-card push 及其表结构、装配和测试，再建立可编译但不提供机器经验功能的空白基线；旧功能未上线，不迁移旧 item/evidence/job/outbox，也不沿用其实现。只保留单一用户 preference 与现有通用 user/scope 鉴权，随后从评测契约重新实现新链路。
- 删除旧自动 Dream/按日记忆方案：`MemoryDreamService`、scheduler、自动补写、`memory/YYYY-MM-DD.md` 数据/API/UI、与按日记忆合并搜索的分支和对应测试均不保留；当前功能未上线，不提供数据迁移或并行兼容。`USER.md` / `AGENTS.md` 作为用户显式维护的上下文继续保留，但新机器经验不得自动修改它们。
- 无破坏性聊天 SSE 变更；内部 provenance、memory workspace 路径、索引信息和后台错误 SHALL NOT 暴露到用户可见 SSE、聊天历史或工具卡片。

## Capabilities

### New Capabilities

- `agent-memory-cortex`: 终态 Run capture、不可变证据、四类机器记忆、异步提取与整理、派生 workspace/index、主动证据检索、Memory Bulletin、状态治理和可观测性。

### Modified Capabilities

- `agent-runtime`: 记忆工具改为证据优先读取；Runtime 增加按 Run 冻结的 Bulletin，并提供受预算限制的主动证据检索。
- `agent-tool-failure-handling`: ToolPart 的内部 provenance 与结构化 outcome 继续作为 Run evidence，但不再决定是否创建记忆任务。
- `user-settings`: 删除按日记忆/Dream 设置与 API，设置页和 `/api/user/memory/cortex` 只管理四类机器记忆、单一用户开关、来源和处理健康；保留用户显式维护的 `USER.md` / `AGENTS.md`。
- `offline-evals`: 增加 Run evidence、提取、整理、检索、Bulletin、端到端收益和安全的分层评测。

## Impact

- 后端核心：`noesis.services.memory`、`noesis.repositories.memory_repository`、Run 终态编排、compaction、memory tools、Agent middleware、PostgreSQL ORM/Alembic、Qdrant worker 和服务端管理的 memory workspace。
- Runtime：新 Run 只自动注入有界 Bulletin；深度证据检索为受限工具/子流程，不在每次普通模型调用前强制执行。
- HTTP：扩展 `/api/user/memory/cortex` 路由组；继续使用 Cookie Session + CSRF、`noesis.schemas`、Service 鉴权与统一响应，不改变聊天 SSE 客户端契约。
- 前端：删除旧 Dream/按日记忆区域；设置页 Memory 区展示用户显式上下文和四类机器记忆、证据、状态、处理健康与治理操作；用户仍只配置一个经验记忆开关。
- 数据：删除未上线的 experience-only 表定义、`memory/YYYY-MM-DD.md` 运行时数据和对应索引；从空模型新增通用 memory item/evidence/snapshot/job/outbox，并新建机器经验 workspace 与索引。
- 评测：新增固定 fixture、人工标注 gold source span、paired A/B 和独立 dev/test 参数冻结；默认测试禁止调用真实外部模型、向量服务或远程工具。
- 依赖：复用 PostgreSQL、Qdrant、现有 embedding/LLM client 和文件服务，不引入图数据库或外部记忆服务。
