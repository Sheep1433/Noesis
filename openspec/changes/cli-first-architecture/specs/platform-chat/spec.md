# Delta: platform-chat — qa_type 路由收窄

## MODIFIED Requirements

### Requirement: qa_type 路由

`POST` 流式问答 SHALL 按 `qa_type` 路由到核心的档位 profile（详见 `agent-profiles`、`agent-heads`）。轻档无工作目录，含检索 / 记忆 / 能力开关；重档含工作区 / skills / 子 Agent。

| `qa_type` | 档位 |
|-----------|------|
| `COMMON_QA` | 轻档 |
| `SUPER_AGENT_QA` | 重档 |

未知 `qa_type` SHALL 拒绝。历史仅展示的废弃类型（旧 DeepResearch、`TEST_CASE_QA`、`FAULT_OPERATION_QA`）MAY 只读映射，**SHALL NOT** 作为新发送入口。档位选择 SHALL 优先取显式参数；无显式参数时 MAY 用兜底规则（带附件 / 提及工作目录 → 重档），SHALL NOT 维护三形态意图分类器。

#### Scenario: SUPER_AGENT 路由

- **WHEN** 请求 `qa_type=SUPER_AGENT_QA`
- **THEN** 系统 SHALL 以重档装配核心（Skills / 工作区 / 可选 HITL）

#### Scenario: 废弃类型只读

- **WHEN** 请求 `qa_type=FAULT_OPERATION_QA`
- **THEN** 系统 SHALL 拒绝创建新 run，并返回该类型已下线的可解释错误

## MODIFIED Requirements

### Requirement: 可靠 Web Agent Run SHALL 明确适用范围

typed RuntimeEventMapper、可靠 Run、多 Tab、snapshot 恢复和统一 Delivery 的演进与验收范围 SHALL 为 `COMMON_QA` 与 `SUPER_AGENT_QA`（`FAULT_OPERATION_QA` 已下线，处置同 `TEST_CASE_QA`：存量会话仅作只读历史展示，任何入口 SHALL NOT 再创建该类型的新 run，不保留兼容 parser 或独立 SSE 边界）。

#### Scenario: 废弃类型不接受新 run

- **WHEN** 执行本能力的实现或验收
- **THEN** SHALL 只验收 `COMMON_QA` 与 `SUPER_AGENT_QA` 的 typed 主路径
- **AND** 以 `FAULT_OPERATION_QA` 或 `TEST_CASE_QA` 发起新 run SHALL 走未知 `qa_type` 的拒绝路径
