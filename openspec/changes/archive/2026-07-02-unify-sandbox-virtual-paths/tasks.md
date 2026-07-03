## 1. 路径 rewrite 核心实现

- [ ] 1.1 新增 `PathRewriteContext`（`user_id`, `session_id`, `backend_kind`, `workspace_prefix`, `extensions_prefix`, `custom_skills_prefix`, `memory_*_path`）由 `build_agent_filesystem_backend` 构造
- [ ] 1.2 新增 `rewrite_virtual_paths_in_command(command, *, ctx)`：**token 级**（`shlex` 分词 + Tier1 显式前缀 + Tier2 workspace 根 + 系统路径 denylist），**禁止**裸全串 `str.replace`
- [ ] 1.3 **仅** workspace `PrefixBackend.execute()` 调用 rewrite；extensions/custom `PrefixBackend` **不**配置映射表
- [ ] 1.4 移除 `AioSandboxBackend._rewrite_custom_skill_paths_in_command`，保留 `exec_dir`、mutex、浏览器 env

## 2. Prompt 与文档

- [ ] 2.1 更新 `execution.py`：cwd 为 workspace 根；`/research/foo` ≡ `research/foo`；workspace 根文件可用 `/notes.md` 或相对路径；**同一 command 内**用 `&&` 链式 `cd`
- [ ] 2.2 `/memory/` 表述与 `add-super-agent-user-memory` 一致（虚拟路径 `/memory/...`，非「execute 禁止读记忆」）
- [ ] 2.3 检查 `super_agent.py` 等：单层 `/skills/{name}` → `extensions/` / `custom/`
- [ ] 2.4 design **Known Limitations**：`pwd` 物理输出、`cat /workspace/AGENTS.md` bypass 不在本 change 拦截

## 3. 回归测试

- [ ] 3.1 workspace `PrefixBackend.execute`：`/research/`、`/skills/extensions/`、`/skills/custom/` 与 `read_file` 同目标
- [ ] 3.2 workspace 根：`write_file("/notes.md")` 后 `execute("cat /notes.md")` 成功
- [ ] 3.3 `/memory/`：`execute("cat /memory/AGENTS.md")` rewrite 后与容器 `/workspace/AGENTS.md` 一致（mock 或 local_shell）
- [ ] 3.4 误伤负例：`echo "see /research/foo"` 输出 **SHALL NOT** 含物理 workspace 前缀
- [ ] 3.5 local_shell + aio 各至少一例；迁移原 `test_execute_rewrites_custom_skill_paths` 至 PrefixBackend 层

## 4. 验证与归档

- [ ] 4.1 `cd backend && uv run pytest tests/test_agent_filesystem.py tests/test_aio_sandbox_backend.py tests/test_sandbox_backend_factory.py -q`
- [ ] 4.2 `cd backend && uv run app.py`
- [ ] 4.3 **先**归档 `unify-sandbox-virtual-paths` 合并 delta 至主 spec；**再**归档 `add-super-agent-user-memory`（避免 `/memory/` 规格互相覆盖）
