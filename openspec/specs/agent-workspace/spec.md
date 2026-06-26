# agent-workspace Specification

## Purpose

> **已合并**：本能力已并入 [`agent-runtime-paths`](../agent-runtime-paths/spec.md)。请改读该 spec；本目录保留仅为兼容历史变更引用。

## Requirements

### Requirement: 权威规格 SHALL 位于 agent-runtime-paths

会话工作区路径、删会话磁盘清理与沙箱挂载边界 **SHALL** 以 `openspec/specs/agent-runtime-paths/spec.md` 为单一事实来源；本目录 **SHALL NOT** 再定义独立 SHALL 条款。

#### Scenario: 查阅工作区布局

- **WHEN** 开发者需要确认 `.data/users/{uid}/sessions/{sid}/workspace/` 等路径约定
- **THEN** SHALL 阅读 `agent-runtime-paths`，**SHALL NOT** 以本文件正文为准
