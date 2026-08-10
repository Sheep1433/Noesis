## 1. Run 数据模型与持久化

- [x] 1.1 定义 run 状态、终态转换、错误码与快照 schema，补状态机单测
- [x] 1.2 新增 agent run ORM、repository 与 Alembic migration，覆盖身份、owner、sequence、retry 和终态字段
- [x] 1.3 实现 run/assistant 终态 compare-and-set，验证 completed、stop、timeout 并发时仅一个终态生效
- [x] 1.4 将 parts/context 检查点集中到 PersistSink，加入语义边界与可配置节流，证明不会按 token 写库
- [x] 1.5 实现启动 recovery：悬空 run → interrupted、assistant → partial/server_restart，running tool 标结果未知
- [x] 1.6 增加 `(user_id, client_request_id)` 唯一约束与 request digest，在单事务中创建 user/assistant/run，覆盖响应丢失重试与摘要冲突
- [x] 1.7 为 PostgreSQL 检查点失败实现有界合并重试和 persistence timeout，验证持续不可写时不会无界缓存

## 2. RunManager 与事件连续性

- [x] 2.1 新建 `domain/chat/runs/`，实现 RunHandle、RunManager、producer 注册/清理与 owner 生命周期
- [x] 2.2 为业务 RunEvent 分配 run 内严格递增 sequence，确保 keepalive 不推进 sequence
- [x] 2.3 实现有界事件缓存、subscriber 独立队列与慢订阅者隔离
- [x] 2.4 实现原子 snapshot + subscribe，并覆盖并发事件恰好进入 snapshot 或 live queue 的测试
- [x] 2.5 实现 after_sequence 连续补发与缓存不足时 snapshot replace，补 gap/重复事件测试
- [x] 2.6 实现每 session 单 active run 冲突控制，返回 409 与已有 run_id
- [x] 2.7 为 event buffer 和各 subscriber queue 增加事件数/字节数双上限，实现 Persist/SSE/Channel 各自背压策略
- [x] 2.8 实现 active run、运行时长、输出、HITL、terminal retention 与 shutdown drain 限制及确定资源回收

## 3. Agent 编排与 Delivery 迁移

- [x] 3.1 将 `QaService` 的 Agent producer 交给 RunManager，移除 SSE generator finally 取消 producer 的所有路径
- [x] 3.2 将 PersistSink、SseDelivery、ChannelDelivery 注册为独立 subscriber，验证任一 Delivery 失败不取消其它订阅
- [x] 3.3 收敛 ACTIVE_STREAMS/RunLifecycle 过渡状态，停止操作改为按 run_id 定位且鉴权幂等
- [x] 3.4 保持四种 qa_type 的 run 身份与终态一致，补 COMMON、SUPER、FAULT、TEST_CASE 代表性测试
- [x] 3.5 保持 HITL pending/resume 使用同一 run_id 与 assistant_message_id，验证 pending 不终态和超时 reject
- [x] 3.6 接入临时模型错误 `run-status/retrying/will_retry`，重试恢复发 running，耗尽后才发终态 error
- [x] 3.7 为模型调用增加 attempt_id，按“无输出、已有正文、已开始工具/HITL”实施重试投影边界，丢弃旧 attempt 迟到事件
- [x] 3.8 为工具增加 timeout、cancel grace period、迟到结果隔离与 unknown outcome；验证停止不会误报远程副作用已撤销

## 4. `/api/chat/runs` 接口

- [x] 4.1 新增 run 创建 schema、Service 与 `POST /api/chat/runs`，完成 session 权限校验、消息落库和快速 ACK
- [x] 4.2 新增 `GET /api/chat/runs/{run_id}`，返回授权后的状态、snapshot_sequence、parts 与终态/重试元数据
- [x] 4.3 新增 `GET /api/chat/runs/{run_id}/stream`，支持 after_sequence、run-snapshot、keepalive 与终态收尾
- [x] 4.4 新增 `POST /api/chat/runs/{run_id}/stop`，覆盖所有者、越权、重复 stop 和已终态场景
- [x] 4.5 删除 `/api/chat/sessions/stream` 路由及前端旧调用，验证项目中不存在第二条发送路径
- [x] 4.6 检查所有新 API 使用 ResponseUtil/Service 分层、Cookie Session + CSRF 和正确 404/409/500 语义
- [x] 4.7 要求新创建 API 接收 Idempotency-Key/client_request_id，相同摘要返回原 run，不同摘要返回 409

## 5. 前端恢复与用户感知

- [x] 5.1 在 `frontend/src/api/chat.ts` 增加 create/get/subscribe/stop run 客户端与类型
- [x] 5.2 重构 `useSSEStream.ts`：保存 run_id/last_sequence，解析 run-snapshot/run-status，并按 sequence 去重
- [x] 5.3 移除 beforeunload stop beacon；刷新/组件卸载只关闭 subscription，不终止 run
- [x] 5.4 对无终态 EOF、网络错误和 sequence gap 实现 get snapshot + 重订阅，禁止误调用成功收尾
- [x] 5.5 历史初始化识别 active streaming assistant，查询 run 并恢复同一 assistant_message_id
- [x] 5.6 增加 retrying、interrupted/server_restart、partial/stopped 的用户状态和操作提示，运行产品文案审查
- [x] 5.7 覆盖刷新恢复、重复 snapshot、gap 重同步、retrying、最终失败和明确 stop 的前端测试
- [x] 5.8 为创建响应未知保留 client_request_id；SSE 重连增加指数退避、随机抖动、自动重试上限和手动恢复入口

## 6. 通道与故障隔离

- [x] 6.1 让 headless/channel run 使用同一 RunManager 与 PersistSink，不依赖浏览器 SSE
- [x] 6.2 为平台出站失败记录独立 delivery 结果，验证 Telegram 失败不覆盖已 completed run
- [x] 6.3 验证无网页订阅的 Telegram、automation run 能正常终态落库和投递
- [x] 6.4 检查通道绑定与 run 查询/停止权限，确保外部 chat_id 不能越权访问其它用户 run
- [x] 6.5 明确 P0 ChannelDelivery 非 durable outbound，增加队列溢出、发送失败和进程退出丢失的监控与运维说明

## 7. 验证、部署与文档

- [x] 7.1 新增后端回归矩阵：刷新断连、多订阅、慢消费者、stop/complete 竞态、LLM retry、HITL、服务重启
- [x] 7.2 增加数据库写入计数/压测，确定检查点节流默认值并验证长文本不会逐 token UPDATE
- [x] 7.3 执行 `cd backend && uv run pytest tests/ -q`，修复所有 SSE、持久化和通道回归
- [x] 7.4 执行 `cd frontend && pnpm lint && pnpm build`，验证生产构建
- [x] 7.5 更新部署代理 SSE timeout/keepalive 说明，并制定发布前 active run drain 与回滚收口步骤
- [x] 7.6 实现完成后更新 `docs/architecture/platform/chat-streaming.md` 为新 Current 架构，并将 Proposed 设计文档转为 Current 或合并去重
- [x] 7.7 增加可观测性：run/attempt/tool_call/delivery 关联字段，以及 active run、队列字节、overflow、检查点失败、取消延迟、重连和回收指标
- [x] 7.8 验证生产为单 active backend 或 owner sticky routing；不满足时禁止宣称支持 live run 恢复
- [x] 7.9 增加故障注入：创建 ACK 丢失、DB 不可写、慢 SSE、旧 attempt 迟到、工具取消未确认、重连风暴与 terminal 内存回收
