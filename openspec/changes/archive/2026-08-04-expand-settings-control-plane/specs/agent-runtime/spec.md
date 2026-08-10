## ADDED Requirements

### Requirement: Agent 上下文预览 SHALL 与真实装配共享解析器
运行时 SHALL 暴露不执行模型的上下文解析能力，供设置服务生成指定用户与 Agent profile 的来源清单和最终编译预览；预览与真实 run SHALL 共享记忆、规则和提示词解析器，SHALL NOT 在 API 层复制拼装规则。

#### Scenario: 预览不产生运行副作用
- **WHEN** 设置服务请求上下文预览
- **THEN** 运行时 SHALL NOT 调用模型、创建 checkpoint、写入 `/memory/` 或创建聊天消息

### Requirement: L2 记忆查询 SHALL 保持用户路径隔离
运行时或记忆服务 SHALL 只在当前用户权威记忆目录内列出和搜索 L2 日记，规范化并校验日期/相对路径；查询结果 SHALL NOT 改变 L0/L1 默认注入规则。

#### Scenario: 路径穿越查询
- **WHEN** L2 查询参数试图越出当前用户记忆根
- **THEN** 系统 SHALL 拒绝请求且 SHALL NOT 读取其它用户或宿主文件
