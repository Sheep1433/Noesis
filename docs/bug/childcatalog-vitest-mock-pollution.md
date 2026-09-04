# childCatalogRealtime 全量套件级失败与断流自愈终态重载死循环

> 状态：✅ 已修复
> 日期：2026-09-04
> 环境：Node 25 / vitest 3.2.7 / happy-dom；全量 `pnpm vitest run` 与单文件运行

## 现象

- 全量 vitest 时 `__tests__/childCatalogRealtime.test.ts` 以文件级错误失败：`[vitest] No "getAgentRun" export is defined on the "@/api/chat" mock`；单文件运行 9/9 通过
- 修复中途状态：给 mock 门面补齐导出、`getAgentRun` 解析终态快照后，文件**单跑也挂死**——vitest worker 持续 ~160% CPU、零用例完成、5s 测试超时永不触发（挂死可跨 dev 基线复现，与本仓其它改动无关）

## 根因（两层）

1. **mock 门面缺导出（套件级失败的直接原因）**：`vi.mock('@/api/chat')` 门面未覆盖 `SubagentConversationView` 消费的全部导出。断流自愈路径（订阅失败 → resync → `getAgentRun`）在门面上抛「缺导出」错误：单跑时 rejection 落在用例内、被 `consumeStream` catch 吸收（只污染 stderr）；全量跑时 rejection 时序后移到用例结束，成为未处理拒绝 → 文件级失败。此前记录怀疑「跨文件 mock 污染」，实测为 rejection 时序差异，非模块注册表泄漏（补全门面后全量即过）。
2. **组件终态重载死循环（补导出后暴露的真实缺陷）**：`applyEvent` 的 `run-finished` 分支触发 `loadConversation()`，其尾部对 `activeRunId` **无条件再订阅**（`consumeStream` 每次重置 `terminalSeen`）。当「订阅立即失败（网关错误 / 响应异常）+ resync 拿到终态快照」同时成立：订阅失败 → resync 终态 → `run-finished` → 重载 → 再订阅 → 再失败 → …… 无退避间隔的即时循环；全链皆已 resolve 的 promise 时退避 `setTimeout` 永远走不到，宏任务（含 vitest 超时）被微任务饿死——表现为永久挂死。该组合在生产环境同样成立（SSE 订阅 5xx 而 run 查询 API 正常时为无退避的 API 热循环）。

## 修复

- **组件（根因）**：`SubagentConversationView.consumeStream` 入口守卫——reducer 已知该 `run_id` 处于终态时不再（重）订阅；新 run id（排队续跑 / followup）不受影响
- **测试**：mock 门面补齐 `getSession` / `getAgentRun` / `resumeAgentRunHitl` / `stopAgentRun`；默认 `getAgentRun` 解析终态快照（断流自愈安静收口）；排队 CRUD 用例改用 running 快照（终态 + 队列会按设计触发队首自动提交，场景互斥）；新增回归用例钉住「终态快照后不再重订阅」（撤掉守卫即挂死，红灯已验证）

## 验证

- 单文件：10/10 通过（原 9 条 + 新增回归 1 条）
- 全量：37 文件 / 261 用例全过，21s（修复前：文件级失败；中途状态：套件挂死）

## 状态流转

- 2026-09-04 🆕 新增：全量套件级失败（缺导出 rejection 时序），只记录不修复
- 2026-09-04 ✅ 已修复：mock 门面补全 + 终态重载死循环组件守卫；全量套件恢复绿色
