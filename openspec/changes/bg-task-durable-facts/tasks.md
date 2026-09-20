# 任务：后台任务事实源出内存落库

## 1. 存储与迁移

- [x] 1.1 追加消息 write-ahead 载体扩展（无新表、无迁移）：`create_pending_user_message` 的 `extra` 增加 turn 参数（`model_id` / `reasoning_effort`）落行；补 dropped 翻转标记词汇；如对账扫描需要，为 `extra.pending_run` 加表达式索引。
- [x] 1.2 新增 `t_bg_shell_job` 模型与 migration：`task_id`（PK）、`session_id`、`user_id`、`command`、`status`（BgTaskStatus 值域）、`error`、`result_tail`、`created_at`、`started_at`、`completed_at`；查询路径补 session 维度索引。
- [x] 1.3 `subagents` 配置组新增 `terminal_retention_seconds`（默认 3600）与 `terminal_reclaim_max`（默认 200），接入 config/env 读取与校验。

## 2. 追加消息 write-ahead（复用 pending user message 行）

- [x] 2.1 `deliver_message`（原 deliver_followup）收敛为唯一入口并**全路径** write-ahead：`user_message_id` 为空（工具路径）时经 SubagentSessionPort 补写 pending user message 行（`extra.pending_run: True` + turn 参数），再入内存队列；入队元素携带消息行 id；不引入第二套待投递记录。
- [x] 2.2 队列容量改为 pending 消息行计数判定（DB count），超限在写行前拒绝（HTTP 路径拒绝沿用既有软删收尾）；移除对内存 deque `maxlen` 依赖的容量语义。
- [x] 2.3 投递确认复用 `create_turn_run`（原 create_followup_run）对 pending 行的 launch 事务采纳（清 `pending_run`、盖 `run_id`）；采纳现状为整覆写 extra（会丢弃行上 turn 参数），重载消费路径须在覆写前读取参数或改为合并保留。投递失败**不终态化任务**：改造 `settle_followup_prelude_failure`（现语义把任务落 FAILED）为「任务回退先前终态（冷恢复路径，恢复完整先前收口态）/ 保持当前状态（链式路径）+ 消息行翻转 dropped + 可诊断错误」；冷恢复窗口内受理的停止终态获胜（不回退）；不可续终态（failed/timed_out）的收口链把未消费 pending 行翻转 dropped。
- [x] 2.4 用户 API 路径（send_followup → send_message）接入 turn 参数落行（随 `create_pending_user_message` 的 extra 持久化），排队期间参数不再只存内存。
- [x] 2.5 符号与端点更名：`deliver_followup`→`deliver_message`、`create_followup_run`→`create_turn_run`、`followups` deque→`pending_messages`、`MAX_FOLLOWUPS`→`MAX_PENDING_MESSAGES`、API `/subagent-followup`→`/subagent-messages`（前端调用点同步）、模型工具 `update_async_task`→`send_message`（对齐主规格既有声明，不保留上游别名）；更新 `subagent/roles.py` 的 `BG_TASK_TOOL_NAMES`（递归委派防线名单），并**补充**钉住 `send_message` 及防线名单全量的工具名断言测试（现状无此测试，更名后无测试会红）。

- [x] 2.6 前端呈现：pending 标记消息待执行状态、dropped 标记消息「未执行」标注（SHALL NOT 呈现为已执行的正常消息）；子会话抽屉渲染改造与测试。

## 3. shell 任务落库

- [x] 3.1 shell job 启动时写 `t_bg_shell_job` 行（queued / running）；状态迁移与终态（含 result_tail、error）同步落库，复用 settle 收口链的落库点。
- [x] 3.2 `ShellJobService` / 任务目录对内存 miss 的 shell 任务回退 `t_bg_shell_job` 投影。

## 4. 查询 DB 兜底与状态映射

- [x] 4.1 抽出统一的「run 行状态 + finish_reason → 任务级状态」映射函数，内存快照与 DB 投影共用；映射表见 design「状态映射共用一个函数」。
- [x] 4.2 subagent 任务的 DB 投影：child session + run 行派生任务快照（状态、结果摘要、来源清单、`subagent_type`、时间戳），结果从子会话落库内容派生。
- [x] 4.3 `ExecutorPort` 的 check/list/cancel 查询接入「热集 miss → DB 投影」回退（cancel 对 DB 终态任务幂等返回快照）；无任何 DB 事实时保留现有可诊断提示。
- [x] 4.4 DB 投影携带 `undelivered_messages` 计数（该 child session 的 dropped 标记消息行计数）。

## 5. 终态回收与冷恢复

- [x] 5.1 终态条目按 `terminal_retention_seconds` 惰性 + 周期回收；终态条目超 `terminal_reclaim_max` 从最旧回收；回收动作不触碰 terminal_published / 通知 / 排队（时序见 design 风险节）。
- [x] 5.2 冷恢复重建路径：内存 miss 且 DB 终态可续（completed / cancelled）时，从 child session `extra.subagent` descriptor 解析 worker 配方重建条目、**从 DB pending 行重载追加消息队列（含 turn 参数）**后走既有冷恢复链；重建失败返回可诊断错误且任务回退先前终态（不落 failed）。
- [x] 5.3 pending 标记瞬态化确认：launch 采纳 / 对账翻转 dropped 之外无第三终态，消息行随既有会话消息 retention 管理，不新增清理任务。
- [x] 5.4 终态落库失败处置：`settle` 落库失败从仅记日志改为有界重试（沿用主链路 persistence 超时重试模式），耗尽后条目允许回收、DB 投影携带「终态落库失败」可诊断标注（不伪造终态）。

## 6. 重启对账扩展

- [x] 6.1 对账装配处（leader 晋升链）扩展：非终态 `t_bg_shell_job` 行（queued / running，均不重建——shell 执行环境不持久化）收口为 cancelled（注明进程重启、产出未知）；既有 `reconcile_orphaned_runs` 的收口集合**排除 queued 行**（queued 改走 6.3 排队重建）。
- [x] 6.2 对账将 pending 追加消息行按所属任务去向分流：任务重建排队的保留 pending 并入队；任务已收口终态的翻转 dropped 标记（不自动重投）；对账日志记录收口与分流计数。
- [x] 6.3 排队重建（仅 child run 行）：对账后按 queued run 行 `created_at` 升序重建进程内排队队列并触发 drain（复用既有并发槽判定，唤醒候选全局升序、槽满留队）。

## 7. 测试

- [x] 7.1 单元：状态映射函数（全值域 + finish_reason 分支 + 多 run 取行规则）；pending 计数容量判定；回收保序与活跃豁免；回收资格（收尾在途与落库失败条目不进回收候选）。
- [x] 7.2 集成：write-ahead——工具路径追加落 pending 消息行 → 重启模拟（对账）→ dropped 翻转可见；launch 事务采纳清标记；队列满写行前拒绝。
- [x] 7.3 集成：shell job 落库全生命周期 + 重启收口为 cancelled。
- [x] 7.4 集成：回收后 check/list/cancel DB 兜底投影（subagent / shell 两 kind；重复取消幂等）；回收后冷恢复续聊与重建失败诊断。
- [x] 7.5 回归：`tests/api_contract` 全绿；排队唤醒、协作停止、通知注入既有用例不回归。
- [x] 7.6 集成：重启模拟——queued child run 行按 `created_at` 重建排队、全局升序唤醒、槽满留队；queued/running shell 行收口 cancelled 不重建；排队任务的 pending 追加消息随任务保留并消费；running 任务遗留追加消息翻转 dropped 且投影可见。
- [x] 7.7 集成：投递失败不终态化——冷恢复 descriptor 解析失败任务回退先前终态（不落 failed）、消息行 dropped、调用方收可诊断错误；冷恢复窗口内受理的停止获胜（不回退）；不可续终态收口翻转未消费 pending 行。
- [x] 7.8 集成：回收后冷恢复重载队列——cancelled 任务带未消费 pending 消息，条目回收后 send_message 触发重建，重载的消息按序消费、turn 参数生效；终态落库失败重试耗尽后回收，投影携带落库失败标注。

## 8. 收尾

- [x] 8.1 更新 `docs/engineering/` 后台任务相关文档的注册表语义描述（如存在对应章节）；按审计文档状态流转回写处理结论。
- [x] 8.2 提炼决策记录（`docs/decisions/`）：事实源三层承载与被否的 Redis 队列/租约、追加消息载体取舍。
- [ ] 8.3 归档时替换主规格残留 followup 措辞（Purpose、协作式停止 3 处、输出截断 1 处）为「追加消息」（RENAMED 机制只处理 requirement 标题，自由文本需手工替换），替换后跑 `python3 scripts/verify-md-links.py`。
