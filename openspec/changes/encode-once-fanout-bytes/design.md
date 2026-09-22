# Design: 编码一次、扇出共享（encode-once fanout bytes）

## 1. 背景与问题

### 1.1 现状：编码发生在消费侧，每连接一次

```
producer 事件
  → RunManager.apply_event（锁内）
      → _fanout：envelope（事件对象）put_nowait 到每个订阅队列
  → 每个订阅连接的 _wire_sse_stream（消费循环）
      → encode_sequenced_event(envelope)   ← 每连接各一次
          → json.dumps + 序列号注入 + SSE 格式化
      → yield 字符串
  → Redis hub 路径（多实例形态）
      → sequenced_event_payloads(envelope) → wire dict   ← 又一种表示
```

同一 envelope，SSE 连接编码成字符串、hub 派生成 dict——同一事件三种表示（对象 / 字符串 / dict），编码逻辑分散在两个消费侧。

### 1.2 为什么现在做

- 压测画像中订阅扇出的编码 CPU 已可测量（见 proposal Why）；容量阶梯（1K/10K 并发档）下订阅数 × 事件频率的放大是确定性增长项。
- 前置重构已就位：`DeliveryCore`（投递内核，被动数据结构）已统一主链路与子代理执行器的投递语义，扇出点唯一化（`_fanout`），编码上移有一个干净落点。
- SSE 恢复协议（序号/快照/契约测试）稳定，编码层调整不触碰协议面。

## 2. 目标形态

```
producer 事件
  → RunManager.apply_event（锁内）
      → encode_sequenced_event(envelope)  ← 唯一编码点，产出 bytes
      → _fanout：同一份 bytes put_nowait 到每个订阅队列（共享引用）
      → Redis hub：payload dict 由同一编码产物派生（或直接携带 bytes + 元数据）
  → 每个订阅连接的 _wire_sse_stream
      → yield bytes                       ← 纯转发，零编码
```

### 2.1 核心决策

**D1：编码产物是 `bytes` 而非 `str`。**
SSE 帧最终以字节写入 HTTP 流；`bytes` 不可变，多个 `BoundedEventQueue` 共享同一对象引用无别名风险；省去消费侧 str→bytes 的隐式编码。`StreamingResponse` 对 bytes 生成器直接支持。

**D2：快照首帧不进共享编码路径。**
`run-snapshot` 的载荷含订阅时刻的 `after_sequence` 与快照状态——**per-连接内容**，编码产物随连接变化，共享即错误。保留在 `_wire_sse_stream` 连接侧编码（现状不变）。判据：**共享编码只覆盖「载荷对所有订阅者逐位相同」的事件**；durable 事件（序列号注入后全连接一致）与 transient 事件满足，快照帧不满足。

**D3：transient 事件同规则一次编码。**
transient（text-delta / reasoning-delta / stats-update）同样载荷无关连接。经 `encode_filtered` 在发布点编码一次，扇出共享。心跳注释帧（`SSE_COMMENT_KEEPALIVE`）已是常量 bytes，天然共享。

**D4：锁内编码的安全性。**
`encode_sequenced_event` 是纯函数（json.dumps + 字典拼装），无 I/O、无 await，在 `_assign_and_fanout` 的 handle.lock 临界区内调用不违反「锁内无 I/O」不变式。编码耗时（微秒级 JSON 序列化）计入既有临界区预算，与 projection.apply 同量级，可接受。

**D5：hub 路径统一表示。**
`bus_redis.py` 发布的 wire dict 改由同一编码产物派生：`sequenced_event_payloads` 重构为「编码 bytes + 旁挂 (event_name, payload_dict) 元数据」双产物——SSE 连接消费 bytes，hub 消费 dict，一次计算两用。wire dict 字段形状不变（`type` / `sequence` / `attempt_id` / `run_id` / 载荷字段原样），契约测试与前端零改动。

### 2.2 字节计量

`BoundedEventQueue._item_bytes` 分支简化：envelope 携带编码产物后，`len(encoded_bytes)` 是精确值，替代 `SequencedRunEvent.estimated_bytes` 的 json.dumps 估算（估算路径还带 try/except 兜底）。`max_bytes` 上限语义不变，判定更准。

已知行为变化（可接受，记录在案）：原先 `estimated_bytes` 低估的大事件，在精确计量下会更早触发队列上限 → 慢订阅者隔离更早发生。这**收紧**了背压保护的正确方向（原先可能放行超限事件），非回归。

## 3. 被否方案

| 方案 | 否决理由 |
|---|---|
| 消费侧缓存（连接间共享编码结果的 memoize 字典） | 引入缓存失效与生命周期管理（事件对象可变、连接集合动态），复杂度高于把编码挪到发布点；且 hub 路径仍需第二种表示 |
| 每连接惰性编码 + asyncio Task 预编码流水线 | 为省一次编码引入调度复杂度，方向错误——问题不是编码慢，是重复 |
| 编码产物放 envelope 惰性字段（首次消费时编码并回填） | 锁内数据结构出现「可变缓存」字段，破坏 envelope 不可变纪律；且首个消费者承担编码成本，扇出时刻不确定 |
| 事件对象直接序列化进 Redis、hub 消费侧解码再编码 | 引入跨进程序列化协议层，为省进程内 CPU 付网络字节 + 双重编码，负收益 |

## 4. 风险与边界

- **临界区耗时**：编码进锁内，高吞吐 run 的锁持有时间增加（每事件 +微秒级）。缓解：容量脚本回归对比 `_sample_event_loop_lag` 指标；若出现劣化，编码可移至锁外紧邻扇出（envelope 已含全部所需字段，编码不依赖锁内状态——唯一注意序号注入必须在编码前完成，而序号在 `_assign_and_buffer` 锁内分配，天然满足先后序）。
- **慢订阅者隔离提前**（见 2.2）：容量脚本已含慢消费注入场景，回归验证隔离路径行为符合预期即可。
- **keepalive 与 None 哨兵**：不编码、不进字节计量（哨兵无载荷），消费循环分支保留。
- **子代理执行器侧**（`agents/background/jobs/events.py` 的 DeliveryCore 持有方）：本变更只动主链路 RunManager 的扇出路径；子代理侧同款优化待主链路稳定后按同模式跟进（本变更不含，避免一次动两个持有方）。

## 5. 验证策略

- 单测：同一事件多订阅者收到的 bytes 逐位一致；快照首帧仍按连接编码；transient 共享路径正确；队列精确计量（大事件早隔离）。
- 契约门禁：`tests/test_doc_contract.py`（事件词表互钉）不动且全绿；`tests/api_contract` 全绿。
- 容量回归：`backend/tests/load_test.py` 对比改动前后——事件循环延迟（p50/p95/p99）、订阅扇出 CPU、内存不变。
- 手动验收：单 run 多标签页 + 断线重连 + Redis hub 形态（双进程）下帧内容与重连补发行为与改动前一致。
