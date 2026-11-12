# Delta: platform-chat — qa_type 路由收窄

## MODIFIED Requirements

### Requirement: qa_type 路由

`POST` 流式问答 SHALL 按 `qa_type` 路由到核心的档位 profile（详见 `agent-profiles`、`agent-heads`）：

| `qa_type` | 档位 |
|-----------|------|
| `COMMON_QA` | 轻档（无工作目录；检索 + 记忆 + 能力开关） |
| `SUPER_AGENT_QA` | 重档（工作区 / skills / 子 Agent） |

未知 `qa_type` SHALL 拒绝。历史仅展示的废弃类型（旧 DeepResearch、`TEST_CASE_QA`、`FAULT_OPERATION_QA`）MAY 只读映射，**SHALL NOT** 作为新发送入口。档位选择 SHALL 优先取显式参数；无显式参数时 MAY 用兜底规则（带附件 / 提及工作目录 → 重档），SHALL NOT 维护三形态意图分类器。

#### Scenario: SUPER_AGENT 路由

- **WHEN** 请求 `qa_type=SUPER_AGENT_QA`
- **THEN** 系统 SHALL 以重档装配核心（Skills / 工作区 / 可选 HITL）

#### Scenario: 废弃类型只读

- **WHEN** 请求 `qa_type=FAULT_OPERATION_QA`
- **THEN** 系统 SHALL 拒绝创建新 run，并返回该类型已下线的可解释错误
