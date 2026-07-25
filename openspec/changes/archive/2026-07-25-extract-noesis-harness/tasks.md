## 1. Package skeleton

- [x] 1.1 创建 `backend/packages/harness` + `pyproject.toml`（noesis-harness）
- [x] 1.2 backend path 依赖 + `uv sync`

## 2. Move kernel

- [x] 2.1 `git mv backend/agent` → `packages/harness/noesis`
- [x] 2.2 全仓 import `agent` → `noesis`
- [x] 2.3 移除旧 `backend/agent/` 命名空间，不保留并行 shim

## 3. Break reverse deps

- [x] 3.1 tool_failure / HITL → noesis；迁移仓内调用并移除旧 domain 路径
- [x] 3.2 sandbox lifecycle → noesis.backends；迁移平台调用并移除旧 services 路径
- [x] 3.3 skills revision → noesis.skills.revision
- [x] 3.4 ChatAttachment / KB → `noesis.runtime.deps` + `noesis_server.services.harness_wiring`

## 4. Shared stream + evals

- [x] 4.1 `noesis.runtime.stream.stream_agent_events`；BaseAgent 委托
- [x] 4.2 Harbor worker 经 factory + stream

## 5. Docs / verify

- [x] 5.1 OpenSpec proposal/design
- [x] 5.2 specs delta + AGENTS/NOTES
- [x] 5.3 `uv run pytest tests/ -q`
- [x] 5.4 增加 noesis forbidden-import 与旧命名空间的 AST 边界回归测试
- [x] 5.5 增加 `noesis.factory` 无 FastAPI 启动、无平台 wiring 的子进程导入 smoke test

## 6. Correct package namespace

- [x] 6.1 将 `packages/harness/harness` 更名为 `packages/harness/noesis`
- [x] 6.2 将独立 `packages/llm/llm` 并入 `packages/harness/noesis/llm`，删除 `noesis-llm` distribution
- [x] 6.3 全仓迁移 `harness.*` / `llm.*` import 与 mock patch 路径到 `noesis.*`
- [x] 6.4 更新包元数据、锁文件、文档与架构边界测试
- [x] 6.5 运行 OpenSpec 校验和 backend 全量回归

## 7. Make harness host-independent

- [x] 7.1 将 Agent 所需 env/yaml/path/MCP/checkpointer 配置迁入 `noesis.config`
- [x] 7.2 将 harness logging/path 依赖迁入 noesis，消除顶层 `config/common` import
- [x] 7.3 让 `eval_runtime` 默认只管理内存 checkpointer，Harbor 不加载平台 services wiring
- [x] 7.4 补齐 package dependencies 与隔离 wheel import 测试
- [x] 7.5 运行 OpenSpec 校验、静态边界检查与 backend 全量回归

## 8. Normalize internal package layout

- [x] 8.1 合并 `profiles` / `case_generate` 为 `noesis.agents` / `noesis.agents.case_generate`
- [x] 8.2 将 attachments、stream、HITL、deps、logging 收敛到 `noesis.runtime`
- [x] 8.3 全仓迁移 import、mock patch 路径、文档与代码锚点
- [x] 8.4 增加顶层目录归属回归测试
- [x] 8.5 运行 wheel smoke、OpenSpec 校验与 backend 全量回归

## 9. Stabilize public subsystem APIs

- [x] 9.1 为 `noesis.config` / `noesis.runtime` 增加惰性公共门面与显式 `__all__`
- [x] 9.2 增加公共门面等价性、轻量导入和 wheel 导入回归测试
- [x] 9.3 更新调用文档并运行 OpenSpec、边界测试与 backend 全量回归

## 10. Consolidate the platform host

- [x] 10.1 将平台模块迁入 `backend/noesis_server` 并全仓迁移到 `noesis_server.*` import
- [x] 10.2 将 sandbox runner、Telegram runtime、KB seed 编排移动到 bootstrap/application 归属
- [x] 10.3 消除 `kb → services` 反向依赖并删除 `services.qa_service` 兼容入口
- [x] 10.4 增加平台顶层布局、旧 import 与内部依赖方向的 AST 回归测试
- [x] 10.5 更新启动入口、Alembic、文档并运行 OpenSpec、启动 smoke 与 backend 全量回归

## 11. Thin the Knowledge Base API

- [x] 11.1 将集合、上传、检索业务编排下沉到 `services.knowledge_base_service`
- [x] 11.2 将 Knowledge Base API 收敛为 FastAPI transport 薄层并补边界测试
- [x] 11.3 运行 KB 定向回归与 backend 全量回归
