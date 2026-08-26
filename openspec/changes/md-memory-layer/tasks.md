## 0. 前置

- [x] 0.1 归档 `add-run-aware-memory-cortex`（备注：11.1 未过，被本变更取代）
- [x] 0.2 ~~现存 item 导出~~（旧数据未上生产且无价值，经用户确认直接删除，不做导出）

## 1. 文件层

- [x] 1.1 目录结构与格式定义：MEMORY.md 索引（五类分组、一行一条、行数+字节双保险预算）、条目文件（正文/Why/适用条件/来源/更新时间）、journal 日志；类型集冻结（目录即枚举，五选一，fallback 进 journal）
- [x] 1.2 文件服务：原子写、写前重读（不覆盖用户改动）、slug 生成（撞名追加序号）、索引行一致性维护、索引损坏行容错（坏行跳过且可从条目目录重建）
- [x] 1.3 会话终态判定（idle 超时或显式关闭）+ 会话表「已抽取」标记 + 未抽取会话补扫

## 2. 抽取（写入）

- [x] 2.1 会话终态触发 → 异步抽取任务（读会话消息 + 各 run memory_context 聚合 + 现有条目；输入有界；同一用户串行执行防并发覆盖）
- [x] 2.2 抽取 prompt：类型判定（五选一：偏好/目标/决策/经验/注意事项；不属于任何类型只进 journal）+「不该存」负面清单 + 相对日期转绝对日期 + 轻量合并（重复更新、过时改写、修正视为更新信号）+ 单次至多 3 条（超出只进 journal）
- [x] 2.3 守卫：敏感拒收、防自强化（复述注入条目不记录，用户修正除外）、无价值零写入
- [x] 2.4 journal 情景条目追加
- [x] 2.5 抽取测试：有价值/无价值/敏感/复述/修正更新/重复/过时改写

## 3. 整理（低频后台）

- [x] 3.1 整理任务（触发：条目数/索引大小/间隔）：全局去重、矛盾裁决、淘汰（索引移除；goal 类重点检查完结/演进）、索引压缩（超预算去重/删死指针/条目降级，禁止静默截断）
- [x] 3.2 整理测试：合并/冲突/淘汰后 journal 可搜可重建/索引压缩

## 4. 注入

- [x] 4.1 USER.md + 索引进稳定前缀（会话内不变）；每 Run 小模型选条（廉价模型一次调用；记忆量小于预算时全量跳过选条）
- [x] 4.2 同 Run 冻结（含 HITL resume）、新 Run 重新选条、alreadySurfaced 去重、subagent 不重复、头部免责声明 + 按条目年龄 stale 警告、依赖失败零注入
- [x] 4.3 注入条目清单回写 run.memory_context（多 Run 会话供抽取聚合）
- [x] 4.4 注入通道：选条正文走 Run 级 late-context 快照；每 Run 变化内容禁止进入稳定前缀区（回归测试断言前缀稳定）

## 5. 检索与会话中写入

- [x] 5.1 `search_memory` 改为 grep/读 memory 目录（类型过滤、预算沿用）
- [x] 5.2 会话中主动写入：Agent 提议 + HITL 确认 → 直接写条目文件立即生效

## 6. 前端

- [x] 6.1 设置页记忆区：文件管理（索引/条目查看编辑、journal 查看）+ 单一开关；删除四类条目治理 UI
- [x] 6.2 前端行为测试与 copy audit

## 7. Removal baseline 与回归

- [x] 7.1 删表：t_memory_item/evidence/snapshot/job/outbox/query_trace（Alembic drop；query_trace 写入方随深查询链路删除）
- [x] 7.2 删代码：consolidation 状态机、bulletin 服务与中间件、management 条目治理、workspace 派生视图、深查询链路、memory index 服务
- [x] 7.3 removal baseline 测试：旧表/类/路由不存在、应用可启动、USER.md/AGENTS.md 显式能力不受影响
- [x] 7.4 全量回归（backend pytest / frontend lint+build）

## 8. 评测与门禁

- [ ] 8.1 冻结抽取与整理 fixture，precision/recall/零写入/防自强化/敏感拒收/修正更新指标
- [ ] 8.2 膨胀率指标与索引预算校验
- [ ] 8.3 paired 开/关任务成功率（沿用现有方法与 CI 判定；评测走 prod 模型通道，避免免费配额阻塞门禁）
- [ ] 8.4 release gate 重定义并 fail-closed
