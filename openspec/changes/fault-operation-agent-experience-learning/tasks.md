## 1. 配置与契约对齐

- [ ] 1.1 在 `backend/config/env.py` 增加经验学习相关配置项（总开关、写入开关、Top-K、检索超时），默认值保持关闭且与规格一致
- [ ] 1.2 对齐 `qa_service` 中 `FAULT_OPERATION_QA` 到 `FaultOperationAgent` 的入参，确认 `conversation_id` 与用户 ID 可传递到后续 Service（填补 design 中 Open Questions 的最小可行字段）

## 2. 数据层

- [ ] 2.1 设计并新增 MySQL 表/ORM（经验条目：状态、来源会话、所有者、摘要字段、时间戳、可选向量 id）
- [ ] 2.2 提供 Alembic/SQL 迁移或项目约定的初始化脚本，并在本地验证 `uv run app.py` 可启动

## 3. Service 与（可选）API

- [ ] 3.1 实现 `ExperienceService`（或等价命名）：创建草稿/激活、禁用、按用户范围检索、写入校验与日志
- [ ] 3.2 若需前端或运营操作：在 `backend/api/` 增加路由（`ResponseUtil`、401/403/404 语义一致），否则提供仅内部调用的 Service 方法并在 `test_tdd_design.md` 记录测试点

## 4. Agent 集成

- [ ] 4.1 在 `FaultOperationAgent.run_agent` 中于主推理前接入检索：开关判定、超时降级、长度上限合并上下文
- [ ] 4.2 记录本次注入的经验 ID 列表到日志（或已有遥测通道），便于审计与排障

## 5. 写入与触发

- [ ] 5.1 实现「可沉淀」信号入口（优先：内部 Service 方法 + 后续可接 PATCH/按钮）；校验 `experience_write_enabled` 与准入规则
- [ ] 5.2 入库前脱敏/长度与必选字段校验；失败时明确日志，不吞异常

## 6. 向量检索（可选第二阶段）

- [ ] 6.1 若启用 Qdrant：定义 collection 与 payload 字段，与 `knowledge-base` 或 SOP 索引策略对齐，避免跨集合误检
- [ ] 6.2 实现 RRF 或加权融合（经验 + 未来 SOP）的检索门面，单测或集成测试覆盖超时降级

## 7. 验证与文档

- [ ] 7.1 在 `backend/tests/` 或项目约定位置补充 Service/检索降级用例；在 `test_tdd_design.md` 先写测试点再落用例
- [ ] 7.2 更新 `docs/prd/agent-fault-operation/故障运维设计.md` 中与 SOP 闭环不一致的段落（仅一处权威描述，不写 v2 并行方案）
