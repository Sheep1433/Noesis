## Purpose

本能力规定故障运维（`FAULT_OPERATION_QA`）场景下，系统如何把已验证的排查结论沉淀为可检索的运维经验，并在后续对话中与 SOP/规划检索协同注入模型上下文，同时满足开关、审计与隔离等治理要求，作为实现与测试的验收基线。

## ADDED Requirements

### Requirement: 经验生命周期与状态

系统 SHALL 为每条运维经验维护生命周期状态（至少包含：`draft`、`active`、`disabled`），并记录不可篡改的来源标识（来源会话 `conversation_id`、创建用户、创建时间与最后更新）。仅 `active` 状态的经验参与默认检索；`disabled` SHALL 不得出现在默认检索结果中。

#### Scenario: 草稿不可被默认检索

- **WHEN** 一条经验处于 `draft` 状态
- **THEN** 默认检索路径 SHALL 不返回该条目
- **AND** 管理/调试接口若显式查询草稿，须在权限与审计约束下单独实现（若提供）

#### Scenario: 下线立即生效

- **WHEN** 运维将某条经验标记为 `disabled`
- **THEN** 后续故障运维请求的默认检索 SHALL 不再返回该条目
- **AND** 已持久化的历史引用记录（若存在）可保留用于审计，但不得用于默认注入

### Requirement: 经验写入准入

系统 SHALL 仅在「写入开关」开启且满足准入条件时创建或晋升经验：准入条件至少包括（1）问答类型为 `FAULT_OPERATION_QA`；（2）存在可用的用户或系统显式「可沉淀」信号（例如用户确认已解决或等价 API/内部调用）；（3）通过可配置的最小内容校验（非空、长度下限、必选结构化字段如「现象摘要」「根因」「关键步骤」）。不满足时 SHALL 拒绝写入并记录原因日志，不得静默丢弃异常。

#### Scenario: 写入关闭时拒绝入库

- **WHEN** 配置 `experience_write_enabled` 为 false
- **THEN** 系统 SHALL 不接受新的经验持久化请求
- **AND** SHALL 返回业务可辨失败或内部无操作（若仅为内部调用则记录 warning），且不影响正常对话

#### Scenario: 缺少显式信号不入库

- **WHEN** 对话结束但未收到显式「可沉淀」信号
- **THEN** 系统 SHALL 不创建新的 `active` 经验条目

### Requirement: 故障运维检索注入

当请求为 `FAULT_OPERATION_QA` 且「检索开关」开启时，系统 SHALL 在用户问题进入 `FaultOperationAgent` 主推理前，基于当前用户可见范围检索有限条数（可配置 Top-K）的 `active` 经验，并将摘要安全地合并到模型上下文（例如系统提示附加块或首轮附加上下文），且 SHALL 对总长度施加上限以避免撑爆上下文。

#### Scenario: 检索超时降级

- **WHEN** 经验检索超过配置超时或依赖不可用
- **THEN** 系统 SHALL 降级为不带经验块的故障运维流程
- **AND** SHALL 记录一次可观测告警或 error 日志
- **AND** SHALL 不因此中断 SSE 主流程

#### Scenario: 无命中时不注入

- **WHEN** 检索结果为空
- **THEN** 系统 SHALL 不向模型注入虚构经验
- **AND** Agent SHALL 仍可仅依赖静态提示与既有工具链继续运行

### Requirement: 隔离与隐私

系统 SHALL 按用户或项目约定的租户键过滤经验检索与写入，保证用户 A 的默认检索结果不包含用户 B 的数据；写入 SHALL 绑定当前认证主体，不得从未授权会话冒用 `conversation_id`。

#### Scenario: 跨用户隔离

- **WHEN** 用户 A 发起故障运维请求
- **THEN** 默认检索 SHALL 仅返回用户 A 可见范围内的经验
- **AND** 任何管理列举接口在未授权时 SHALL 返回 401/403 与统一失败结构（与项目 HTTP 约定一致）

### Requirement: 可观测与配置

系统 SHALL 通过 `config/env.py`（或等价配置源）提供至少：`experience_learning_enabled`（总开关）、`experience_write_enabled`、`experience_retrieval_top_k`、检索超时；禁止在代码中硬编码密钥与默认生产开关为全开写入。

#### Scenario: 总开关关闭

- **WHEN** `experience_learning_enabled` 为 false
- **THEN** 系统 SHALL 跳过经验检索与写入相关逻辑路径
- **AND** 故障运维行为须与未部署本能力时一致
