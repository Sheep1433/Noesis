# user-data-layout Specification

## Purpose

> **已合并**：本能力已并入 [`agent-runtime-paths`](../agent-runtime-paths/spec.md)。请改读该 spec；本目录保留仅为兼容历史变更引用。

## Requirements

### Requirement: 权威规格 SHALL 位于 agent-runtime-paths

用户数据根 `.data/users/`、会话子树布局与 `delete_session_data` **SHALL** 以 `openspec/specs/agent-runtime-paths/spec.md` 为单一事实来源；本目录 **SHALL NOT** 再定义独立 SHALL 条款。

#### Scenario: 查阅用户数据布局

- **WHEN** 开发者需要确认 `get_user_root` / `get_workspace_dir` 等路径 API
- **THEN** SHALL 阅读 `agent-runtime-paths`，**SHALL NOT** 以本文件正文为准
