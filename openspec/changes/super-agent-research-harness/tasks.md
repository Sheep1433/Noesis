## 1. Research context and trace model

- [ ] 1.1 确定 `SUPER_AGENT_QA` research context 的唯一注入点与生命周期字段，定义 `research_run_id`、`skill_id`、phase、run/task/activity identity 的来源和兼容缺省行为
- [ ] 1.2 在 noesis-core 增加 research trace 的领域模型或等价持久化结构，覆盖 candidate、source identity、evidence、citation、activity、research gap 和 run 状态
- [ ] 1.3 实现 canonical URL、规范化正文 hash 和 duplicate cluster 规则；同一来源合并展示身份但完整保留 query/provider/rank/tool/task provenance
- [ ] 1.4 增加 trace 状态迁移与幂等 upsert 约束，覆盖 SSE 重订阅、checkpoint 恢复、重复事件和 session 删除清理

## 2. Retrieval and evidence harness

- [ ] 2.1 在 `SUPER_AGENT_QA` research context 接入 web search、web fetch 和知识库检索的 candidate recorder，保持现有 `RetrievalManifest` 作为兼容投影而非唯一事实来源
- [ ] 2.2 将可定位的抓取正文片段提升为 evidence，保存 source identity、artifact identity、locator、验证状态和来源快照关联
- [ ] 2.3 实现 candidate → evidence → citation 的合法绑定校验；无法解析的引用、未验证来源和必要研究缺口必须形成可查询的 trace 状态
- [ ] 2.4 将完整搜索响应、抓取正文和大段工具输出写入 session workspace artifact 或现有 offload 机制，并记录大小、截断原因和 artifact identity
- [ ] 2.5 删除或收敛重复的 research 专用 offload 逻辑，明确与 `FilesystemMiddleware`、现有 `tool_result_budget_middleware` 的职责边界，避免同一工具结果被多次保存

## 3. Context-aware tool budget

- [ ] 3.1 设计当前 model request 的上下文预算计算器，输入模型上下文上限、已占用 prompt/tool 内容、预留输出空间和 provider usage/估算来源
- [ ] 3.2 将单工具结果压缩或 artifact offload 接入动态预算；provider usage 或 tokenizer 不可用时使用保守估算，并把估算方式和截断信息写入 trace
- [ ] 3.3 移除固定并行 `aggregate_max_chars=48K` 作为唯一门禁，使并行与串行工具使用同一动态预算规则，同时保留必要的单结果安全上限
- [ ] 3.4 增加预算回归测试：简单问题、多轮搜索、并行工具、长 web fetch、上下文接近上限、总结后继续调用，以及 provider usage 缺失场景

## 4. Runtime failure and persistence integration

- [ ] 4.1 为 tool、candidate、evidence、task 和 research run 分别实现状态记录与终态规则；单个子工具失败不得自动覆盖父 task 或整轮 research 的明确终态
- [ ] 4.2 在工具终态、evidence 创建、phase 变更、task 终态和 citation binding 处写入语义 checkpoint，禁止按流式 chunk/token 逐条写数据库
- [ ] 4.3 将 research trace 与现有 assistant message skeleton/checkpoint/terminal 持久化链路关联，确保服务端 authoritative 落库和重放不产生重复 assistant row
- [ ] 4.4 增加 `RunOutputExceeded`、子 Agent partial、web fetch 失败、checkpoint 重放和研究缺口的集成测试，验证父子状态与最终报告状态一致

## 5. Delivery and frontend projection

- [ ] 5.1 定义可选、run-scoped 的 research activity SSE 事件或 snapshot 扩展，保证旧客户端忽略新增字段/事件后仍能完成现有聊天渲染
- [ ] 5.2 将最终报告投影为结构化正文 + 仅实际使用的 Sources used；候选、重复、淘汰和失败详情默认保留在 trace/debug 入口
- [ ] 5.3 为 partial、citation_incomplete 和 research gap 增加用户可理解的状态展示，不把未验证来源显示成已验证引用
- [ ] 5.4 增加 SSE 重订阅、sequence gap、刷新恢复和无 research activity 旧客户端的前端回归测试

## 6. Migration, observability and rollout

- [ ] 6.1 先以旁路模式记录真实 `SUPER_AGENT_QA` 研究任务的 candidate、duplicate rate、evidence promotion、citation completeness、trace/artifact size 和上下文截断次数
- [ ] 6.2 提供 research harness feature flag、关闭后的普通 SuperAgent 降级路径和 trace/artifact retention 清理策略
- [ ] 6.3 编写 API/SSE、数据库、artifact、配置和前端兼容说明，明确 trace 查询接口是否在本变更提供或暂由内部诊断接口承载
- [ ] 6.4 执行后端测试、前端 lint/build 和至少一轮真实深度调研验收；确认没有固定 48K 并行门禁、没有重复来源展示、引用可追溯且失败归因正确
