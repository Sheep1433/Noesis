## Why

~~原动机：将 Agent 运行时与产品 Harness 物理拆分（`noesis_runtime/`、Profile 注册表、Harbor 同入口）。~~

**状态（2026-07-23）：SUPERSEDED / 搁置**

整包搬家（factory/middleware/backends 迁包 + 四 Agent 改 Profile + 评测全切）影响面过大，短期不解锁 Telegram/微信等多通道。其真正有产品价值的部分——**Run 事件与投递解耦**——改由 **`unify-run-delivery`** 在现有 `agent/` + `qa_service` 上自立完成。

远期若 Delivery 已稳且目录仍痛，可另开 **slim** change（仅轻量 `AgentRunService` / 评测同入口，**不做**整包迁目录）。本 change **不再实现**。

## What Changes

- **无**：本 change 不再落地代码。
- 历史设计文档保留供参考；规格 delta **不**合并进 main（archive `--skip-specs`）。

## Capabilities

### New Capabilities

- （搁置）原 `agent-runtime-harness` — **不**归档进主规格。

### Modified Capabilities

- （无）原对 `platform-chat` / `agent-offline-eval` 的委托要求改由 `unify-run-delivery` 以 Fan-out 方式覆盖流式侧；评测同入口不在本阶段强制。

## Impact

| 区域 | 影响 |
|------|------|
| 活跃变更 | 以 `unify-run-delivery` 为 SSE/多通道主线 |
| 本目录 | 归档为 `archive/YYYY-MM-DD-extract-agent-runtime-harness`（skip-specs） |
