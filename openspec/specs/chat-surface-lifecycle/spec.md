## Purpose

本能力规定 Noesis **Chat 对话面** 的统一生命周期：**未发送面（COMPOSING）→ 发送中（SENDING）→ 已物化会话（ACTIVE）**。

核心产品规则：

- **点击发送（通过校验）才物化** `t_chat_session`（`ensure` / `create`）；
- 未发送的输入与附件 **仅存浏览器内存**，刷新 / 新建 / 切历史 **丢弃**（合理且为本能力明确选择）；
- Composer 挂载或仅改未发送偏好 **SHALL NOT** 物化可列表会话。

附件发送编排与刷新丢弃语义 **保持** [`chat-composer-send-upload`](../chat-composer-send-upload/spec.md)（方案 B）；本能力补齐 ensure 边界、偏好分层、列表过滤与 ACTIVE 路由续聊。

权威设计见 [`docs/prd/platform/Chat对话面生命周期设计.md`](../../../docs/prd/platform/Chat对话面生命周期设计.md)。

**SHALL NOT** 引入 draft 会话、soft `lifecycle=draft`、或发送前 staging 作为产品路径。  
**SHALL NOT** 改变 SSE 事件类型集合或 assistant 骨架—检查点—终态落库语义。

## Requirements

### Requirement: 对话面 SHALL 区分 COMPOSING、SENDING、ACTIVE

系统 SHALL 将主 chat 面建模为：

| 状态 | 含义 |
|------|------|
| `COMPOSING` | 未发送；可有内存中的输入、本地附件队列、偏好 overlay；**无**因本面产生的可列表会话 |
| `SENDING` | 用户已点发送且通过校验；正在 ensure、upload（若有）、stream |
| `ACTIVE` | 会话已物化且可续聊；可出现在历史列表；URL 为 `/chat/{sessionId}`（或等价） |

从 `COMPOSING` 进入可列表 `ACTIVE` 的产品路径 SHALL 为 **用户发送且通过校验的发送编排**。打开已有会话 SHALL 直接进入 `ACTIVE`。

#### Scenario: 进入默认 chat 页为 COMPOSING

- **WHEN** 已认证用户导航至 `/chat` 或 `/chat/new`（无有效 ACTIVE `sessionId`）
- **THEN** 对话面 SHALL 处于 `COMPOSING`
- **AND** 侧栏 SHALL NOT 因本次进入而新增空会话项

#### Scenario: 发送成功后进入 ACTIVE

- **WHEN** 用户在 COMPOSING 输入有效文字并成功完成发送编排（含 SSE 启动）
- **THEN** 对话面 SHALL 进入 `ACTIVE`
- **AND** 该 `session_id` SHALL 可出现在历史列表中

---

### Requirement: 会话物化 SHALL 仅发生在发送编排或更新已存在 ACTIVE 会话时

下列动作 **SHALL NOT** 调用 `PUT /api/chat/sessions/{session_id}/ensure`（或 `POST /sessions` create）以致产生新的可列表空会话：

1. 挂载 ModelSelector / KbScopeSelector / Composer 工具条；
2. 加载模型目录或填入默认 `model_id`；
3. 在 COMPOSING 下仅修改模型、KB、MCP、Skills 等偏好；
4. 选择 / 拖拽 / 粘贴文件进入本地队列；
5. 仅打开或刷新 `/chat/new`。

客户端 **MAY** 在内存中预生成即将首次发送使用的 `session_id`（UUID），但 **SHALL NOT** 在发送前因此 ensure。

发送编排 **SHALL** 允许 ensure；`ACTIVE` 下用户再改偏好 **MAY** 对**已存在**会话 merge `extra`。

#### Scenario: 打开页面加载默认模型不物化

- **WHEN** 用户打开 `/chat/new`，ModelSelector 展示平台或 User default 模型
- **THEN** 前端 SHALL NOT 因此调用 ensure/create 使「新对话」出现在历史侧栏

#### Scenario: COMPOSING 切换模型不物化

- **WHEN** 用户在未发送任何消息时切换 `model_id`
- **THEN** 变更 SHALL 仅留在内存 overlay（或写入 User defaults，若用户显式「设为默认」）
- **AND** SHALL NOT 新增可列表会话

#### Scenario: 点击发送才 ensure

- **WHEN** 用户首次在某 COMPOSING 面点击发送且通过校验
- **THEN** 前端 SHALL 在 upload（若有）与 stream 之前（或与之编排的等价顺序中）ensure/create 该会话
- **AND** 此前 COMPOSING 期间 SHALL NOT 已因偏好或选文件而 ensure 成功出可列表空会话

---

### Requirement: 偏好 SHALL 分层为 User defaults、Composing overlay、Session.extra

系统 SHALL 按下列顺序解析发问配置（模型、KB、MCP、Skills 等）：

1. 当次请求显式字段（若允许）；
2. `ACTIVE` 的 `session.extra`；
3. 当前 COMPOSING 的内存 overlay；
4. User defaults；
5. 平台 / profile 缺省（如 FAULT MCP profile，与既有工具配置 spec 一致）。

User defaults **SHALL** 跨会话生效，**SHALL NOT** 靠创建空会话写入。

首次发送时，客户端或服务端 **SHALL** 将当时 composing overlay merge 进该会话 `session.extra`。

COMPOSING 的 overlay **SHALL NOT** 要求服务端持久化；刷新后丢失 **SHALL** 可接受。

#### Scenario: 首次发送合并 overlay

- **WHEN** 用户在 COMPOSING 选择非默认模型后发送首条消息
- **THEN** 会话 `extra.model_id` SHALL 等于该选择

#### Scenario: 新 COMPOSING 使用 User defaults

- **WHEN** 用户已设置默认模型并打开 `/chat/new`
- **THEN** ModelSelector SHALL 展示该默认
- **AND** SHALL NOT 为此创建可列表会话

---

### Requirement: 未发送附件 SHALL 仅在本地队列且刷新可丢弃

在启用 chat 附件的 qa_type（如 `COMMON_QA`、`SUPER_AGENT_QA`）下，选文件 / 拖拽 / 粘贴 **SHALL** 只进入本地待发送队列，行为遵循 `chat-composer-send-upload`：

- 发送前 **SHALL NOT** 调用 `POST .../attachments`；
- 发送前 **SHALL NOT** 写入企业知识库 Collection；
- 浏览器刷新、`newChat`、切换到另一历史会话时，**SHALL** 丢弃本地队列与未发送输入；
- **SHALL NOT** 提供 draft/staging 服务端恢复未发送附件的产品路径。

点击发送且通过校验后，编排 **SHALL** 为：ensure → 串行 upload → 组装 `file_dict` → SSE stream → 清空本地队列。

发送按钮：无有效 user 文字则禁用（与 `chat-composer-send-upload` 一致）。

#### Scenario: 选文件无 HTTP

- **WHEN** 用户在 COMMON_QA 的 COMPOSING 选择 `report.pdf`
- **THEN** UI SHALL 展示待发送项
- **AND** SHALL NOT 发起附件或 KB upload HTTP

#### Scenario: 刷新丢弃未发送附件

- **WHEN** 用户已选文件入队但未发送，随后刷新 `/chat/new`
- **THEN** 本地队列 SHALL 为空
- **AND** SHALL NOT 要求服务端删除未上传附件（因从未上传）

#### Scenario: 发送时先 ensure 再 upload

- **WHEN** 用户发送带本地队列附件的消息
- **THEN** 前端 SHALL 先 ensure 会话再 POST attachments，再发起 stream
- **AND** `file_dict` SHALL 在 upload 成功之后组装

#### Scenario: 纯文本发送不调用附件 API

- **WHEN** 用户仅发送文字且队列为空
- **THEN** SHALL ensure（或 stream 内幂等物化）与 stream
- **AND** SHALL NOT 调用附件 upload API

---

### Requirement: 会话列表 SHALL NOT 展示无 user 消息的空壳

默认会话列表（含侧栏查询）**SHALL** 仅包含至少有一条未删除 `role=user` 消息的会话（或产品等价的「已产生对话」条件）。

零 user 消息的占位「新对话」**SHALL NOT** 出现在默认列表。系统 **MAY** 清理历史脏数据；**SHALL NOT** 要求用户手动删因挂载产生的空项。

本能力 **SHALL NOT** 依赖 `lifecycle=draft` 字段。

#### Scenario: 多次刷新不堆侧栏

- **WHEN** 用户对 `/chat/new` 连续刷新 3 次且从未发送
- **THEN** 因此新增的可点击空会话数 SHALL 为 0

#### Scenario: 首条消息后出现在列表

- **WHEN** 用户首次发送成功且 user 消息已落库
- **THEN** 该会话 SHALL 出现在历史列表中

---

### Requirement: ACTIVE 会话 SHALL 可通过 URL 刷新续聊

系统 SHALL 支持 `/chat/{sessionId}`（或等价）定位 ACTIVE 会话。

首次发送成功后，前端 **SHALL** `replace` 为该会话 URL。

刷新 ACTIVE URL 时，前端 **SHALL** 加载该会话消息与详情（含 `extra`），**SHALL NOT** 用新的 COMPOSING 空面覆盖该会话。

#### Scenario: 刷新 ACTIVE 保留消息

- **WHEN** 用户在已有消息的 `/chat/{sessionId}` 刷新
- **THEN** 页面 SHALL 展示该会话历史消息
- **AND** SHALL NOT 另造一条可列表空会话作为当前面

#### Scenario: 发送后 URL 含 sessionId

- **WHEN** 用户从 `/chat/new` 成功发送首条消息
- **THEN** 地址 SHALL 变为包含该 `sessionId` 的 ACTIVE 路由

---

### Requirement: newChat SHALL 进入干净 COMPOSING

- 当前为 `ACTIVE`：SHALL 清空未发送编辑态，进入新的 COMPOSING（新内存 session id 供下次发送）；
- 当前为有脏状态的 COMPOSING：SHALL 清空本地状态并换新内存 id（或经确认后等同）；
- 当前为干净 COMPOSING：MAY 提示已是最新对话。

`newChat` **SHALL NOT** 仅为换 id 而 ensure 出侧栏空项。

#### Scenario: 从 ACTIVE 新建

- **WHEN** 用户在 ACTIVE 点击新建对话
- **THEN** 对话面 SHALL 为无消息的 COMPOSING
- **AND** 原 ACTIVE 会话仍在历史列表中

---

### Requirement: FAULT_OPERATION_QA SHALL 禁止一切附件上传入口

在 `qa_type=FAULT_OPERATION_QA` 下，系统 **SHALL NOT** 展示或接受会话附件 / KB 上传（含拖拽、粘贴、Composer `+`），**SHALL NOT** 调用 attachments 或 `requirement_docs` / 测试集合 upload。

#### Scenario: FAULT 下上传被拒绝

- **WHEN** 用户在故障运维面尝试上传文件
- **THEN** SHALL 提示不支持
- **AND** SHALL NOT 产生 KB 或会话附件写入

---

### Requirement: qa_type SHALL 共用物化时机

`COMMON_QA`、`SUPER_AGENT_QA`、`FAULT_OPERATION_QA` 主 chat 面 **SHALL** 共用「发送才物化」规则；能力用 flags 区分，**SHALL NOT** 复制第二套物化时机。

`TEST_CASE_QA` 收编后 **SHALL** 遵循同一「有实质提交后再进列表」语义；收编前 **MAY** 继续 `POST /sessions`，但 **SHALL NOT** 导致主 chat 侧栏被挂载污染。

#### Scenario: 切换 qa_type tab 不刷屏空会话

- **WHEN** 用户在未发送时于 COMMON 与 SUPER 间切换
- **THEN** 历史侧栏 SHALL NOT 因此增加多条空「新对话」

---

### Requirement: 与既有附件消费链兼容

发送成功并绑定到会话后的附件 **SHALL** 仍由 `chat-session-attachments` 与 ChatAttachmentsMiddleware 消费；`file_dict` 哨兵 **SHALL** 保持 `__CHAT_ATTACHMENT__:<uuid>`（除非另有变更专改）。

磁盘布局 **SHALL** 符合 `agent-runtime-paths`（`sessions/{session_id}/uploads|attachments`）。

#### Scenario: Agent 消费已发送附件

- **WHEN** 用户发送带文档附件的消息且 stream 正常
- **THEN** Agent SHALL 能按既有附件工具链读取该附件
