## 1. 配置与文档

- [x] 1.1 需求文档统一使用 `requirement_docs`；种子文件导入该 collection；更新 `.env.example`
- [x] 1.2 更新 `docs/prd/agent-test-case/测试用例生成设计.md` §4：开源「上传=选文档」、双路召回顺序、测试点/用例语义

## 2. 场景级 RAG 与并行

- [x] 2.1 新增 `build_scene_rag_context(scene, *, file_name)`：双路 hybrid（`requirement_docs` 全库 + `test_case_docs`）；固定 Markdown 小节顺序
- [x] 2.2 重构 `generate_test_cases_node`：**按场景分组**并行；场景内共享 context，测试点级并发生成用例
- [x] 2.3 移除阶段 A/B 对 LLM 输出 `chunk_indexes` 的依赖；删除 per-point `retrieve_chunks` 路径
- [x] 2.4 `retrieval_trace` 以 `scene_name` 为键写入 eval/日志

## 3. 提示词与语义

- [x] 3.1 `_build_scenes_testpoints_prompt`：去掉 `chunk_indexes` 字段说明；测试点仅标题级
- [x] 3.2 `_build_case_prompt`：注入共享 `scene_rag_context`；用例须含 `test_steps` / `expected_results`

## 4. 验证

- [x] 4.1 `backend/tests/` 单测：mock 检索，断言双路拼接顺序与 channel trace
- [x] 4.2 `uv run app.py` 冒烟；手工：上传文档 → 勾选测试点 → 检查日志含各 channel hit
- [x] 4.3 在 `docs/test/test_tdd_design.md` 登记多知识库 RAG 与 trace 测试点
