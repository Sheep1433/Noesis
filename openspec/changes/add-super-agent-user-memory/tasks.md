## 1. 用户记忆路径与 seed

- [ ] 1.1 `user_data_paths.py`：`get_user_agents_md_path`、`get_user_profile_md_path`、`ensure_user_memory_files`（含 AGENTS/USER seed 模板）
- [ ] 1.2 单测：路径解析、ensure 幂等、非法 user_id

## 2. CompositeBackend `/memory/` 路由

- [ ] 2.1 `mount_paths.py`：`AGENT_MEMORY_ROUTE`、`AGENT_MEMORY_AGENTS_FILE`、`AGENT_MEMORY_USER_FILE`
- [ ] 2.2 `agent_filesystem.py`：`MemoryBackend`（AGENTS 可写、USER 只读）；接入 `build_agent_filesystem_backend`
- [ ] 2.3 单测：read/write AGENTS、拒绝写 USER、与 `/research/` 隔离

## 3. MemoryMiddleware

- [ ] 3.1 `agent/middlewares/memory_prompt.py`：`NOESIS_MEMORY_SYSTEM_PROMPT`（中文）
- [ ] 3.2 `MemorySyncMiddleware`（或等价）：`/memory/AGENTS.md` 写入后刷新 `memory_contents`
- [ ] 3.3 单测：注入块、缺失文件跳过、编辑后 sync

## 4. SuperAgent 与 prompt 清理

- [ ] 4.1 `deep_research_agent.py` → `super_agent.py`，类名 `SuperAgent`
- [ ] 4.2 挂载 `MemoryMiddleware` + 现有 Skills/Todo/Web；子 Agent `task-worker` 不挂 Memory
- [ ] 4.3 删除 `prompts/deep_research.py`；`__init__.py` 移除 `DEEP_RESEARCH*` profile
- [ ] 4.4 `test_super_agent_prompt.py` 替换旧深度研究 prompt 测试

## 5. qa_type BREAKING 重命名

- [ ] 5.1 `IntentEnum`：`SUPER_AGENT_QA` 替换 `DEEP_RESEARCH_QA`
- [ ] 5.2 `qa_service.py`、`agent_lifecycle.py`、停止/取消路径
- [ ] 5.3 前端：`chat.vue`、`DefaultPage.vue`、`theme.ts`、`QatypeIcon`、`TableModal`、文案与 gradient key
- [ ] 5.4 历史 `extra.qa_type=DEEP_RESEARCH_QA` 展示映射（只读）

## 6. 评测与配置

- [ ] 6.1 `evals/agent/_agent.py`：`run_super_agent`；wildclaw/browsecomp/loadtest qa_type
- [ ] 6.2 `.env.example`、`config.example.yaml`、README/evals 文档中的 DEEP_RESEARCH 措辞

## 7. 规格与验证

- [ ] 7.1 `uv run pytest tests/ -q`（含新增 memory/filesystem/super_agent 测）
- [ ] 7.2 `uv run app.py` 冒烟：`SUPER_AGENT_QA` 流式一轮
- [ ] 7.3 前端 `pnpm lint`（触及文件）
- [ ] 7.4 归档前合并 delta 至 `openspec/specs/`，删除 `agent-deep-research`
