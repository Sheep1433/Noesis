## Context

Noesis 已有用户显式维护的长期上下文、未上线的自动 Dream/按日记忆方案和一套已实现但不可采用的 Cortex Phase 1。旧 Dream 会把历史消息整理为 `memory/YYYY-MM-DD.md`；旧 Cortex 只处理 completed 主 Agent Run 中的“工具失败 → 后续恢复”，并把 active experience 通过 Qdrant 召回后渲染为 action card。两条自动链路会重复提取、形成多个事实来源并妨碍效果判断，因此都必须在新实现前删除。由于均未上线，旧 item/evidence/job/outbox 及运行时数据不迁移，旧可靠性代码也不沿用；只保留独立的单一用户 preference 和现有通用 user/scope 鉴权。

新的系统必须服务 Noesis 的真实任务：长 Run、多个工具、代码/文档产物、用户纠正、HITL、compaction、跨 Session 项目工作和多用户隔离。记忆的价值不只来自失败；成功工作流、关键决策、环境限制、验证命令和被用户纠正的默认行为同样会改变下一次任务。

实现跨越以下入口：

- Run 终态：`backend/packages/noesis-core/src/noesis/services/run_service.py`
- Run/消息投影：`backend/packages/noesis-core/src/noesis/chat/runs/`、`noesis/chat/message_builder.py`
- compaction：`noesis/services/compaction_service.py`、`noesis/agents/middlewares/compaction_middleware.py`
- memory domain/service：`noesis/services/memory/`
- repository/ORM：`noesis/repositories/machine_memory_repository.py`、`noesis/storage/postgres/models/memory.py`
- Runtime middleware/tools：`noesis/agents/middlewares/memory_bulletin_middleware.py`、`noesis/agents/tools/memory_tools.py`
- 设置 API/schema：`backend/server/api/user_settings_api.py`、`noesis/schemas/memory.py`
- Qdrant 生命周期：现有 knowledge runtime/factory 与 memory index worker
- 离线评测：`backend/evals/memory_cortex/`

约束：

- API 必须遵守 `API → Service → repository/ORM`；外部 schema 位于 `noesis.schemas`。
- PostgreSQL 是机器记忆权威事实源；Qdrant 与文件 workspace 必须可重建。
- 记忆失败不得阻塞聊天回复或破坏 completed Run 的权威终态。
- 用户只配置一个 `enabled`；不保留平台总开关或整理/注入双开关。
- 当前工作区已有旧实现；新实现必须先建立无旧表、旧 worker、旧 middleware 和旧运行数据的空白基线。

## Goals / Non-Goals

**Goals:**

- 对每个启用记忆、进入权威终态且具有稳定持久化证据的主 Agent Run 建立不可变、可寻址的 evidence snapshot，不依赖最终成功或是否发生工具失败。
- 从 Run 中提取 `decision|experience|workflow|gotcha` 四类候选，并让每条结论能回到 source Run/message/tool/artifact span。
- 使用 token-aware chunking、结构化输出、确定性校验和 `succeeded_no_output`，让长 Run 不因一次上下文超限静默丢失。
- 用异步 job 将 capture、extract、consolidate、index 分开，支持多实例、重试、fencing、dead-letter 和处理健康查询。
- 以 PostgreSQL 保存类型、scope、版本、状态、有效期、关系和 provenance；生成可审查的文件 workspace 与可重建语义索引。
- 提供低成本的 manifest/summary/混合检索，并在确有历史证据需求时运行有界主动检索，输出带引用的 Memory Bulletin。
- 防止跨用户/跨项目污染、外部内容晋升为指令、recall-loop、stale memory 和原始工具输出直接注入。
- 让用户查看、搜索、来源追溯、编辑、禁用、启用、失效和删除机器记忆，并能看到后台处理失败。
- 在实现前冻结可复现的分层评测与 release gate，分别判断覆盖、提取、整理、检索、上下文、安全、成本和端到端收益。

**Non-Goals:**

- 自动修改用户维护的 `USER.md`、`AGENTS.md`；它们继续作为显式上下文，但不属于机器经验写入目标。
- 保留旧 Dream、按日记忆、`memory/YYYY-MM-DD.md`、自动补写、独立 scheduler/API/UI 或其历史兼容读取路径。
- 把知识库文档、Skill 定义、当前 checkpoint 或完整聊天历史重定义为机器记忆。
- 自动生成并安装 Skill、修改 Agent prompt、更新模型权重或执行在线训练。
- 引入图数据库；关系先保存在 PostgreSQL。
- 将 subagent 独立内部轨迹默认升级为跨 Session 记忆；首版只处理主 Run 可见的消息、工具与产物。
- 在每个普通模型调用前同步运行深度检索 Agent。
- 保证 exactly-once 外部模型调用；系统保证持久化结果和状态迁移幂等。
- 为旧 experience-only 和 raw action-card push 保留并行兼容方案。

## Decisions

### 1. 具有稳定证据的终态 Run 是统一 capture 入口

主 Agent Run 进入 `completed|partial|error|interrupted` 权威终态且存在至少一项稳定工作证据时自动创建 capture job。稳定工作证据包括持久化 assistant 结论、终态 ToolPart、产物/文件变更、验证结果或用户纠正。`hitl_pending` 不是终态，不创建；用户在任何有效工作产生前取消导致的 interrupted Run 不创建。HITL resume 沿用同一 `run_id`，最终只创建一次。用户显式要求记住时，系统可以在当前 Run 终态后给候选增加 `explicit_user_request` 信号，但不绕开 evidence、scope 和安全检查直接写 active。

eligible terminal Run 的定义不依赖工具数量、工具是否失败或最终是否成功。capture 至少记录：

- 用户目标与后续纠正；
- 可见 assistant 结论；
- ToolPart 的结构化 state/outcome、参数摘要和内部 provenance；
- 产物引用、文件变更摘要与验证结果；
- compaction 摘要及其覆盖范围；
- Run/session/message/tool/artifact 标识与时间；
- 已注入 memory ids，供 recall-loop 排除。

备选方案是只对 completed、失败恢复、显式 remember 或高 importance Run 创建任务。该方案会漏掉有价值的失败/部分轨迹或重复旧覆盖缺口，且 importance 在 extraction 前无法可靠判断，因此不采用。

### 2. Run snapshot 与 memory evidence 分开

新增 `t_memory_run_snapshot`，一条 eligible Run 至多一行：

- `run_id`、`user_id`、`session_id`、`scope_key`；
- `source_updated_at`、`captured_at`、schema version、content digest；
- normalized evidence blob 或服务端文件路径；
- source token estimate、chunk count、capture status；
- processing status 与安全错误摘要。

snapshot 保存用于 extraction 的稳定输入。它不是用户可直接编辑的长期 memory，也不会自动注入。`t_memory_evidence` 继续保存“某个 memory item 由哪些 snapshot/span 支持”，一个 item 可以由多个 Run 支持，一个 Run 可以支持多个 item。

source span 使用结构化坐标：`message:{message_id}`、`tool:{tool_call_id}`、`artifact:{artifact_id}`、`chunk:{chunk_id}`，并保存 span digest。来源对象软删除后，item 保留但来源接口返回不可用；用户删除 memory 或账户时按现有隐私规则清理对应 snapshot/evidence。

备选方案是直接把全部消息复制到每条 item 的 JSONB。它会造成大规模重复、删除语义复杂且难以重建，因此不采用。

### 3. 长 Run 使用结构边界分块，不做整段 all-or-nothing 提取

capture 先过滤系统脚手架、已召回记忆块、重复 SSE 片段和不需要的超大原始输出，再按以下优先级分块：

1. 用户目标/纠正；
2. assistant 决策与最终结论；
3. tool call + outcome + 相邻解释；
4. artifact/文件变更 + 验证；
5. compaction 覆盖段。

每个 chunk 有独立 token 预算和不可伪造的 `chunk_id`。超限工具输出只保留结构化 outcome、安全摘录、digest 和来源指针；不会截断到半个 JSON/message。多个 chunk 并行提取后，由 deterministic merge 输入 consolidator；单个 chunk 失败只影响该 chunk，并在 coverage 指标中显式记录。模型异常以安全异常类别记录并在 retry 间 backoff；用户 goal/correction 与内部 validation 同时存在的高信号 chunk 若首轮为空，只做一次 targeted recheck，仍为空则作为已确认 no-output，不触发 job failure。ordered steps + validation + stop rule 由代码确定性归为 workflow，而不是因用户明确选择而误标 decision。

job 结果区分：

- `succeeded`：产生候选；
- `succeeded_no_output`：证据完整但没有持久价值；
- `partial`：部分 chunk 成功，需重试或人工查看；
- `failed`：没有可用结果；
- `dead`：超过尝试次数。

备选方案是固定首尾截断。它容易丢失长 Run 中部的失败/修复/验证链，不采用。

### 4. 机器记忆只保留四种可验收内容

| 类型 | 内容 | active 的最低证据 |
|---|---|---|
| `decision` | 已确认选择、原因、适用范围、替代/撤销信息；用户陈述的持久个人目标/兴趣/背景/输出偏好（记忆维度同时覆盖任务经验与用户上下文） | 用户确认、用户陈述的持久目标/偏好，或完成产物/验证明确体现选择 |
| `experience` | 一次任务的目标、关键尝试、结果和验证 | eligible terminal Run + 可定位 outcome/结果证据 |
| `workflow` | 可复用步骤、顺序、验证和 stop rule | 至少一次完整验证；高风险流程需要重复证据或人工确认 |
| `gotcha` | 环境限制、常见误区、失败前提和规避条件 | 失败/纠正/环境差异证据，且适用范围明确 |

LLM 只能返回受限 candidate schema：`type`、`subject`、`statement`、`applicability`、`evidence_refs`、`confidence_reason`、`proposed_relation`。代码负责：

- 校验所有 evidence ref 属于当前 snapshot；
- 计算 canonical `subject_key` 和 content digest；
- 根据来源与验证信号决定 candidate/active/needs_review；
- 转义和限长展示字段；
- 拒绝密钥、角色标记、外部指令和无证据结论。

不增加更多 memory type。用户偏好继续由现有用户记忆管理；知识库内容继续由 knowledge 模块管理。

### 5. provenance 和 scope 是确定性资格条件

每个 snapshot/span 带来源类别：

- `user`：当前用户明确输入；
- `assistant_derived`：Agent 根据用户/项目证据生成；
- `tool_internal`：受控本地工具、测试和数据库结果；
- `tool_external`：网页、远程 MCP、第三方内容；
- `system`：系统提示、heartbeat、scheduler、框架脚手架；
- `memory_recall`：从既有记忆注入或显式召回的内容。

`system` 和 `memory_recall` 不得成为新 candidate 的语义证据。`tool_external` 可以支持事实观察，但不得单独产生可自动注入的命令式 workflow/gotcha；必须有用户确认或受控验证。assistant 对外部工具内容的转述 SHALL 继承所引用 evidence 中最低信任等级，不能仅因写进 assistant 文本就升级为 `assistant_derived` 高信任来源。candidate/item 的 effective provenance 由全部 supporting evidence 的最低信任等级确定，来源类别存于结构化列，不从 prose 解析。

scope 至少包含：

- `user_id`；
- `agent_profile`；
- `project_key`，优先使用规范化 repository identity，没有仓库时使用受控 workspace key；
- 可选 `tool_provider_key` 和 `environment_key`。

检索先做 user/project/profile 的确定性 eligibility，再做相关性排序。跨项目内容只允许显式搜索，默认不自动注入。用户级规则与项目级经验不能共享同一 subject identity。

### 6. consolidation 使用确定性候选集和有限模型裁决

memory item 状态为：`candidate|active|superseded|disabled|invalidated|needs_review`。delete 为物理/软删除操作，不作为可召回状态。

consolidation 对 `(user_id, scope_key, memory_type, subject_key)` 获取 transaction advisory lock，读取当前 item 和有界语义近邻，再执行：

- `ADD`：无匹配当前项；
- `REINFORCE`：同结论增加独立 evidence 和 last_verified_at；
- `UPDATE`：同 subject 的表述/适用范围被新证据补充；
- `SUPERSEDE`：用户纠正、决策变化或有效事实变化；
- `CONTRADICT`：证据冲突但不足以判定新旧，进入 needs_review；
- `NOOP`：重复、低价值或没有新证据。

向量相似度只用于生成有限候选集，不能单独决定 UPDATE/SUPERSEDE。模型只能在代码提供的 candidate ids 和 operation enum 中裁决，并必须引用 supporting evidence。用户禁用/失效状态优先于自动裁决；自动任务不能复活 disabled/invalidated item。

关系首版只实现 `supersedes`、`contradicts`、`derived_from`、`applies_to`，保存在 PostgreSQL；不引入图数据库。

### 7. PostgreSQL 是事实源，文件 workspace 与 Qdrant 是派生视图

PostgreSQL 保存 item、evidence、snapshot、job、relation、user preference 和 index/workspace outbox。任何状态变化与 outbox 在同一事务提交。

服务端管理的派生 workspace 由 `noesis.config` 确定根目录，使用用户 id 和 scope digest 隔离，不写入用户项目仓库：

```text
memory-workspaces/{user_id}/{scope_digest}/
├── manifest.json
├── memory_summary.md
├── memories/
│   ├── decisions.md
│   ├── experiences.md
│   ├── workflows.md
│   └── gotchas.md
└── runs/{run_id}.md
```

文件只包含安全摘要、memory ids、source span 引用和检索 handles，不复制大工具输出或密钥。workspace 每次从 PostgreSQL desired state 重建/增量同步；写入使用临时文件、结构验证和 atomic rename。它不是 ORM 的第二事实源，用户通过 API 修改 memory 后由 outbox 重建文件。

Qdrant 继续使用稳定 memory id 作为 point id，保存 active item 的 embedding 与 scope payload。worker 每次重读 PostgreSQL desired state：active upsert，其他状态/不存在 delete。embedding/template version 变化通过全量重建，不保留双索引运行方案。

### 8. 读取采用 fast bulletin 与 bounded evidence query

#### Fast path

新 Run 开始时，Runtime 使用当前用户请求、agent profile 和 project key 查询：

1. PostgreSQL/manifest 的 lexical handles；
2. Qdrant semantic candidates；
3. PostgreSQL 状态、scope、validity、provenance 和 evidence 权威过滤；
4. 去重和总 token 预算。

fast path 不额外调用生成模型。它从 active item 的结构化字段确定性渲染 Memory Bulletin：

```text
结论 + 适用范围 + 验证/置信状态 + [memory_id]
```

只有高于冻结阈值、来源合格且与当前 project/profile 匹配的 item 才可自动注入。candidate、needs_review、disabled、invalidated、superseded、外部内容生成的命令和原始 Run span 不自动注入。

#### Deep path

当用户明确询问历史、当前任务需要多跳/时间/工作流证据，或 fast path 没有强命中时，`search_memory` 可以调用受限 `MemoryQueryService`。该服务只拥有只读工具：

- `search_manifest`；
- `search_memory_items`；
- `read_run_span`；
- `read_artifact_summary`；
- `get_memory_source`。

它没有 shell 写权限、网络、业务工具、外部 MCP 或跨用户路径；受 max steps、timeout、token、returned spans 和并发预算限制。输出 schema 必须包含 `bulletin`、`memory_ids`、`source_spans`、`evidence_status`；来源无效或证据不足时返回明确 abstain，不得编造结论。

fast/deep 是同一读取架构的成本分级，不是两套可配置产品方案。深度路径不会在每次普通请求前强制运行。

### 9. Bulletin 在同一 Run 内冻结，raw recall 不进入 system prompt

自动 fast Bulletin、`run_id`、memory ids、source snapshot 和 `bulletin_hash` 写入 LangGraph `PrivateStateAttr`。模型可见 Bulletin 只包含稳定 statement/applicability/verification label 与 memory id；source run/span、last_verified_at、evidence count、当前时间和当前 run_id 只保存在 private metadata，通过来源工具展开，不进入自动 prompt。同一 Run 的多次 model call、HITL resume 和跨进程 checkpoint 恢复复用逐字节相同的自动块；新的 Run 重新检索。private state 不复制给 subagent。

显式 deep query 的结果作为工具输出进入当前对话，并带 untrusted/evidence framing；它不修改已经冻结的自动块。任何 query embedding、Qdrant、workspace 或 PostgreSQL 故障都返回零自动注入或可解释的搜索错误，不降低 scope/provenance 门槛，不阻塞 Agent 主流程。

备选方案是 raw top-k 直接拼接或每轮重新检索。前者会污染指令上下文，后者破坏 Run 稳定性和可复现性，均不采用。

### 10. 单一用户开关控制自动链路

`t_memory_user_preference.enabled` 默认 false，且是唯一功能控制：

- false：新的 eligible terminal Run 不创建自动 capture/extraction/consolidation job，新 Run 不自动检索/注入；
- true：允许自动 capture、后台处理和 fast Bulletin；
- 两种状态下：已有 item 均可查看、显式搜索、回源、编辑、失效和删除；
- 关闭时已 claimed job 在进入每个阶段前重新检查 preference，安全停止并记录 `skipped_disabled`；
- 重新开启后只处理之后 completed 的 Run，除非用户显式发起受控 backfill。

不再提供平台总开关、generate/use 双开关或多个运行方案。停 worker 仅是运维故障/回滚手段，不改变用户设置语义。

### 11. API 与 UI 展示记忆和后台健康，不暴露实现细节

继续使用 `/api/user/memory/cortex` 静态前缀：

- preference GET/PUT；
- item list/detail/update/disable/enable/invalidate/delete；
- evidence/source；
- processing health：最近 capture/consolidation 时间、pending/partial/failed/dead 数量、索引/workspace 延迟；
- 可选用户触发 retry/backfill，必须有幂等和预算限制。

响应 schema 位于 `noesis.schemas.memory`，API 只做鉴权、CSRF、输入输出翻译。用户文案使用“经验记忆、来源、待确认、处理失败”等业务词，不显示表名、provider key、workspace 路径、Qdrant、claim token 或内部错误。

删除 item 会同步清理 evidence relation 和派生视图，但不默认建立永久 tombstone；确认文案必须说明未来相似 Run 可能重新生成。若产品需要“永远不要记住此主题”，应另建用户规则能力，不混入 delete。

### 12. 评测先于全量实现并分层定位错误

评测数据每条包含：完整/分块 Run snapshot、gold memory items、type、scope、source spans、预期 revision、是否可自动注入、后续任务和安全标签。

测试层次：

1. **Capture coverage**：eligible Run 是否产生 snapshot；长输入是否完整分块；不得静默丢失。
2. **Extraction**：precision、recall、type accuracy、source-span accuracy、no-output accuracy。
3. **Consolidation**：ADD/REINFORCE/UPDATE/SUPERSEDE/CONTRADICT/NOOP、状态和时间语义。
4. **Retrieval**：precision@k、answer-bearing Run/span recall、scope/abstention、latency。
5. **Context**：Bulletin precision、harmful/stale/untrusted injection、token 数。
6. **End-to-end**：paired memory-on/off 的任务成功率、重复失败率、工具调用、token、TTFT 和总成本。
7. **Safety**：cross-user/project、recall-loop、外部指令、删除/关闭残留。

dev/test 数据、模型、embedding、prompt、阈值和 paired seeds 在 test 前冻结。首版 release gate 固定为：eligible fixture capture coverage=1.0 且 silent drop=0；extraction precision≥0.85、recall≥0.80；source-span precision/recall≥0.90；consolidation operation accuracy≥0.85；exact evidence recall@5≥0.80、precision@5≥0.70；cross-user/project 泄漏、低信任命令自动注入和 recall-loop 增量均为 0；fast path 额外 p95 延迟≤500ms 且自动 Bulletin≤500 tokens；paired end-to-end 中任务成功率差值的 95% CI 下界≥-2 个百分点，并且任务成功率提升或重复失败率下降至少一项的 95% CI 排除零。阈值不得根据 test 结果修改；未通过时 SHALL 停止全量启用并回到对应层修正。

默认测试只用 fake/fixture；live eval 必须显式启用并记录模型、版本、成本和时间。单个综合 QA 分数不能替代分层指标。

### 13. 首版 scope、artifact、用户编辑和历史处理规则固定

首版 canonical project key 规则固定为：会话工作区内带 `origin` 的 Git repository 使用规范化 remote identity；其余情况（含无 `origin` 的 Git repository 与非 Git Run）一律使用 `global`。产品现实：Agent 运行工作区是每会话沙箱目录，SuperAgent 等非克隆仓库场景的 scope 恒为 `profile:*|project:global`；origin 分支是唯一的跨会话项目级通道（同仓库克隆到不同会话得到相同 origin digest，经验互通）。无 `origin` 的沙箱仓库不按本地路径 digest 派生 key——该 key 含 session_id，其他会话永远无法复现，会把记忆锁进不可召回的死胡同 scope。只有同 project key 的经验允许自动注入；`global` item 默认保持 candidate/pull-only，只有用户明确确认其适用于全部非项目任务后才可 active/自动注入，不能把一次临时目录经验扩散到所有任务。

artifact evidence 首版只保存 artifact id/type、用户可见逻辑路径、content digest、size、生成/修改状态、验证结果和受配置限制的 diff/summary；不复制二进制内容、完整大 diff 或服务端绝对路径。

用户编辑 item 时创建新 item version，并追加 `source_kind=user_revision` 的审计 evidence；旧版本进入 superseded，自动 consolidation 不得覆盖用户 revision，除非后续用户再次编辑或明确标记失效。

首版不提供关闭期间或功能上线前历史 Run 的自动 backfill。系统只处理用户开启后的新 eligible Run；未来若新增 backfill，必须通过独立 change 规定用户显式授权、时间/数量预算和删除语义。

### 14. 动态 Bulletin 必须保护 provider 上下文缓存

PromptAssembler SHALL 将稳定 system/developer instructions、工具 schema 和可缓存历史前缀保持在动态 Bulletin 之前；自动 Bulletin 作为独立的 late context segment 放在稳定前缀之后、当前用户输入/本轮新增内容之前。不得把 Bulletin 插入稳定 system prompt 中间或重复拼入多个消息。这样 Bulletin 变化只使其后的动态后缀失效，不破坏更长的稳定前缀缓存。当前只依赖 provider 自动 prefix cache，不注入 provider 专用 breakpoint 标记（暂无显式缓存 provider 诉求）；若未来接入要求显式 breakpoint 的 provider，再单独提案补充适配层 capability。

Bulletin 使用 canonical serializer：memory item 按稳定 score bucket、type、memory id 排序；固定字段顺序、空白、标题和转义；不渲染当前时间、当前 run id、evidence count、last verified time、source run id 或随机值。同一 `bulletin_hash` 必须生成逐字节相同的 model-visible text。内容真实变化时允许产生新 hash 并自然失效旧后缀缓存，不为了 cache 保留 stale memory。

显式 Deep Query 作为后续 tool result 加入消息尾部，不改写 system prompt 或已冻结自动 Bulletin，因此只影响本次新增后缀。Noesis 已有 usage normalization 可记录 `cache_read_tokens` / `cache_write_tokens`；若 provider 不返回 cache 指标，系统记录 unknown，不得当作 0。

评测 SHALL 分别报告 memory-on/off 的 cache read ratio、cache write tokens、uncached input tokens、TTFT 和成本，并按“同 Run 后续调用”“新 Run Bulletin 未变化”“新 Run Bulletin 变化”三类场景检查。同 Run、Bulletin 未变化和 Deep Query 后续调用在 provider 声明支持 prefix cache 时必须验证实际 reuse；Bulletin 变化场景硬性验证稳定 prefix 的逐字节 hash/位置不变，同时如实报告实际 cache read。后者不得因 provider 路由、cache eviction 或实现只缓存更长 prefix 而伪造成必然命中。cache 优化不能删除必要记忆、延迟更新或改变 scope/provenance 结果。

## Risks / Trade-offs

- [Risk] 所有 eligible terminal Run 都进入队列会增加后台成本。→ capture 先做确定性过滤和 token 估算；允许 `succeeded_no_output`；按用户/时间预算限流，不跳过 coverage 统计。
- [Risk] 文件 workspace 与 PostgreSQL 双份表示可能漂移。→ PostgreSQL 为唯一事实源；workspace 使用 outbox、desired-state 重读、原子替换和全量重建。
- [Risk] 主动检索准确但延迟高。→ fast path 零额外生成调用；deep path 只按意图/工具显式触发，并限制 steps、timeout、spans 和并发。
- [Risk] 动态 Bulletin 改变 prompt prefix，降低 provider cache hit 并增加 TTFT/成本。→ 保持稳定 system/tool/history 前缀，Bulletin late-insert、canonical serialize、同 Run 冻结，动态来源/时间只放 private metadata，并单独评测 cache read/write。
- [Risk] LLM 生成 subject 导致重复或错误合并。→ 代码归一化 subject key；向量只给候选；SUPERSEDE/CONTRADICT 必须有同 subject 或用户纠正证据。
- [Risk] 外部工具内容形成持久指令。→ provenance 结构化；外部内容不能单独激活命令式记忆；deep recall 以证据数据而非指令进入上下文。
- [Risk] 长 Run 分块丢失跨 chunk 因果。→ 保留全局 Run outline、tool/action sequence、chunk overlap handles；merge 阶段只从已验证 evidence refs 综合。
- [Risk] 旧实现与新 schema 并存造成维护成本和误判。→ 在编写新实现前先删除旧 extraction/revision/retriever/action-card 行为及装配，建立机器经验功能暂时不可用但应用可编译测试的空白基线；只保留经清单确认的中性可靠性/偏好基础，不保留版本选择或兼容开关。
- [Risk] 自动记忆可能放大错误。→ candidate/needs_review、source citation、用户治理、有效期和 memory-on/off eval 共同限制。
- [Risk] 单一开关无法分别控制成本和注入。→ 这是明确产品要求；查看/搜索/治理始终可用，后台预算由系统安全配置而非第二用户开关管理。

## Migration Plan

1. 盘点并删除现有 experience-only item/evidence/job/outbox 表定义与运行数据；功能未上线，不导出迁移快照。只保留独立的单一 preference 和现有通用 user/scope 鉴权。
2. 删除旧 RecoveryAdapter、failure identity/resolution、experience-only Extractor/Revision/Retriever、raw action-card middleware、旧 worker 装配、旧 API/UI 字段和只验证旧语义的测试；同时删除 `MemoryDreamService`、Dream scheduler、自动补写、按日记忆 API/UI/search 分支、`memory/YYYY-MM-DD.md` 运行时数据和测试；移除 completed-only/failure-only job 创建分支。
3. 建立空白基线：应用、迁移和现有非机器记忆测试可运行；经验记忆开启时暂不自动 capture/注入；使用静态扫描和测试证明没有旧 worker、旧 middleware、兼容 flag 或双路径。
4. 先建立新版 eval fixtures/harness 和冻结 release gate，再新增 `t_memory_run_snapshot`、通用 item/provenance/relation/job schema。
5. 更新 Run 终态为所有启用用户的 eligible terminal Run 创建 capture job；部署 token-aware capture/extract/consolidation，并先只生成 snapshot/candidate。
6. 部署 workspace outbox、Qdrant desired-state 重建、fast Bulletin 与受限 deep query；不得恢复旧 action-card 路径。
7. 更新 API/UI 为四类 memory 与处理健康；不读取或恢复任何旧 active experience/evidence。
8. 在全部 release gate 通过后，用户自行开启 `enabled`；未通过时保持自动链路不可用并修正对应层。
9. 回滚时关闭用户 preference、停止新 worker 和自动注入，保留 PostgreSQL snapshot/item；派生 workspace/Qdrant 可删除并重建。聊天与用户显式维护的 `USER.md` / `AGENTS.md` 不受影响；旧 Dream/按日记忆代码不恢复。

## Open Questions

首版实现前没有未决的产品或数据边界；模型、embedding、阈值、token/step/retention 数值由 dev eval 冻结后写入配置和验证记录，不改变本设计的行为契约。
