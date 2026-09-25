# Tasks: 编码一次、扇出共享

## 1. 编码入口收敛（sse.py）

- [x] 1.1 `noesis/chat/delivery/sse.py`：`encode_sequenced_event` 返回 `list[bytes]`（`format_sse` / `format_done` 产物 encode 为 bytes；`SSE_COMMENT_KEEPALIVE` 改常量 bytes）；`encode_filtered` 同步返回 `list[bytes]`
- [x] 1.2 `sequenced_event_payloads` 重构为与编码共享单次计算：一次遍历产出 `(event_name, payload_dict, encoded_bytes)` 三元组（或等价结构），`encode_sequenced_event` 与 hub wire dict 派生共用，杜绝两次 json.dumps
- [x] 1.3 单测：同一 envelope 编码产物确定性（重复调用逐位一致）；StreamDone / 终态 / transient / 快照各类事件分支产出正确

## 2. 扇出路径改造（manager.py）

- [x] 2.1 `SequencedRunEvent` 增加 `encoded: bytes | None` 字段（默认 None，不可变 dataclass 追加字段）：`_assign_and_buffer` 或 `_assign_and_fanout` 锁内完成编码并填充；transient 路径（`publish_transient`）在投递前编码一次
- [x] 2.2 `_fanout`：订阅队列收 `encoded` 非空的事件（envelope 或 bytes 包装按消费方最小改动定形）；`BoundedEventQueue._item_bytes` 对携带 encoded 的事件改用 `len(encoded)` 精确计量
- [x] 2.3 PersistWriter 与 Redis publisher 消费路径核对：persist 不消费 bytes（原样用 envelope 字段）；publisher 派生 wire dict 用 1.2 的共享产物
- [x] 2.4 单测：多订阅者收到同一 bytes 对象（`is` 引用相同）；大事件在精确计量下更早触发慢订阅者隔离（原低估路径）；锁内无 I/O 不变式（编码为纯函数断言）

## 3. 消费循环简化（chat_api.py）

- [x] 3.1 `_wire_sse_stream`：durable / transient 分支改为「取队列产物 → yield bytes」，删除该文件的 `encode_sequenced_event` / `encode_filtered` import；`run-snapshot` 首帧与 replay 路径保留连接侧编码（快照 per-连接，D2）
- [x] 3.2 hub 路径（`HubSubscription` 消费循环同文件）：wire dict → SSE 帧的转换改用共享编码产物的 bytes（或确认 hub dict 形状下由 1.2 产物直接携带 bytes），保持去重 / after_sequence / 终态收流语义不变
- [x] 3.3 回归确认：`_sse_response`（StreamingResponse）对 bytes 生成器行为正确；keepalive（常量 bytes）与 None 哨兵分支原样

## 4. 契约与容量验收

- [x] 4.1 `uv run pytest tests/ -q` 全绿（含新增单测）；`uv run pytest tests/api_contract -q` 契约门禁
- [x] 4.2 `tests/test_doc_contract.py` 事件词表互钉不动且全绿（词表提取自桥接层，与编码层解耦，预期零改动——如有漂移即设计违约，回头查 1.2）
- [ ] 4.3 容量对比（待环境：起服务跑 load_test 前后两轮）：`backend/tests/load_test.py` 改动前后各跑一轮，记录事件循环延迟 p50/p95/p99、订阅扇出段 CPU、常驻内存——验收标准：延迟不劣化、CPU 下降、内存持平
- [ ] 4.4 手动验收（待环境：uv run app.py + Redis 双进程形态）（`uv run app.py`，Redis 形态双进程）：a) 单 run 双标签页内容一致；b) 断线重连带 after_sequence 补发行为不变；c) 慢订阅者（限速消费）被隔离后重连快照恢复；d) 终态帧 + [DONE] 收流时序不变
- [ ] 4.5 文档：`docs/engineering/platform/chat-streaming.md` 数据流节补「编码一次扇出共享」描述；归档时同步主规格

## 5. 后续（本变更不含）

- [ ] 5.1 子代理执行器侧（`agents/background/jobs/events.py`）同模式改造——主链路稳定一个版本后跟进
