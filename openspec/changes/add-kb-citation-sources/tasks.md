## 1. 规范替换

- [x] 1.1 将 proposal/design/specs 从 typed annotation 改为 Prompt Markdown citation
- [x] 1.2 明确 Web、KB、无来源和 retrieval-only 行为

## 2. Agent 与 Harness

- [x] 2.1 COMMON_QA 和 SuperAgent 移除 citation `response_format` 与 provider allowlist
- [x] 2.2 删除 `CitedAnswer`、structured response adapter 和虚拟 Tool 过滤
- [x] 2.3 建立共享 Prompt citation 约束和 golden tests

## 3. 平台与前端清理

- [x] 3.1 删除 typed answer/annotation SSE 处理和 durable annotation 投影
- [x] 3.2 删除 message annotation、citation resolve API 和前端 offset marker 注入
- [x] 3.3 保留独立 retrieval results，并统一使用“本轮检索结果”语义

## 4. 验证

- [x] 4.1 后端 citation、stream、retrieval 定向测试通过
- [x] 4.2 前端单测、lint 和 build 通过
- [x] 4.3 运行当前配置真实模型的 Web citation 集成测试
- [x] 4.4 运行 code review 与 `openspec validate add-kb-citation-sources`
