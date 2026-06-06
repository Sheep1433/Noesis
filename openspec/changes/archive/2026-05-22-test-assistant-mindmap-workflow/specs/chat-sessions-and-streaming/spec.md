## ADDED Requirements

### Requirement: TEST_CASE_QA 业务帧 SHALL 支持测试助手脑图与列表联动

在 `qa_type=TEST_CASE_QA` 的流式响应中，当服务端发出 `scenes-testpoints-ready` 或 `testpoints-confirm-required` 时，`data:` JSON 中的 `scenes` 字段 SHALL 为 JSON 数组，且每项 SHALL 可包含 `scene_name`、`scene_description`（可选）及 `test_points` 数组；`test_points` 项 SHALL 包含非空的 `point_name` 供前端勾选与脑图生成。客户端（测试助手页）SHALL 将同一 `scenes` 引用同时用于勾选列表与脑图 Markdown 生成，不得依赖额外隐藏字段。

#### Scenario: scenes 载荷结构可供双端消费

- **WHEN** 测试助手消费 `testpoints-confirm-required` 事件
- **THEN** `scenes` SHALL 可被解析为至少包含 `scene_name` 与 `test_points[].point_name` 的结构，且 SHALL 无需再请求历史消息即可渲染列表与脑图
