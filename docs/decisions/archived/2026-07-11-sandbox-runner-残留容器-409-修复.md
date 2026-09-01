# 决策：sandbox-runner 残留容器 409 修复

状态：implemented
日期：2026-07-11
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：zzqroot 重部署 compose 后 `SUPER_AGENT_QA` 对话无正文；backend 报 `创建用户沙箱失败 HTTP 503`，Docker 409 同名容器已存在（`Exited` 状态）。
- **根因**：`sandbox-runner` 内存 `_records` 清空后 `_sync_running()` 对非 running 容器返回 `None` 但不删除；`ensure()` 直接 `_start_container` 触发名称冲突。
- **修复**：`deploy/sandbox-runner/manager.py` 新增 `_cleanup_stale_container()`，在 `_start_container` 前移除 exited/dead 容器；单测 `test_ensure_runtime.py` 覆盖。
- **运维**：已手动 `docker rm noesis-sandbox-*` 恢复线上；后续发版 runner 镜像后自动免疫。
