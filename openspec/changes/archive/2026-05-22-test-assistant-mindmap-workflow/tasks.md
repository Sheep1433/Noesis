## 1. 文档对齐

- [x] 1.1 将 `docs/prd/agent-test-case/测试用例生成设计.md` §7.2 状态机与脑图说明与本变更 `design.md` 对齐（脑图不展示需求全文、采纳后更新树）
- [x] 1.2 在 `docs/test/test_tdd_design.md` 增补测试助手脑图/状态机测试点（仅要点）

## 2. 前端：状态机与脑图数据源

- [x] 2.1 新增 `scenesToMarkmap(scenes, selectedPointNames?)` 工具，输出 Markmap 用 Markdown
- [x] 2.2 重构 `TestAssistant.vue`：上传 → `parsing_doc` → `parse_done` → `gen_scenes` → `pick`；解析成功/生成中提示文案
- [x] 2.3 `testpoints-confirm-required` 时用全量 `tcScenes` 刷新脑图；禁止用 `extracted_markdown` 驱动 `initValue`
- [x] 2.4 用户确认勾选后：先刷新脑图为采纳子集，再 `resumeTestCase`；移除 `onFinish` 中用 `casesMarkdown` 覆盖脑图的逻辑
- [x] 2.5 空 `scenes` 时与后端错误态一致，不进入 `pick`

## 3. 联调与验证

- [ ] 3.1 手工路径：上传 `tc_upload_002.md` → 见解析/生成提示 → 列表与脑图均为场景/测试点 → 勾选 → 脑图缩为采纳集 → 用例流式输出
- [x] 3.2 `pnpm exec eslint` 覆盖改动文件；`uv run app.py` 冒烟
