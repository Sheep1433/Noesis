## ADDED Requirements

### Requirement: 系统 SHALL 提供可配置的上下文窗口上限

系统 SHALL 在 `config.yaml` 的 **`context`** 段（经 `ModelConfig` 暴露）支持：

- **`max_input_tokens`**：非负整数，表示 Agent 会话上下文窗口上限（token 估算基准）。当值 **> 0** 时，系统 SHALL 将其作为上下文占用率的分母，且 **`SummarizationOffloadMiddleware`**（及等价摘要逻辑）SHALL 使用同一上限作为 `_get_profile_limits()` 的优先来源。
- **`display_enabled`**：布尔值；为真时系统 SHALL 向 SSE 推送 `context-update` 并在 chat 页展示指示器；为假时 SHALL NOT 推送且前端 SHALL 隐藏指示器。

当 **`max_input_tokens` 为 0** 时，系统 SHALL 尝试从主模型 `profile.max_input_tokens` 解析；若仍不可用，SHALL 记录 warning 并使用仓库约定的保守默认值（SHALL NOT 回退到 `generation.max_tokens`）。

环境变量 MAY 通过既有 `ModelConfig` 合并机制覆盖 yaml（命名以 `design.md` / `config.example.yaml` 为准）。

#### Scenario: 显式配置上下文上限

- **WHEN** `context.max_input_tokens` 设为 `128000` 且 `display_enabled` 为真
- **THEN** `context-update` 帧的 `context.max_tokens` SHALL 为 `128000`
- **AND** 摘要触发分数 SHALL 基于 `128000` 与 `summarization.trigger_fraction` 计算

#### Scenario: 关闭展示时不推送 SSE

- **WHEN** `context.display_enabled` 为假
- **THEN** 流式路径 SHALL NOT 发出 `context-update` 事件
- **AND** chat 页 composer footer SHALL NOT 展示上下文指示器

### Requirement: 流式路径 SHALL 发出会话上下文占用的 context-update

对经 **`create_noesis_agent`** 装配的 Agent 流式路径（至少 **`COMMON_QA`**、**`FAULT_OPERATION_QA`**、**`DEEP_RESEARCH_QA`**），系统 SHALL 在每次 LLM 调用前的 **`before_model`** 阶段估算当前消息列表的输入 token 数，并在 **`context.display_enabled`** 为真时发出 SSE 事件 **`context-update`**。

`data:` JSON SHALL 包含至少：

- **`type`**: `"context-update"`
- **`messageId`**: 当前 assistant 消息 ID（与 `message-start` 一致）
- **`context`**: 对象，键为 **`current_tokens`**（非负整数）、**`max_tokens`**（正整数）、**`used_percentage`**（0–100 整数，`min(100, round(current_tokens / max_tokens * 100))`）

当摘要或工具结果卸载导致消息列表 token 估算变化后，系统 SHALL 在后续 `before_model` 或等价时机再次发出 `context-update`，使 `current_tokens` 反映压缩后的值。

本 Requirement 的 token 估算 SHALL 使用与 **`SummarizationOffloadMiddleware`** 相同的计数器实现，SHALL NOT 使用 provider `usage_metadata` 或 `usage-update` 累计值替代。

`TEST_CASE_QA`（CaseCoordinator StateGraph）路径在本能力首期 MAY 不发出 `context-update`；前端对该 `qa_type` SHALL 隐藏上下文指示器。

#### Scenario: 单轮对话前发出 context-update

- **WHEN** 用户发起流式对话且 `before_model` 估算 `current_tokens=29000`、`max_tokens=128000`
- **THEN** SSE SHALL 含 `context-update`，其 `context` 为 `{ current_tokens: 29000, max_tokens: 128000, used_percentage: 23 }`（四舍五入）

#### Scenario: 摘要后上下文下降

- **WHEN** 摘要中间件将估算 token 从 `90000` 降至 `35000`，且下一次 `before_model` 运行
- **THEN** 随后 `context-update` 的 `current_tokens` SHALL 约为 `35000`，`used_percentage` SHALL 相应下降

#### Scenario: LangGraphSseBridge golden 覆盖 context-update

- **WHEN** 自动化测试向桥接注入含 custom stream `context-update` 负载或等价合成事件
- **THEN** 输出 SHALL 含 `event: context-update` 且 `data` JSON 含 `type`、`messageId`、`context.current_tokens`、`context.max_tokens`、`context.used_percentage`

### Requirement: 会话 SHALL 持久化最近上下文快照

系统 SHALL 在流式过程中或回合结束时，将会话**最近一次**有效的 `context-update` 快照写入该会话的 **`extra.context`**（或与之等价的会话级 JSON 字段），至少包含 **`current_tokens`**、**`max_tokens`**、**`used_percentage`**。

加载会话历史时，客户端 SHALL 可从会话 `extra.context` 恢复 composer footer 指示器的初值；流式 `context-update` 到达后 SHALL 覆盖本地状态。

#### Scenario: 刷新页面后恢复指示器

- **WHEN** 用户刷新 chat 页且会话 `extra.context` 为 `{ current_tokens: 87040, max_tokens: 128000, used_percentage: 68 }`
- **THEN** composer footer SHALL 展示约 `68%` 的环形指示器
- **AND** hover tooltip SHALL 展示 `87K / 128K` 量级绝对值（格式化规则见前端 Requirement）

### Requirement: chat 页 composer footer SHALL 展示上下文占用指示器

系统 SHALL 在 **`chat.vue`** 输入区（composer）**下方**展示会话级上下文指示器，行为如下：

- 默认展示 **环形进度 + 整数百分比**（如 `68%`），数据来源于最新 `context-update` 或会话 `extra.context`。
- 用户 **hover** 指示器时，SHALL 通过 tooltip 展示 **绝对 token 值**，格式为 **`{current} / {max}`**，使用与 `formatTokenCount` 一致的缩写（如 `87K / 128K`）。
- 指示器 SHALL 与 assistant 气泡底部的 **`usage-update` 计费 token 行分开**，SHALL NOT 合并为一行。
- 当无有效 `context` 数据（`max_tokens` 缺失或 `display_enabled` 为假，或 `qa_type` 为 `TEST_CASE_QA`）时，SHALL 隐藏指示器。
- 流式过程中 SHALL 随 `context-update` 实时刷新；未知 SSE 键 SHALL 兼容忽略。

颜色语义 SHALL 随 `used_percentage` 变化：低于 60% 为中性、60%–84% 为警告色、85% 及以上为接近上限色（与默认摘要触发比例 0.85 对齐）。

#### Scenario: hover 显示绝对值

- **WHEN** 指示器显示 `68%` 且 `context` 为 `{ current_tokens: 87040, max_tokens: 128000 }`
- **THEN** 用户 hover 时 tooltip SHALL 展示 `87K / 128K`（或等价格式化结果）

#### Scenario: 与 usage 行并存

- **WHEN** 同一会话 assistant 消息含 `usage-update` 累计 token 且 composer footer 含上下文指示器
- **THEN** assistant 气泡底部 SHALL 仍展示 `↑… ↓…` 计费摘要
- **AND** composer footer SHALL 独立展示上下文百分比，二者 SHALL NOT 互相替代

#### Scenario: TEST_CASE_QA 隐藏指示器

- **WHEN** 用户切换 `qa_type` 为 `TEST_CASE_QA`
- **THEN** composer footer SHALL NOT 展示上下文指示器
