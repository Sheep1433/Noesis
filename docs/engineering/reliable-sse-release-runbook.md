# 发布 Runbook：reliable-sse-multitab

> 对应 tasks.md §8.5。本 change 是 BREAKING 发布——前后端与数据库 schema 作为一个发布单元切换。

## 前置条件

- [ ] 目标提交已通过 code review
- [ ] `cd backend && uv run pytest tests/ -q` 全通过
- [ ] `cd frontend && pnpm test && pnpm lint && pnpm build` 全通过
- [ ] `cd backend && uv run python tests/load_test.py` 达到容量阈值
- [ ] 使用 `E2E_BASE_URL`、`E2E_STORAGE_STATE`、`E2E_SESSION_ID` 运行 `cd frontend && pnpm test:e2e`
- [ ] PostgreSQL 数据库已备份
- [ ] 隔离环境（staging）可用

## 发布步骤

### 1. 停止新 Run

```bash
# 在生产环境停止接收新 Run（通过 Nginx 或应用配置）
# 已有 active Run 继续运行直到完成或 drain 超时
```

### 2. Drain 现有 Run

```bash
# 等待 active Run 自然完成（最长 max_run_duration_seconds=900s）
# 超时未完成的 Run 由 recovery 收口为 interrupted
curl -s http://127.0.0.1:8089/health  # 确认实例健康
```

### 3. 备份数据库

```bash
pg_dump -U $POSTGRES_USER -h $POSTGRES_HOST $POSTGRES_DATABASE > backup-$(date +%Y%m%d%H%M%S).sql
```

### 4. 同步部署 migration + backend + frontend

```bash
# 4a. 停止旧 backend
docker compose -f deploy/docker-compose.yml stop backend

# 4b. 拉取新镜像 / 代码
git pull origin dev  # 或 main

# 4c. 启动新 backend（advisory lock 确保单实例）
docker compose -f deploy/docker-compose.yml up -d backend

# 4d. 确认 advisory lock 获取成功（日志中应有 "已获取 PostgreSQL advisory lock"）
docker compose -f deploy/docker-compose.yml logs backend | grep "advisory lock"

# 4e. 确认 recovery 执行（日志中应有 "agent_run_reclaimed"）
docker compose -f deploy/docker-compose.yml logs backend | grep "recover_orphaned"

# 4f. 部署前端
cd frontend && pnpm build
# 将 dist/ 部署到 Nginx/CDN
```

### 5. 双 Tab smoke test

在隔离环境验证：
- [ ] Tab A 创建长回答，Tab B 打开同一 session——B 通过 active-run API 发现 Run
- [ ] 关闭 Tab A，Tab B 继续直到完成
- [ ] Tab B 点击 stop，两个 Tab 显示相同 partial 终态
- [ ] 断网恢复后 snapshot 校正，无重复文本

### 6. 开放流量

```bash
# 恢复 Nginx 路由
# 监控首批用户：
#   - event-loop lag
#   - subscriber overflow
#   - checkpoint latency
#   - subscriber_count / subscriber_queue_bytes
#   - persistence_blocked / terminal_cas_loser
```

## 回滚

本 change 不保留旧路径作为回滚手段。如 smoke test 失败：

1. 停止服务
2. 用步骤 3 的数据库备份恢复
3. 用发布前代码整体恢复（`git checkout <pre-release-commit>`）
4. 失败发布窗口不接收新 Run

## 验收阈值

| 指标 | 阈值 |
|------|------|
| event-to-client latency | p99 < 500ms |
| event-loop lag | p99 < 100ms, max < 1s |
| overflow 后其它 subscriber 延迟 | 无显著上升 |
| terminal persistence | 100% 先 DB 后 fan-out |
| advisory lock | 第二实例 fail-fast |

自动化 E2E 需要一个已登录的 Playwright storage state 和该用户拥有的 session。HITL 场景另设 `E2E_HITL_QUERY`，其内容必须稳定触发可审批工具。
