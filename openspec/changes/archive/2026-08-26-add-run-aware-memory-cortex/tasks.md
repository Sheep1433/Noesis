## 1. 先删除旧机器经验实现并建立空白基线

- [x] 1.1 盘点并删除未上线的 experience item/evidence/job/outbox 表定义与运行数据，不导出迁移快照；只保留独立的单一 preference 和现有通用 user/scope 鉴权
- [x] 1.2 删除 `RecoveryAdapter`、failure pairing/identity/resolution、experience-only Extractor/Revision/Retriever 和旧 action-card renderer 源码，不保留 legacy module、兼容 wrapper 或版本 flag
- [x] 1.3 删除旧 completed-only/failure-only job 创建分支、scheduler/worker 装配和 `MemoryInjectionMiddleware` action-card 接入，确保 Runtime 不再自动 capture 或注入旧经验
- [x] 1.4 删除 `MemoryDreamService`、Dream scheduler/自动补写、按日整理 prompt、`memory/YYYY-MM-DD.md` 数据/API/UI/index/search 分支和测试，不提供兼容开关或迁移；只保留 `USER.md` / `AGENTS.md` 显式编辑能力
- [x] 1.5 删除旧 Cortex item API/UI 字段、旧状态文案和只验证旧语义的 eval/tests；保留单一用户 preference
- [x] 1.6 删除旧 experience-only job/outbox primitives 和 fixtures；新版通用 lease/claim token/outbox 在第 3、6 组按新版状态机从零实现
- [x] 1.7 增加 removal baseline 测试和静态扫描：应用可导入、后端可启动，`USER.md` / `AGENTS.md` 与其它非机器记忆能力通过，旧 Dream/L2/adapter/action-card/failure-only hook/双 worker/兼容 flag 不存在或不可达
- [x] 1.8 在 removal baseline 通过前 SHALL NOT 开始第 3 组及后续新业务实现；将删除与空白基线作为独立提交/检查点，便于判断后续新增行为

## 2. 先建立新版评测契约和 Gold 数据

- [x] 2.1 定义版本化 RunMemory fixture schema：capture eligibility、四类 gold item、scope、source spans、revision operation、自动注入资格和后续任务
- [x] 2.2 建立 completed、partial、error、有效 interrupted、无有效工作取消、无失败成功、决策变化、用户纠正、失败恢复、workflow/gotcha、长 Run/compaction、大输出、无价值和安全样本
- [x] 2.3 实现 capture/chunk coverage、silent drop、extraction precision/recall/type/source-span/no-output 和 consolidation operation 指标接口
- [x] 2.4 实现 exact/near retrieval、span recall、abstention、Bulletin precision、reader error、latency/steps/spans/token、cache-read/write/uncached/availability 和 paired A/B 报告接口
- [x] 2.5 将 release gate 阈值、dev/test split、模型/embedding/prompt/schema/参数/seeds 冻结进版本化 eval config；默认只使用 fake/fixture
- [x] 2.6 为尚未实现的 pipeline 建立 expected-fail/contract tests，确保后续每一层可以独立判断是否达标

## 3. 通用数据模型

- [x] 3.1 将 `t_memory_item` 扩展为 `decision|experience|workflow|gotcha`，增加 statement/applicability/subject、scope、effective provenance、版本、有效期与 needs_review 约束
- [x] 3.2 新增 `t_memory_run_snapshot` 与 relation 模型，落实 `UNIQUE(run_id)`、source watermark、digest、chunk/coverage、retention 和 user/scope 索引
- [x] 3.3 扩展 `t_memory_evidence` 支持 message/tool/artifact/chunk/user_revision source span、span digest 和多 item↔多 Run 关系
- [x] 3.4 扩展 job phase/result、workspace/index outbox 和健康聚合，使用通用可靠性 primitives
- [x] 3.5 编写 Alembic migration：从空模型创建新 item/evidence/snapshot/job/outbox，保留独立 preference，不读取旧 experience 数据
- [x] 3.6 补空库升级、回滚、约束和迁移测试，验证同 user/scope/type/subject 只有一个 current item 且 relation 不分叉/自引用

## 4. 终态 Run Capture 与不可变 Snapshot

- [x] 4.1 在 `completed|partial|error|interrupted` 权威终态按单一用户 preference、稳定工作证据和 `run_id` 幂等创建 capture job
- [x] 4.2 排除 `hitl_pending`、无有效工作取消、subagent 和内部 memory Run，验证 HITL resume 同 `run_id` 只创建一次
- [x] 4.3 实现 RunSnapshotBuilder，收集用户目标/纠正、assistant 可见结论、ToolPart outcome、产物/变更、验证、compaction span 和已召回 memory ids
- [x] 4.4 实现 provenance 分类、derived taint 的最低信任传播和 recall-loop 排除，过滤 system/reasoning/重复 SSE/内部整理内容
- [x] 4.5 实现 snapshot digest、source watermark、schema version、source span、软删除来源、retention 和账户删除清理
- [x] 4.6 补成功/部分/失败/中断、无工具、显式 remember、compaction、内部 Run、外部内容转述和 recall-loop capture 测试

## 5. Token-aware Chunking 与四类 Extractor

- [x] 5.1 实现按 user correction、assistant decision、tool+outcome、artifact+validation、compaction 边界分块和稳定 chunk id/token estimate
- [x] 5.2 对超大工具输出保留结构化 outcome、安全摘录、digest 和来源指针，禁止截断到半个 message/JSON 或只取固定首尾
- [x] 5.3 在 `noesis.schemas.memory` 定义四类 candidate JSON schema、Field descriptions、字段限长、source ref、provenance 和脱敏规则
- [x] 5.4 实现 chunk 并行 extraction、单 chunk 重试、`succeeded_no_output|partial|failed|dead` 和 coverage 统计
- [x] 5.5 实现跨 chunk deterministic merge，模型不得覆盖 user/scope/state/id，candidate effective provenance 取 supporting evidence 最低信任等级
- [x] 5.6 补 decision、experience、workflow、gotcha、无价值、伪造 source id、敏感内容、长 Run 中部证据和部分 chunk 失败测试
- [x] 5.7 记忆维度扩展为任务经验 + 用户上下文：抽取 prompt v7 将用户陈述的持久个人目标/偏好/背景纳入 decision 正向案例（用户证据即可），瞬时闲聊不提取；dev fixture 补用户学习目标与任务内偏好两个真实场景

## 6. Consolidation、状态机与后台可靠性

- [x] 6.1 实现 canonical identity `(user_id, scope_key, memory_type, subject_key)`；project key 规则固定为「带 origin 的沙箱仓库 → origin digest；其余（含无 origin 沙箱仓库）→ global」，并补跨会话死胡同 scope 回归测试
- [x] 6.2 对非 Git 自动生成的 global item 默认保持 candidate/pull-only，仅用户明确确认全局适用后可 active
- [x] 6.3 实现 identity advisory lock、当前 item/有界近邻读取和 `ADD|REINFORCE|UPDATE|SUPERSEDE|CONTRADICT|NOOP` 裁决
- [x] 6.4 首版 consolidation 不调用自由模型；只按受限 operation/evidence refs 确定性裁决，向量只提供候选，不得单独决定 UPDATE/SUPERSEDE
- [x] 6.5 实现 candidate/active/needs_review/superseded/disabled/invalidated、有效期、关系、独立 Run evidence 和 user revision
- [x] 6.6 实现 capture/extract/consolidate/workspace_sync/index_sync 阶段，复用 `SKIP LOCKED`、attempts、lease、claim token、续租、timeout 和 fencing
- [x] 6.7 实现持久化上阶段结果复用、partial/dead reaper、`skipped_disabled`、健康计数和独立 retention cleanup
- [x] 6.8 补跨项目/全局、重复强化、用户纠正、证据冲突、外部命令、disabled/invalidated、并发、崩溃窗口、关闭开关和 cleanup 测试

## 7. 派生文件 Workspace 与 Qdrant

- [x] 7.1 在 `noesis.config` 定义服务端管理、按 user/scope digest 隔离且不写入项目仓库的 memory workspace 根
- [x] 7.2 从 PostgreSQL desired state 生成 `manifest.json`、`memory_summary.md`、四类 memory 文档和受限 Run summaries
- [x] 7.3 artifact evidence 只保存逻辑路径、type、digest、size、状态、验证和有界 diff/summary，不写二进制、完整大 diff 或服务端绝对路径
- [x] 7.4 实现 workspace 结构验证、临时文件、atomic replace、outbox claim/fencing、外部修改不回写和全量重建
- [x] 7.5 更新 `noesis_memory` embedding/payload 支持四类 item、scope、effective provenance、validity 和 template version
- [x] 7.6 实现 Qdrant desired-state upsert/delete、乱序/重复收敛、collection 丢失和 version 全量重建
- [x] 7.7 补路径隔离、敏感内容、迟到事件、删除后旧 upsert、索引断开、旧 worker fencing 和重建测试

## 8. Fast Bulletin 与只读 Deep Query

- [x] 8.1 实现当前 Run query、manifest/lexical 与 semantic candidate 合并、bounded overfetch 和 PostgreSQL user/project/profile/status/validity/provenance/evidence 过滤
- [x] 8.2 实现零额外生成调用的确定性 Bulletin renderer，模型可见内容只包含结论、适用范围、验证状态、memory id 和≤500 token 预算；source span 留在 private metadata
- [x] 8.3 实现 canonical Bulletin serializer 与 `bulletin_hash`：稳定排序/字段/空白/转义，排除当前时间/run id/source span/evidence count/last verified/随机值
- [x] 8.4 排除 candidate/needs_review/history/cross-project/global-unconfirmed/low-trust-command/raw evidence 自动注入
- [x] 8.5 实现只读 MemoryQueryService 与 `search_manifest|search_memory_items|read_run_span|read_artifact_summary|get_memory_source` 工具
- [x] 8.6 限制 user/scope、steps、timeout、token、concurrency、returned spans；禁用网络、业务写工具、外部 MCP、shell 写入和递归 capture
- [x] 8.7 定义 deep query 输出 `bulletin|memory_ids|source_spans|evidence_status`，支持 exact/near-match/contradicts/insufficient
- [x] 8.8 补低分零注入、多跳、时间、workflow、错误前提、超时部分结果、无证据 abstain、外部命令和跨 scope 攻击测试

## 9. Runtime、Memory Tools 与 Run 稳定性

- [x] 9.1 将 `run_id`、user、agent profile 和 canonical project key 经 RunService → agent factory → middleware stack 显式传递
- [x] 9.2 新建 Bulletin middleware/private state，不恢复旧 action-card 实现；同 Run 冻结、新 Run 刷新、subagent 不继承
- [x] 9.3 调整 PromptAssembler：稳定 system/developer/tool/history prefix 在前，单一动态 Bulletin late-insert，Deep Query 只作为后续 tool result
- [x] 9.4 复用 usage normalization 记录 cache-read/cache-write/uncached input/TTFT，provider 缺失 cache details 时记录 unavailable
- [x] 9.5 扩展 `search_memory` schema/service/tool 只检索四类 item、project scope、history 状态和按需 Deep Query，删除 L2/按日兼容参数与分支
- [x] 9.6 扩展 `get_memory_source` 读取有限 message/tool/artifact/chunk span，处理来源删除/retention 且剥离 provider/路径/敏感内容
- [x] 9.7 在 embedding/workspace/Qdrant/PG/query controller 故障时零自动注入或返回可解释搜索错误，Agent Run 继续且不放宽门槛
- [x] 9.8 补同 Run、新 Run同内容/变化内容 cache hash、HITL 跨进程、subagent、用户关闭、source 删除、错误前提和依赖降级测试
- [x] 9.9 保持稳定前缀布局以支持 provider 自动 prefix cache，经四类 cache 场景评测验证；显式 breakpoint 标记层经用户决策移除（暂无 Anthropic 类 provider 诉求），未来接入时再单独提案

## 10. API、设置页与用户治理

- [x] 10.1 在 `noesis.schemas.memory` 定义 preference、四类 item/revision、evidence/source、processing health 和列表/过滤 schema
- [x] 10.2 扩展 `/api/user/memory/cortex` preference、item list/detail/update、source/evidence、disable/enable/invalidate/delete 和 health API，经 Service、Cookie Session、CSRF 与统一响应
- [x] 10.3 实现用户编辑生成 `user_revision` 新版本、重复操作幂等、越权 404、删除级联 relation/evidence/outbox 和来源不可用
- [x] 10.4 更新前端 memory API/types，展示 type/status/project scope、statement/applicability、evidence count、last verified、version 和 source
- [x] 10.5 删除设置页 Dream/按日文件/日期整理/自动补写/L2 搜索区域，保留 `USER.md` / `AGENTS.md` 原文编辑和新的机器经验区域
- [x] 10.6 展示最近 capture/consolidation、pending/partial/failed/dead/skipped、workspace/index lag，不泄露表名/token/provider/path
- [x] 10.7 只保留单一“经验记忆”开关；开启控制自动链路，关闭保留查看/搜索/治理，重新开启不回放关闭期间历史
- [x] 10.8 补 API 401/CSRF/越权/schema/状态冲突和前端显式文件/经验列表/删除确认/健康/错误文案测试，并运行 `product-facing-copy-audit`（行为测试：`frontend/__tests__/memorySettingsBehavior.test.ts`；copy audit：`backend/tests/test_memory_copy_audit.py`）

## 11. 完整评测与 Release Gate

- [ ] 11.1 运行 capture/extraction/consolidation 分层评测，达到 coverage=1.0、silent drop=0、precision≥0.85、recall≥0.80、source span≥0.90、operation accuracy≥0.85（冻结 test 的 source-span recall=0.8462，未通过；2026-08-24 起 OpenCode Zen 免费池触发 FreeUsageLimitError，dev/test live 重跑被外部配额阻断，配额恢复后按「dev 分析→prompt 改进→冻结 test→release_gate」顺序执行）
- [x] 11.2 运行 retrieval/Bulletin 评测，达到 exact evidence recall@5≥0.80、precision@5≥0.70、fast p95≤500ms、自动 Bulletin≤500 tokens
- [x] 11.3 运行 cross-user/project、stale/disabled、low-trust command、recall-loop、关闭/删除残留，所有零容忍安全 gate 为 0
- [x] 11.4 运行冻结 snapshot 的 paired memory-on/off，报告任务成功率、重复失败率、工具调用、token、TTFT、query/background 成本和 95% CI
- [x] 11.5 运行同 Run、新 Run同 Bulletin、新 Run变化 Bulletin、Deep Query 四类 cache 场景，报告 hash/text、cache-read/write/uncached tokens、availability、TTFT 和成本
- [x] 11.6 验证任务成功率差值 95% CI 下界≥-2pp，且任务成功率提升或重复失败率下降至少一项的 95% CI 排除零；未通过 SHALL 停止可启用状态并修正对应层
- [x] 11.7 保存 dev/test 数据版本、模型/embedding、prompt/schema、阈值、seeds、命令、成本和完整报告；不得 test 后调参重报

## 12. 验证、审查与长期文档

- [x] 12.1 运行 memory 相关后端单元/集成/eval、完整 `cd backend && uv run pytest tests/ -q`，并停止本次启动的 worker/server/临时进程
- [x] 12.2 运行前端影响范围测试、`pnpm lint` 和 `pnpm build`
- [x] 12.3 更新 `docs/architecture/platform/agent-memory.md` 为实现后的唯一自动记忆方案，明确旧 Dream/L2 已删除，以及终态 capture、状态机、workspace/index、cache-aware prompt、读取分级、故障降级和回滚
- [x] 12.4 更新数据库迁移与部署说明（`deploy/README.md`「经验记忆迁移与恢复」+ `docs/architecture/platform/agent-memory.md`），验证空库、禁用用户、partial/error Run 和索引丢失恢复
- [x] 12.5 使用 `code-review` 同时检查仓库规范与本 change specs，修复需求遗漏、范围扩大、浅包装、重复逻辑和安全问题
- [x] 12.6 使用静态扫描确认 OpenSpec artifacts 和用户可见文案不含外部产品名称或“参考/仿照某产品”描述
- [x] 12.7 运行 `openspec validate add-run-aware-memory-cortex --strict`，确认每个 Scenario 可定位到测试/eval，并保存可复现验证记录
