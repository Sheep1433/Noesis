# 决策：沙箱收敛：Shell 操作符 / Compose bind / session 隔离

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **根因**：不是双 Skills 路径，而是整用户 rw 挂载 + execute `shlex.split`→`shlex.join` 改写 + Compose 把 runner 容器内路径交给 daemon，叠加 AIO 兼容链。
- **P0 修复**：
  1. 删除 execute 虚拟路径 rewrite（`path_rewrite.py`）；Shell 保留 `>`/`|`/`&&`。
  2. Compose：`NOESIS_HOST_DATA_DIR` / `NOESIS_HOST_SKILLS_DIR` 必须是宿主机绝对路径，并以同路径字符串 bind 进 runner。
  3. 挂载收敛：session workspace → `/workspace` rw；公共/个人 Skills → `/skills/public|personal` ro；不再挂整棵 `users/{uid}`。
- **P1**：per-session 容器；handle 404 清缓存并重建；删除 AIO / `agent-sandbox`；slim 非 root（UID 10001）；去掉递归 chmod 644。
- **P2**：上传/删除个人 Skills 后 `bump_user_skills_revision` + `RevisableSkillsMiddleware` 强制重扫。
- **验证**：`backend` 487 passed；`deploy/sandbox-runner` 13 passed。Compose 真机验收见 `deploy/README.md` 清单。
- **OpenSpec**：`openspec/changes/converge-agent-sandbox/`。
