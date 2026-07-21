## 1. P0：修复 execute Shell 操作符破坏

- [x] 1.1 审计并改写 `backend/agent/backends/path_rewrite.py`：禁止 `shlex.split` → `shlex.join` 整命令回写；按 design 目标态移除（或旁路）execute 虚拟路径 rewrite
- [x] 1.2 确认 `PrefixBackend.execute` / Docker Exec 路径不再依赖会破坏 `>`、`|`、`&&` 的 rewrite；filesystem 工具路由保持独立
- [x] 1.3 新增单测：`printf x > out.txt`、`mkdir -p a && printf y > a/b.txt`、管道命令在 `local_shell`（及 mock docker exec）下语义正确
- [x] 1.4 更新 Agent Prompt / Skills 文档：cwd=`/workspace`，产物用相对路径；不再教 `/skills/extensions` 等旧绝对路径给 Shell

## 2. P0：修复 Compose 宿主机 bind 路径

- [x] 2.1 梳理 `deploy/docker-compose.yml` 与 runner 环境变量：明确 `NOESIS_HOST_DATA_DIR`（及 skills 宿主机路径）必须是 **daemon 可见的宿主机路径**
- [x] 2.2 修改 `deploy/sandbox-runner/manager.py`：创建容器时 bind source 只用宿主机路径；禁止把 runner 容器内 `/data/noesis`、`/data/skills` 直接交给 daemon（除非配置已保证同源）
- [x] 2.3 保证 backend 数据卷与 sandbox workspace bind 指向同一宿主机目录（文档 + compose 注释写清）
- [x] 2.4 增加/更新部署检查：Agent 写入 `/workspace/notes.md` 后，backend/前端可读同一文件（Compose 集成探针脚本或手工验收清单）

## 3. P0：真正只读的双 Skills 挂载 + session workspace 隔离

- [x] 3.1 更新 `sandbox_mount_policy.py` / `mount_paths.py` / `agent_filesystem.py`：权威路径改为 `/skills/public`、`/skills/personal`；workspace 默认可写根 = `/workspace`
- [x] 3.2 修改 runner 挂载表：仅挂载当前 session workspace、public skills、personal skills；移除整棵 `users/{uid}` rw 挂载
- [x] 3.3 确认 `execute` 无法写入 `/skills/personal` 与 `/skills/public`（集成测：重定向/覆盖应失败）
- [x] 3.4 确认同用户其它 session、uploads、attachments、用户记忆默认不可经 Shell 访问
- [x] 3.5 `SkillsMiddleware.sources` 改为 `[public, personal]`（personal 在后覆盖同名）
- [x] 3.6 （可选过渡）filesystem 层保留 `/skills/extensions`、`/skills/custom`、`/user-skills` 别名 remap；不在 Shell rewrite 恢复

## 4. P1：Handle 缓存失效与自动重建

- [x] 4.1 修改 `backend/services/sandbox_service.py`：runner 404 / container missing 时清除 `_HANDLE_CACHE` 对应条目
- [x] 4.2 `ensure` + 执行路径：失效后重建并重试一次；仍失败则返回明确错误（禁止静默永久失败）
- [x] 4.3 单测：模拟 runner 先返回旧 handle、再 404、再成功重建的序列

## 5. P1：删除 AIO 兼容链与危险 chmod

- [x] 5.1 删除或停用 `AioSandboxBackend`、配置项 `aio`、`SANDBOX_AIO_IMAGE`、AIO 端口/浏览器 env 注入
- [x] 5.2 从 `pyproject`/lockfile/backend 镜像移除 `agent-sandbox` 依赖
- [x] 5.3 删除 `deploy/sandbox-runner/paths.py`（及调用方）中为 AIO UID=1000 做的递归 chmod/644 逻辑；改为创建目录时 chown 到沙箱非 root UID，保留脚本可执行位
- [x] 5.4 更新 `backend/AGENTS.md`、部署文档：生产 = Docker Exec + sandbox-slim；开发 = local_shell

## 6. P1：沙箱非 root 与基础隔离

- [x] 6.1 `deploy/sandbox-slim/Dockerfile` 声明非 root `USER`；探针 `id -u` ≠ 0
- [x] 6.2 runner 创建容器时设置基础资源限制（CPU/memory/PID 等，按现有配置项落地）；只读根文件系统若与写 `/workspace` 冲突则仅收紧 capabilities / no-new-privileges
- [x] 6.3 验证裸机 prod：workspace 文件属主为沙箱 UID 映射后 backend 用户可读写（或文档化需统一 UID）

## 7. P2：Skills 元数据失效 + 规格/代码收敛

- [x] 7.1 Skills 上传/删除 API 成功后 invalidate 该用户 session 的 Skills 元数据（或提供重扫钩子）；补测「同 session 上传后可见」
- [x] 7.2 删除仍存在的 `/skills/` 静态索引 backend（若有）及死代码路径重写
- [x] 7.3 全局替换 Prompt、常量、测试中的旧路径命名，与 delta specs 一致
- [ ] 7.4 将本 change 的 delta 在归档前与 `openspec/specs/{agent-sandbox,skills-filesystem,agent-runtime-paths,container-deployment}` 对齐准备（实现完成后 `/opsx:archive`）

## 8. 测试与验收

- [x] 8.1 更新既有 sandbox/filesystem 单测以匹配新挂载与路径（原 48 个及相关用例）
- [x] 8.2 新增缺失的真实场景测：Shell 操作符；个人 skills ro；stale handle 重建（可用 mock runner）
- [x] 8.3 Compose 手工/脚本验收：写入可见、公共 skills 可读、个人 skills 只读、无宿主机空目录被 Docker 自动创建成 root-owned 幽灵路径
- [x] 8.4 `cd backend && uv run pytest`（至少 sandbox / filesystem / skills 相关）通过；按需 `uv run app.py` 冒烟
- [x] 8.5 复杂结论追加到 `docs/NOTES.md`（只增不减）：路径收敛原因、Compose bind 陷阱、shlex 教训
