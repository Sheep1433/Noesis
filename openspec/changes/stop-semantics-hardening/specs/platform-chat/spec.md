# platform-chat Delta

## MODIFIED Requirements

### Requirement: 停止生成

系统 SHALL 提供按 `run_id` 停止当前执行的 API；用户停止、浏览器断开、生成失败与服务重启 SHALL 分流。刷新、关闭页面和普通网络断开 SHALL NOT 自动调用 stop。chat 页停止 UI SHALL 等待服务端 run 进入终态，避免本地假完成：点击停止后客户端 SHALL await 停止接口返回的终态快照、应用该快照并据此收尾 UI；等待期间客户端 SHALL 保持事件订阅与增量渲染，SHALL NOT 预先断开订阅或本地预置终态；停止接口调用失败（网络错误、401、5xx）时客户端 SHALL 保持运行态与停止按钮可重试，SHALL NOT 移除停止入口。

#### Scenario: stop → partial
- **WHEN** run 所有者明确调用 stop 且 run 仍在进行
- **THEN** 服务端 SHALL 中止 Agent 并将 assistant 标为 partial

#### Scenario: beforeunload 不停止
- **WHEN** 浏览器在 run 进行中刷新或关闭页面
- **THEN** 客户端 SHALL NOT 因 `beforeunload` 调用 stop
- **AND** 后端 SHALL 允许 run 继续

#### Scenario: 停止接口失败保持运行态
- **WHEN** 用户点击停止且停止接口调用失败（网络错误或非 2xx）
- **THEN** 客户端 SHALL 保持「生成中」状态与停止按钮可再次点击
- **AND** SHALL 提示停止请求失败
- **AND** SHALL NOT 将消息本地置为已停止或追加中断标注

## ADDED Requirements

### Requirement: 停止终态文案单一来源

用户停止主 run 的终态处理中，未完成工具 part 的收尾文案与前端中断标注的工具错误文案 SHALL 统一为同一短文案（「用户已停止生成」），且正常收尾与停止兜底两条终态处理路径 SHALL 一致；实时展示与历史回放 SHALL NOT 出现同一工具在不同时机呈现不同错误文案。中断标注 SHALL 幂等派生：既依据 SSE 终态帧（含其他窗口停止场景），也依据停止接口快照（快照整体替换 parts 后重新追加），两种到达序下标注均不丢失。

#### Scenario: 实时与回放一致
- **WHEN** 主 run 在工具执行中被用户停止，前端实时收尾并在刷新后从历史回放该消息
- **THEN** 未完成工具 part 的错误文案 SHALL 前后一致（用户已停止生成）
- **AND** 中断说明 SHALL 依据落库的 `extra.finish_reason === 'stopped'` 派生呈现
