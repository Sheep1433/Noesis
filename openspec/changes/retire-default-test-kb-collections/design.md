## Context

当前 `backend/server/main.py` 在 Qdrant 初始化后调用 `backend/server/bootstrap/kb.py`。该启动逻辑既创建 `requirement_docs`、`test_case_docs`，又调用 `KbCollectionConfigService.ensure_defaults_for_qdrant_collections` 为已有 Qdrant 集合补 PostgreSQL 配置。测试用例链路逐步退场时，这两个职责需要分开处理。

## Goals / Non-Goals

**Goals:**

- 服务启动不再隐式创建测试用例链路专用集合。
- 已有用户集合继续获得缺失的 PostgreSQL 默认配置。
- 启动生命周期仍保持 Qdrant 初始化后、其它运行时服务启动前完成集合配置同步。

**Non-Goals:**

- 不删除已经存在的 `requirement_docs`、`test_case_docs` 数据。
- 不删除测试用例页面、Agent、配置字段或上传接口。
- 不改变知识库 API 与 Qdrant 数据结构。

## Decisions

1. 将 `ensure_default_kb_collections` 改为只同步已有集合配置的 `sync_existing_kb_collection_configs`。名称直接描述保留的职责，避免继续使用“默认知识库”语义。
2. 删除启动模块中的默认集合常量、固定向量维度和 `create_collection` 调用。测试用例链路如仍需要集合，必须通过显式上传或运维操作创建。
3. 保留 `KbCollectionConfigService.ensure_defaults_for_qdrant_collections`。它遍历已有集合，不会创建 Qdrant collection，因此不会重新引入默认库。
4. 不在本阶段执行数据删除。已有集合可能包含用户数据，自动删除不满足安全边界。

## Risks / Trade-offs

- [风险] 新环境首次使用测试用例功能时目标集合不存在 → 由现有显式上传/创建流程处理；后续彻底移除测试用例链路时再删除相关入口。
- [风险] 改名遗漏生命周期测试 patch 点 → 更新 `test_server_lifespan.py`，并增加断言确认启动同步不会调用 `create_collection`。
- [取舍] 已有默认集合继续显示 → 本阶段避免自动删数据；如确认无数据价值，再单独提供显式迁移或删除动作。
