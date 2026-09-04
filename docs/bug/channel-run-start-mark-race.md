# channel/automation 启跑标记竞态覆写终态 run（毒丸数据源头）

**状态**：✅ 已修复
**日期**：2026-09-03

## 现象

prod 启动必挂：lifespan 内 `RunRecoveryService.recover_orphaned_runs` 抛 `RuntimeError("assistant terminal compare-and-set failed")`，uvicorn 以 exit code 3 退出，error 日志无 traceback。触发数据为一条 automation run（3a49aac1）：assistant 消息已是终态 error（「操作失败，请稍候重试。」），run 行却停在 running。

## 根因

`channel_run_service.run_channel_agent` 在 `run_manager.start(...)` 返回后，用**无条件 UPDATE** 把 run 行标记为 running：

```python
await db.execute(
    TAgentRun.__table__.update()
    .where(TAgentRun.id == run_id)          # ← 无状态前置条件
    .values(status=RunStatus.RUNNING.value, ...)
)
```

producer 已在 `run_manager.start` 内并发执行：模型毫秒级失败（如网关 503）时，producer 的终态链路（RunError → terminal candidate → `repository.finalize`：run→error + 消息→error 同事务提交）可能**先于**主协程的这条 UPDATE 落库。UPDATE 随后把终态 error 覆写回 running——「消息已终态而 run 遗留非终态」的毒丸就此产生，且每次启动对账都会踩到（消息 CAS 要求 streaming 状态，落空即抛错炸 lifespan）。

消息 extra 的形状（旧键 + finish_reason/error_code/error，与 `finalize` 的消息写入完全一致）证实写入方是终态链路本身——数据本是对的，是被覆写坏的。

## 修复

启跑标记收敛为单一入口 `RunService.mark_run_started`（CAS：`compare_and_set_status([QUEUED], RUNNING)`，主链路 `start_queued_run` 与 channel `run_channel_agent` 共用）：终态先落库时 CAS 落空即 no-op，不覆写。全仓扫描确认该类「与 run 生命周期竞态的无条件状态写入」仅此一处（其余写入点均 CAS 条件化或处于启动期单写手上下文）；`resume_channel_hitl` 的同位置已是 CAS（`[HITL_PENDING] → RUNNING`），无需改。

配套：启动对账侧对历史毒丸免疫（消息已终态时仅收口 run 行，`run_recovery_service.py` 预判分支），两道防线独立生效。

## 修复方与验证

- 修复方：ZCode（2026-09-03）；启动对账免疫由另一会话先行实施，本次评审后简化为预判分支。
- 回归：`tests/test_channel_delivery_result.py::test_channel_run_start_mark_is_cas_not_overwrite`（fast-fail producer 先落终态，断言启跑标记走 queued 前置 CAS——旧代码下必红）；后端全量 1388 绿。
- 生产库存量：毒丸 run 已由启动对账收口为 interrupted/server_restart，非终态 run 归零。
