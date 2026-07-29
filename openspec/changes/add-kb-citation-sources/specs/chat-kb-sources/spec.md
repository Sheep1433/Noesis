## ADDED Requirements

### Requirement: Web 工具 SHALL 产出可绑定 evidence

`web_search` 的每个有效 HTTP(S) 结果与 `web_fetch` 的成功页面 SHALL 进入同一 run-level retrieval manifest，以 canonical URL 去重并获得 opaque `evidence_id`。Web 引用 SHALL 使用 `type=url_citation`，保存 URL、title、生成时 excerpt 快照与结构校验状态。resolve SHALL 只返回落库快照和安全外链，不得因点击引用而由服务端重新抓取任意 URL。

#### Scenario: 搜索或抓取后引用网页

- **WHEN** Web 工具返回网页 evidence 且 typed answer segment 绑定其 evidence id
- **THEN** text part SHALL 生成覆盖该 segment 的 `url_citation`
- **AND** retrieval part SHALL 保留该网页结果

### Requirement: Cited annotations SHALL 与 retrieved results 分层

系统 SHALL 采用 OpenAI-style 两层来源模型：顶层 text part 的 `annotations[]` 表示回答实际引用的 evidence，独立 retrieval part 的 `results[]` 表示检索工具返回的 evidence。cited SHALL 是模型 typed binding 经结构校验后的 retrieved 子集；系统 SHALL NOT 从 Top-K、score 或文件名推断 cited。

#### Scenario: 检索后未绑定 evidence

- **WHEN** 工具返回三个 retrieval results，但 typed answer 没有 cited evidence binding
- **THEN** text annotations SHALL 为空
- **AND** 三个 results MAY 保留为折叠的检索结果

#### Scenario: 一个回答片段由多个 evidence 支撑

- **WHEN** 同一 answer segment 绑定两个已登记 evidence id
- **THEN** 对应 text range SHALL 允许存在两个 typed citation annotations

### Requirement: Citation SHALL 是 text annotation 而非正文 marker

用户可见正文 SHALL 只包含回答文本。引用身份与定位 SHALL 存在于 typed annotation 中，前后端 SHALL NOT 依赖 `[[source:...]]`、`[ID:n]` 或其它嵌入正文的 marker 建立映射。数字角标仅是 annotation 的展示结果，不进入模型协议或持久身份。

#### Scenario: 复制带引用的回答

- **WHEN** 用户复制 assistant 的纯正文
- **THEN** 复制内容 SHALL 不包含内部 evidence id、source token 或 marker

### Requirement: Citation SHALL 至少通过结构校验

平台 SHALL 在生成 citation annotation 前验证：binding evidence 来自本轮 retrieval manifest、document/version/segment identity 一致、locator schema 可解析、text offset 非空且未越界。通过首版校验的 annotation SHALL 标记 `verification=structural`，SHALL NOT 宣称已完成语义事实验证。

#### Scenario: 无效 binding 不污染正文

- **WHEN** 一个 binding 未通过结构校验
- **THEN** 平台 SHALL 不生成对应 annotation
- **AND** 正文与合法 annotations SHALL 继续交付

### Requirement: Answer segment 拼接与 annotation 时序 SHALL 确定

平台 SHALL 按 typed `segments[]` 顺序原样、零分隔符拼接 `text`，不得隐式加入空格或换行；所有分隔字符必须来自 segment text。offset SHALL 包含这些原始字符。segment streaming 的 annotation SHALL 最迟在对应 `text-end` 前登记；仅终态 structured output MAY 在 `text-end` 后登记，但 SHALL 早于 `finish` 与终态持久化。

#### Scenario: 两个 segment 组成段落

- **WHEN** 第一个 segment 以换行结束且第二个 segment 以正文开始
- **THEN** 最终 text SHALL 等于两个 segment text 直接连接
- **AND** 第二个 segment 的 start index SHALL 包含第一个 segment 自带换行的长度

### Requirement: Citation resolve SHALL 以 assistant message 为授权根

`GET /api/chat/messages/{message_id}/citations/{citation_id}` SHALL 从指定 assistant 消息的 text annotation 读取 document/version/segment identity，并重新校验当前用户对 session、message 与知识库对象的访问权限。客户端 SHALL NOT 通过该 API 覆盖 collection、document、version、segment 或 locator。

#### Scenario: 跨会话猜测 citation id

- **WHEN** 用户请求不属于自己的 message/citation 组合
- **THEN** 服务端 SHALL 拒绝访问且 SHALL NOT 读取对应知识库片段

#### Scenario: 原证据已不可定位

- **WHEN** annotation 指向的 version 或 segment 已删除
- **THEN** 服务端 SHALL 返回明确 stale/missing 或 404/410 语义
- **AND** SHALL NOT 静默返回相似片段

#### Scenario: 消息快照与当前 evidence 不同

- **WHEN** annotation 保存了生成时 excerpt，且用户点击 citation
- **THEN** resolve SHALL 重新鉴权并实读精确 version/segment
- **AND** 权限撤销或 evidence 缺失时 SHALL 返回 forbidden/stale/missing
- **AND** SHALL NOT 把落库 excerpt 快照伪装成实读成功

### Requirement: Retrieval manifest SHALL 有确定容量预算

系统 SHALL 对每次检索结果数、run 级去重 evidence 数、excerpt 字符/bytes、locator JSON bytes 与 assistant content JSON bytes 执行可配置硬上限。超限时 SHALL 确定性截断 retrieved-only 数据、标记 truncated 并记录指标；已经被合法 annotation 引用的 evidence SHALL 优先保留。

#### Scenario: 多次并行检索超过消息预算

- **WHEN** 一轮 run 的去重 retrieval results 超过配置上限
- **THEN** 系统 SHALL 优先保留 cited evidence，再按确定排序保留 retrieved-only evidence
- **AND** 最终 assistant content SHALL 不超过配置的 JSON byte 上限

### Requirement: UI SHALL 默认展示 cited，折叠 retrieved-only

前端 SHALL 根据 text annotation range 渲染可点击角标，并使用同一 annotation 聚合默认来源列表。未被引用的 retrieval results MAY 显示在折叠的“本轮检索结果”中，但 SHALL NOT 使用“引用”或“答案依据”文案。

#### Scenario: 仅有 retrieved results

- **WHEN** assistant 没有 citation annotation 但存在 retrieval part
- **THEN** UI SHALL 显示零个引用来源
- **AND** MAY 提供折叠入口查看本轮检索结果
