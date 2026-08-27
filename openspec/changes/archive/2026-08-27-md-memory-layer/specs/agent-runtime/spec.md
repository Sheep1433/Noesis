## REMOVED Requirements

### Requirement: SuperAgent SHALL 提供用户记忆检索工具

移除原因：深查询链路（MemoryQueryService、PG/Qdrant 权威校验、状态过滤）随旧皮层删除；`search_memory` 改为对 memory 目录的 grep/读文件，见 agent-memory-cortex 变更。

### Requirement: SuperAgent SHALL 提供记忆来源读取工具

移除原因：`get_memory_source` 依赖 evidence/span 表；md 文件层条目自带来源行，无独立来源读取工具。

### Requirement: Agent Runtime SHALL 按 Run 注入稳定的 Memory Bulletin

移除原因：自动 Bulletin 注入路径删除；注入改为「稳定前缀（USER.md + 索引）+ 每 Run 选条快照」，见下方 ADDED。

### Requirement: MemoryQueryService SHALL 使用独立只读运行边界

移除原因：MemoryQueryService 及其 trace 表随旧皮层删除。

### Requirement: Runtime SHALL 保护稳定 Prompt 前缀的上下文缓存

移除原因：该 requirement 绑定 Bulletin 序列化与 hash 机制；缓存纪律（稳定前缀区禁放每 Run 变化内容）并入下方 ADDED requirement。

## ADDED Requirements

### Requirement: 记忆注入 SHALL 区分稳定前缀与 Run 级选条通道

Runtime SHALL 经上下文文件通道注入记忆：`USER.md` 与 `MEMORY.md` 索引 SHALL 位于稳定前缀（会话内不变），每 Run 选中的条目正文 SHALL 经 Run 级 late-context 快照通道注入；每 Run 变化的内容 SHALL NOT 进入稳定前缀区（防全历史缓存失效）。注入 SHALL 在 Run 级冻结，subagent 不重复注入。`search_memory` 工具 SHALL 以 grep 与文件读取实现并遵循既有预算限制。旧 Bulletin 中间件注入路径 SHALL 移除。

#### Scenario: 注入位置稳定
- **WHEN** 同一会话多轮对话
- **THEN** 稳定前缀内容 SHALL 不变，记忆内容 SHALL 位于相同注入位置

#### Scenario: 检索预算
- **WHEN** 模型调用 search_memory
- **THEN** 工具 SHALL 遵循预算并只读 memory 目录文件
