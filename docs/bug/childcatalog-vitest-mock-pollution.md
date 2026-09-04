# 前端全量 vitest：childCatalogRealtime 套件级失败（测试污染）

**状态**：🆕 新增
**日期**：2026-09-04
**执行方**：ZCode（测试观察，随 citation-supply-tightening 验证轮发现；只记录不修复）

## 现象

`pnpm vitest run`（全量）时 `__tests__/childCatalogRealtime.test.ts` 以**文件级错误**失败：

```
[subagent] stream failed Error: [vitest] No "getAgentRun" export is defined on the "@/api/chat" mock.
```

同文件**单独运行恒通过**（9/9）；全量 241+ 个用例全部 passed，仅该文件被记为 failed。在 dev 基线（c2315372^）与 feat worktree 两个环境下均可复现（2/2），单跑即过（2/2）。

## 判定要点

- 非 citation-supply-tightening 引入：dev 全量基线同样失败。
- 疑似跨文件测试污染：其他测试文件对 `@/api/chat` 的 `vi.mock` 未导出 `getAgentRun`，vitest 模块注册表在并行/复用场景下泄漏到本文件（报错指向 `SubagentConversationView/index.vue:535` → `useRunStreamClient`）。
- 修复方向（供后续处理者）：给本文件的 mock 补 `getAgentRun` 导出，或改用 `importOriginal` 部分 mock；亦可排查 vitest 线程池下 `vi.mock` 工厂的全局泄漏面。
