## Why

实测路径推演与压测画像（容量脚本 `backend/tests/load_test.py`：100 并发 run × 每 run 2-3 订阅者 × 每秒 10-30 事件）：同一条 durable 事件对**每个订阅连接**各执行一次 `encode_run_event → json.dumps → format_sse`（`server/api/chat_api.py` 的 `_wire_sse_stream` 消费循环内，经 `sse.py` 的 `encode_sequenced_event`）。载荷对所有订阅者完全相同（`run_id` / `sequence` / `attempt_id` 一致，不存在 per-连接差异），编码 N 次产出 N 份内容相同的字符串——订阅数 × 事件频率的 CPU 纯放大，高峰档（300 订阅 × 30 事件/秒）约 9,000 次/秒冗余 JSON 序列化。

三类消费方当前各自编码、形态不一：SSE 连接走 wire 字符串、Redis hub 走 wire dict、persist 旁路不编码——编码时机分散在消费侧，是同一事件三种表示的根因。

## What Changes

- **编码上移到发布点，一次编码、扇出共享**：`sse.py` 的 `encode_sequenced_event` 成为唯一编码入口，在 `_assign_and_fanout`（`chat/runs/manager.py`）扇出前调用一次，产出 `bytes`（不可变，可安全被多个订阅队列共享引用）。
- 订阅队列（`BoundedEventQueue`）与 `RunSubscription` 携带**编码后字节**而非事件对象；`_wire_sse_stream` 消费循环退化为「取 bytes → yield」，不再 import 编码函数。
- `BoundedEventQueue` 字节计量从 `estimated_bytes`（估算）改为 `len(encoded_bytes)`（精确）——有界队列的上限判定随之更准。
- transient 事件同路径一次编码（`encode_filtered` 产物共享同规则）。
- Redis hub 路径（`chat/runs/hub.py` / `bus_redis.py`）的 wire dict 由同一份编码产物派生，消除「事件三种表示」的消费侧分叉；payload 形状不变（契约测试钉住的 wire 词表不动）。
- 契约测试 `tests/test_doc_contract.py` 的事件词表互钉不受影响（编码产出字节，词表提取自桥接层类型，两者解耦）。

## Impact

- 后端改动集中三处：`noesis/chat/delivery/sse.py`（编码入口收敛）、`noesis/chat/runs/manager.py`（扇出点改为编码后字节）、`server/api/chat_api.py`（`_wire_sse_stream` 简化、快照首帧保留现编码路径）。
- `run-snapshot` 首帧为 per-连接内容（快照随订阅时刻的 `after_sequence` 变化），保留在连接侧编码——不进共享编码路径，避免「共享产物带连接态」的错误。
- 内存语义：`bytes` 不可变，多队列共享同一对象引用；单 run 常驻内存不变（编码产物替换原事件引用），队列字节上限计量更准后，超限淘汰判定轻微收紧（原先被低估的大事件将更早触发慢订阅者隔离）。
- 无数据库变更、无前端变更、无协议变更（SSE 帧字节逐位一致）。
- 收益：订阅扇出的编码 CPU 从 O(订阅数 × 事件数) 降为 O(事件数)；`chat_api.py` 消费循环行数下降。
