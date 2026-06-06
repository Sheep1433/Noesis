## ADDED Requirements

### Requirement: 服务端 SHALL 按可配置间隔发送 SSE 注释保活帧

在 `POST /api/chat/sessions/stream` 的流式响应中，当距离上一次已写入客户端的字节（含任意业务 SSE 帧或注释保活帧）超过配置阈值且流仍未正常结束时，系统 SHALL 写入一条符合 SSE 规范的注释行帧（以 `:` 开头、以空行结束），且该帧 SHALL 不包含 `event:` 或业务 `data:` JSON，以免干扰现有前端解析。

#### Scenario: 保活间隔为正且上游长时间无输出

- **WHEN** `sse_keepalive_interval_seconds`（或最终实现中的等价配置名）为大于 0 的数值，且 Agent 异步生成器在超过该秒数内未产出下一项
- **THEN** 系统 SHALL 向响应流写入至少一条 SSE 注释保活帧，随后继续等待上游事件直至结束或取消

#### Scenario: 保活被显式关闭

- **WHEN** 配置项将保活间隔设为 0（或项目约定的「关闭」值）
- **THEN** 系统 SHALL 不发送注释保活帧，流行为与关闭该功能时一致

### Requirement: 连接类写入失败 SHALL 可观测且不降级为未分类业务错误

当客户端已断开或连接重置导致无法继续向响应体写入时，系统 SHALL 停止继续向该连接写入，并将该情况记录为 INFO 或 WARNING 级别日志（不得默认使用 `logger.exception` 视为未处理应用错误），且 SHALL 不因此损坏数据库会话的显式 `rollback`/`commit` 约定以外的逻辑。

#### Scenario: 写入阶段触发 BrokenPipe

- **WHEN** `StreamingResponse` 消费循环在 `yield` 编码后的 SSE 字节时抛出 `BrokenPipeError` 或 `ConnectionResetError`
- **THEN** 系统 SHALL 结束该次流的写入循环，并采用 INFO 或 WARNING 记录断开事实

### Requirement: SSE 对外契约 SHALL 具备自动化回归覆盖

系统 SHALL 为 `LangGraphSseBridge`（或统一的 SSE 字符串格式化入口）提供自动化测试，覆盖至少：`message-start` 形态、`text-delta` 含 `textDelta`、`finish` 含 `finishReason`/`usage`、`data: [DONE]` 收尾；断言方式为解析 `data:` 行 JSON 键集合或关键字段类型，防止静默破坏 `useSSEStream` 消费者。

#### Scenario: 桥接层最小 golden 断言

- **WHEN** 测试向桥接传入代表「消息开始」与「文本增量」的合成事件并收集输出字符串
- **THEN** 输出 SHALL 包含合法 SSE 帧边界且 `data:` 负载可被 `json.loads` 解析，且 `type` 字段与事件名一致
