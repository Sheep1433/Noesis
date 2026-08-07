## 1. 记忆整理核心

- [x] 1.1 实现消息文本清洗、问答配对、稳定条目标识与结构化 Markdown 编解码
- [x] 1.2 实现按用户/日期查询已完成消息并原子写入 L2 的 MemoryDreamService
- [x] 1.3 扩展 UserMemoryService，提供条目级日期/分类/关键词检索
- [x] 1.4 实现来源消息权限校验和有限相邻上下文读取

## 2. API 与自动运行

- [x] 2.1 在 `/api/user/memory` 增加手动整理、条目搜索和来源读取接口
- [x] 2.2 实现上一自然日的周期整理器并接入应用生命周期
- [x] 2.3 补充整理幂等、用户隔离、过滤和 API Service 测试

## 3. Agent 工具

- [x] 3.1 实现绑定当前用户的 `search_memory` 与 `get_memory_source` tools
- [x] 3.2 将记忆 tools 注入 SuperAgent 主 Agent 和 task-worker，并补工具测试

## 4. 设置页

- [x] 4.1 删除 USER.md 常用字段 API 调用、状态与表单，只保留 Markdown 原文编辑
- [x] 4.2 扩展前端 memory API 类型与调用方法
- [x] 4.3 增加日期选择、手动整理状态和条目级检索结果展示

## 5. 验证与文档

- [x] 5.1 运行后端相关测试与完整测试，修复回归
- [x] 5.2 运行前端 lint/build，修复新增错误
- [x] 5.3 更新长期架构文档，说明 L0/L1/L2 数据流、隐私边界与失败行为
