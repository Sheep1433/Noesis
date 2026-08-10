## 1. Harness 公开评测能力

- [x] 1.1 使用 ContextVar 实现公开 `temporary_checkpointer`，并让 eval bootstrap 停止修改 `_saver`
- [x] 1.2 增加可恢复的临时 KB runtime binding，并补依赖恢复测试
- [x] 1.3 公开 `build_chat_model`，迁移 Harbor 对 `_build_chat_model` 的调用并补模型入口测试

## 2. Agent 评测公共运行层

- [x] 2.1 新增公共 Agent event collector、run result 和 manifest 序列化
- [x] 2.2 迁移 BrowseComp 使用公共结果模型
- [x] 2.3 迁移 Harbor 公共文本、工具、usage 和错误收集，同时保留 Harbor trajectory 制品

## 3. Agentic RAG 与 Case 展示

- [x] 3.1 新增 `evals.agent.rag` JSONL loader、Harness GeneralQAAgent runner 和命令入口
- [x] 3.2 实现 KB Tool 调用、期望来源命中和回答结果评分，并提供不依赖外部服务的测试
- [x] 3.3 修复 Case RAG provider 的旧 patch 路径并增加回归测试，保持展示流程可运行

## 4. 文档与验证

- [x] 4.1 更新 `backend/evals/README.md`、`evals.agent` 入口和示例 fixture，明确 Retrieval 与 Agentic RAG 边界
- [x] 4.2 运行相关评测单测、Harness package boundary/wheel smoke 和 `git diff --check`
