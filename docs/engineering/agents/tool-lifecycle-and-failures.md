# 工具生命周期与失败处理

> 状态：Current  
> 规格：`agent-tool-failure-handling`

## 1. 三层语义

工具结果不能只用一个 success/error 表达：

| 字段 | 回答的问题 | 示例 |
|---|---|---|
| `status` | invoke 是否正常返回 | `success` / `error` |
| `outcome` | 正常返回后，执行结果是什么 | `ok` / `empty` / `command_failed` / `timed_out` |
| `state` | 用户现在应看到什么生命周期 | `running` / `failed` / `timed_out` 等 |

`state` 的合法值为：

```text
running ──▶ approval_pending ──▶ running ──▶ succeeded
   │                 ├─────────▶ rejected
   │                 └─────────▶ cancelled / failed / timed_out
   └───────────────────────────▶ failed / timed_out / cancelled
```

终态 `succeeded | failed | timed_out | rejected | cancelled` 不接受晚到事件回退。重复相同终态按 `tool_call_id` 幂等更新同一 part。

## 2. 数据流

```text
LangGraph callback
  → LangGraphSseBridge（生成 status/outcome/state/结构化执行字段）
  → RunEvent
  → RunProjection（幂等投影 + Run 边界 reconcile）
  → PersistSink / Run snapshot / assistant content.parts
  → 前端 snapshot replace / SSE reducer
  → ToolCallCollapse / SubagentCollapse
```

`AssistantMessageBuilder` 与前端 reducer 都以 `tool_call_id` 定位，HITL resume 或 snapshot 重放不得追加第二张工具卡片。

## 3. 边界收敛

- `completed`：残留非终态工具转为 `cancelled`，不能误报成功。
- `partial/stopped`：残留非终态工具转为 `cancelled`。
- `error`：残留非终态工具转为 `failed`；执行/HITL 超时转为 `timed_out`。
- `hitl_pending`：本次 action 与承载它的父 task 为 `approval_pending`；其它无执行者的非终态工具转为 `cancelled`。
- 服务重启：原 `running` 工具转为 `failed + outcome=unknown + errorCategory=server_restart`，不推断远程副作用是否发生。

## 4. 进程结果

`execute` 只认执行包装返回的固定结果协议，不根据 stdout/stderr 中出现 `failed`、`not found` 等单词猜测。保留字段：

- `exit_code=0` → `outcome=ok + state=succeeded`
- `exit_code!=0` → `outcome=command_failed + state=failed`
- `exit_code=124` 或明确 `timed_out=true` → `outcome=timed_out + state=timed_out`
- `truncated=true` 只表示输出被截断，不改变成功/失败结论

Noesis 不为生成命令追加 `|| true`。用户明确提供 shell 容错表达式时，按 shell 最终退出码处理。

## 5. 产品展示与脱敏

工具卡片固定显示：正在执行、等待确认、已完成、执行失败、执行超时、已拒绝、已停止。失败详情只展示安全短句、适用的退出码和截断提示，不展示堆栈、宿主路径、provider、内部网络地址或密钥。

同一回答有任意失败终态时，只显示一次回答级完整性提示；没有正文时提供重新执行入口。

## 6. 排查查询

检查终态 run 是否仍保存非终态工具：

```sql
SELECT r.id AS run_id,
       p->>'tool_call_id' AS tool_call_id,
       p->>'name' AS tool_name,
       p->>'state' AS tool_state
FROM t_agent_run r
CROSS JOIN LATERAL json_array_elements(COALESCE(r.snapshot->'parts', '[]'::json)) p
WHERE r.status IN ('completed', 'partial', 'error', 'interrupted')
  AND p->>'type' = 'tool'
  AND p->>'state' IN ('running', 'approval_pending');
```

检查 snapshot 与 assistant 历史中的同一工具状态是否不一致：

```sql
SELECT r.id AS run_id,
       rp->>'tool_call_id' AS tool_call_id,
       rp->>'state' AS run_state,
       mp->>'state' AS message_state
FROM t_agent_run r
JOIN t_chat_message m ON m.id = r.assistant_message_id
CROSS JOIN LATERAL json_array_elements(COALESCE(r.snapshot->'parts', '[]'::json)) rp
JOIN LATERAL json_array_elements(COALESCE(m.content->'parts', '[]'::json)) mp
  ON mp->>'tool_call_id' = rp->>'tool_call_id'
WHERE rp->>'type' = 'tool'
  AND mp->>'state' IS DISTINCT FROM rp->>'state';
```
