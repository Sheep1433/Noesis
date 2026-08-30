# 网关风控 451 后 SSE 挂流：run 永久卡「生成中」、停止无效、无法新建会话

> 状态：✅ 已修复（2026-08-30，`54fa7a80`）
> 发现日期：2026-08-30
> 环境：zzqnoesis.cn 生产，kilo.ai 网关（stepfun provider 返回 `statusCode: 451, The content you provided or machine outputted is blocked.`）

## 现象

- 触发内容风控后，会话**永久停留在「生成中」**（前端持续转圈，无错误提示）
- 点击停止无效：UI 状态不变
- **无法新建会话**：新会话发首条消息无响应/被拒

## 根因（本地假网关复现实证）

**网关风控拦截后不关连接，保持 SSE 只发 keep-alive ping。而 httpx 读超时按「任意字节」计时——每个 ping 都重置计时器**，导致：

1. `request_timeout=120`（读超时）对 ping 挂死流**永不触发**：5s 读超时的客户端对着 ping 流 15s+ 不超时（复现实测）
2. 模型调用无异常、无返回、无重试——run 停在 RUNNING，直到 15/30 分钟看门狗才兜底
3. 卡死 run 占满 `run_max_active_per_user=4`，新会话首条消息的 run 直接 RunCapacityExceeded →「无法新建会话」
4. 停止依赖 producer 取消后的收尾路径，存在收尾失败窗口；进程重启后注册表为空时 stop 全程 no-op 但 DB 行永远 RUNNING

对照排除：451 作为**流内 error 帧**返回的路径是健康的（langchain 抛 `APIError` → `LLMErrorHandlingMiddleware` 分类不可重试 → 降级文案 → run 正常终态）——事故流是「挂住不发任何内容」的变体。

## 修复

1. **流级空闲超时（根因）**：`ChatOpenAICompatible._astream` 按「真实生成 chunk」计时（`factory.py`），ping/注释帧不重置；`request_timeout` 内无 chunk 抛 `StreamIdleTimeoutError` → 中间件按瞬时错误退避重试 → 耗尽降级「模型响应中断」→ run 可达终态。慢速活流（每 0.1s 一 chunk）不被误杀（有测试锁定）
2. **stop 兜底收口**：`RunService.stop` 在内存 stop 后复查 DB 行，仍非终态则强制 finalize 为 `interrupted/stopped`（覆盖重启后注册表为空、producer 收尾失败两种来源）

启动对账经查已由 `RunRecoveryService` 覆盖（重启遗留 run 收口 interrupted），无需新增。

## 复现方式

本地起假网关（`200 + SSE 头 + 每 2s 发 ": ping" 注释帧，永不结束`），用 `ChatOpenAICompatible` 指向它：读超时不触发、流无限挂起；cancel 可穿透（0.00s）验证了 stop/watchdog 的有效性边界。

## 状态流转

- 2026-08-30 🆕 新增：生产事故定位，双修复提交 `54fa7a80`，全量 1261 测试通过
- 2026-08-30 ✅ 已修复
