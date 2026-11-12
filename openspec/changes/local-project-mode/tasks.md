# Tasks: 本地项目目录模式

## M1 能跑（`--project` + 真实目录 + 全放行）

- [ ] 1.1 `noesis chat` 加 `--project` 参数（缺省当前目录）：校验目录存在，设 `NOESIS_DATA_DIR=<project>/.noesis` 与 workspace 根，透传 factory 装配
- [ ] 1.2 本地组合根：`inner=create_local_shell_backend()`，`AgentPathBackend(strip_root=None, canonicalize=False)`——路径直通语义（agent 路径 = 宿主机路径）；`execute` 走宿主机 shell
- [ ] 1.3 回归钉住：路径直通（`read_file /abs/path` 映射不变）、memory/docker 后端既有行为不回归（`uv run noesis chat` 无 `--project` 时维持现状）
- [ ] 1.4 M1 验收：裸 git 仓库零配置跑通对话 + `read_file` / `write_file` / `edit_file` / `execute`（仅在一次性测试仓库上，读写尚无边界）

## M2 敢用（权限层）

- [ ] 2.1 `ProjectBoundaryBackend`（SandboxBackendProtocol 包装，框架无关拦截器——参照 dcode `RepositoryBounds` 形态）：写/删目标在项目根外 → 可解释拒绝；读放行；单测覆盖路径逃逸形态（`..`、符号链接、绝对路径）
- [ ] 2.2 HITL 分级扩展：`InterruptOnConfig` 支持参数谓词（execute 命令 / 网络工具 URL / 写路径模式）；全局三档 `manual / auto / yolo`（dcode 词汇：yolo 需显式确认进入），缺省策略 = 项目外/执行/网络询问、项目内写按 auto
- [ ] 2.3 CLI 确认 UI：终端 y/n（含「本次允许 / 永久允许 / 拒绝」三选）；拒绝后返回可解释错误，Agent 会话不中断
- [ ] 2.4 授权记忆：`.noesis/local.db` permission_grant 表（工具 + 参数模式 + 作用域）；「永久允许」命中免确认；`--yes-all` 与配置文件预置放行集
- [ ] 2.5 M2 验收：让它改一个仓库时项目外路径必弹确认；拒绝后 Agent 能继续工作、不卡死

## M3 用得久（持久化）

- [ ] 3.1 新增可选依赖 `langgraph-checkpoint-sqlite`；CLI 组合根换 `AsyncSqliteSaver`（`.noesis/checkpoint.db`，沿用 `temporary_checkpointer` 覆盖机制）；aiosqlite busy_timeout、状态目录硬化（dcode 验证的生产细节）
- [ ] 3.2 `local.db` 轻存储：session / message / todo_item 三表 + CLI repository（终态「先比较再写入」单行 CAS，格式带版本号）；会话列表 / 历史加载 / `--session` 续聊
- [ ] 3.3 会话锁：同项目第二个 CLI 进程给可解释错误（文件锁，dcode `_session_lock` 同款问题）
- [ ] 3.4 记忆按项目隔离：memory store 落项目内 `.noesis/`（与平台用户目录隔离），多项目互不串扰回归
- [ ] 3.5 M3 验收：重启后会话可续（checkpoint + 消息历史一致）；两个项目目录并行使用状态不串

## 收尾

- [ ] 4.1 平台形态零影响回归：`uv run pytest tests/ -q` 全量 + `api_contract`（CLI 不触碰的装配面保持绿）
- [ ] 4.2 自用素材（对应 TODO P2）：M2 后开始每日自用，bug 记 `docs/bug/`；每周回顾「哪个瞬间想退回 dsh / Claude Code」→ 下一个修复点
