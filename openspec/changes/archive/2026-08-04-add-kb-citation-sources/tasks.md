## 1. 规范

- [x] 1.1 保持 Prompt Markdown citation，不使用 structured answer 或虚拟 Tool
- [x] 1.2 明确 Web/KB 统一 `[n]`、确定性匹配、可点击上标和跳转行为

## 2. Agent 与 Harness

- [x] 2.1 COMMON_QA 和 SuperAgent 移除 citation `response_format` 与 provider allowlist
- [x] 2.2 删除 `CitedAnswer`、structured response adapter 和虚拟 Tool 过滤
- [x] 2.3 建立共享 Prompt citation 约束和 golden tests

## 3. 平台与前端

- [x] 3.1 解析 Markdown 编号和参考资料，与本轮 Web/KB retrieval 做唯一确定性匹配
- [x] 3.2 实现 Web 安全外链和 KB 受认证保护的 Collection 文档跳转
- [x] 3.3 前端将已匹配 `[n]` 渲染为可点击上标，并由已持久化 text/retrieval 在刷新后重建
- [x] 3.4 用回答末尾来源入口和统一抽屉展示独立 retrieval results，删除旧折叠块

## 4. 验证

- [x] 4.1 后端覆盖 Web/KB Prompt 元数据、普通 Markdown streaming 和 retrieval 持久化
- [x] 4.2 前端覆盖上标渲染、Web/KB 点击、伪造/多义拒绝和刷新重建
- [x] 4.3 运行真实模型 Web 与 KB citation 端到端测试
- [x] 4.4 运行 code review、定向回归与 `openspec validate add-kb-citation-sources --strict`
