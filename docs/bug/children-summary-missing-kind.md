# children 摘要丢失 kind 字段，真实集成断言失败

**状态**：🆕 新增
**日期**：2026-09-04

## 现象

`tests/api/test_bg_subagent_real.py::test_super_agent_launches_bg_subagent_to_terminal` 失败于：

```
assert child.get("kind") == "subagent"
AssertionError: assert None == 'subagent'
```

子会话本身创建成功（`parent_id` / `profile_id=task-worker` 均在），仅 `GET /api/chat/sessions/{id}/children` 返回的摘要里没有 `kind` 字段。

## 根因

`child_session_summary`（`packages/noesis-core/src/noesis/services/subagent_runtime_port.py:15`）在 run 投递统一重构（提交 `aaad3a09`）中收敛为目录摘要的单一构造点，返回字段集不含 `kind`。该函数被 executor 事件推送 / 目录快照 / 目录事件流三处共用，故 children 列表响应始终缺 `kind`，而集成测试仍断言它存在。属重构遗漏的字段面收窄，非本次 subagent 类型分发变更引入（该变更未触碰 children 摘要链路）。

## 复现

```bash
cd backend && uv run pytest tests/api/test_bg_subagent_real.py::test_super_agent_launches_bg_subagent_to_terminal -m integration -q
```

（需可达的 dev server 与有效模型网关；断言点在 children 列表返回后、终态等待前。）

## 备注

- 发现于 subagent-type-dispatch 变更的验证执行：真实 LLM 委派链路走通（子会话创建、任务终态均正常），仅该断言红。
- 修复方向二选一：摘要补 `kind` 字段（从 task 推 `kind="subagent"`，shell 目录条目相应给 `kind="shell"`），或测试改为不再断言 `kind`（若产品面确认 children 列表无需该字段）。
