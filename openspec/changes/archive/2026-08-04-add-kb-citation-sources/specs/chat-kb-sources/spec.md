## ADDED Requirements

### Requirement: Citation SHALL 由 Prompt 生成普通 Markdown

系统 SHALL 通过共享 system prompt 要求 Agent 在普通 Markdown 回答中为 Web 和 KB 统一生成 `[n]` 引用及 `### 参考资料`。系统 SHALL NOT 使用 typed answer segment、structured `response_format` 或虚拟 Tool。平台 MAY 解析已生成的 Markdown 编号并将其与本轮 retrieval 做确定性匹配，但 SHALL NOT 改写模型正文。

#### Scenario: 模型引用网页

- **WHEN** 回答使用 `web_search` 或 `web_fetch` 返回的事实
- **THEN** 模型 SHALL 在事实附近输出 `[n]`
- **AND** 参考资料的对应条目 SHALL 包含工具返回的原始 URL
- **AND** SHALL NOT 输出内部 evidence ID

#### Scenario: 工具没有提供来源

- **WHEN** 工具结果不包含可识别来源
- **THEN** 模型 SHALL NOT 编造引用
- **AND** MAY 明确说明依据不足

### Requirement: Retrieval results SHALL NOT 冒充 cited sources

平台 MAY 持久化工具返回的 retrieval results，并在回答末尾通过紧凑来源入口和来源抽屉展示，但 SHALL NOT 将 Top-K、score 或全部检索结果自动称为“引用”或“答案依据”。平台 SHALL NOT 解析模型 Markdown 反推 claim-to-source binding。

#### Scenario: 检索后模型没有引用

- **WHEN** 工具返回检索结果但最终正文没有引用
- **THEN** 正文 SHALL 保持模型原始输出
- **AND** retrieval results MAY 继续通过来源入口和来源抽屉展示

### Requirement: Retrieval results SHALL 使用统一来源抽屉

正文实际出现对应 `[n]`、全部参考资料条目均唯一匹配成功、流式正文已完成且该段之后没有其他正文时，客户端 SHALL 隐藏仅供绑定使用的 `### 参考资料` 段；否则 SHALL 保留原始 Markdown，并将连续参考资料条目分行展示。存在本轮 retrieval results 时，客户端 SHALL 在回答底部工具栏与 token 用量同行展示紧凑来源图标和去重后的来源文档数量，而不是独立的检索结果折叠块。点击入口 SHALL 打开“来源”抽屉，按“引用来源”和“其他检索结果”展示 Web 与 KB 来源；抽屉 SHALL 使用紧凑单行编号条目，只展示单行省略标题及域名或 Collection，不展示 excerpt 正文，并保留完整标题供 hover 查看；原始 Markdown SHALL 保持完整。

#### Scenario: 查看本轮全部来源

- **WHEN** 用户点击回答末尾的来源入口
- **THEN** 客户端 SHALL 打开来源抽屉
- **AND** Web 来源 SHALL 可安全打开原始 URL
- **AND** KB 来源 SHALL 在新标签页进入对应 Collection 文档并打开该文件的分片抽屉
- **AND** 未被正文引用的结果 SHALL 归入“其他检索结果”

### Requirement: 可点击引用 SHALL 来自本轮 retrieval

客户端 SHALL 解析正文 `[n]` 与参考资料条目，并使用 canonical URL（Web）或文件名与 Collection（KB）与已持久化的本轮 retrieval results 匹配。Web 展示标题 MAY 与 retrieval title 不同，不参与来源身份判断。只有唯一匹配成功的条目 SHALL 渲染为可点击上标。

#### Scenario: Web 编号匹配成功

- **WHEN** 参考资料的 URL canonicalize 后唯一匹配本轮 Web retrieval
- **THEN** 对应 `[n]` SHALL 渲染为可点击上标
- **AND** 点击 SHALL 打开当前回答的来源抽屉并滚动、高亮对应编号
- **AND** 点击抽屉条目 SHALL 使用安全外链策略打开原始 URL

#### Scenario: KB 编号匹配成功

- **WHEN** 参考资料的文件名、Collection 和可用 locator 唯一匹配本轮 KB retrieval
- **THEN** 对应 `[n]` SHALL 渲染为可点击上标
- **AND** 点击 SHALL 打开当前回答的来源抽屉并滚动、高亮对应编号
- **AND** 点击抽屉条目 SHALL 在新标签页进入受认证保护的对应 Collection 并打开该文件的分片抽屉

#### Scenario: 条目无匹配或多义

- **WHEN** 参考资料无法唯一匹配本轮 retrieval
- **THEN** 平台 SHALL NOT 生成可点击 citation
- **AND** 正文 SHALL 保持模型原始 Markdown
