# agent-runtime Delta

## MODIFIED Requirements

### Requirement: Context Management SHALL 实现 Claude Code 式分层策略

场景 prompt、用户输入和附件 SHALL 在调用 Agent 前准备。稳定来源 SHALL 与 conversation 分离；压力处理 SHALL 按 tool-result replacement、snip、micro-compraction、conversation compaction 和 reactive overflow recovery 的顺序进行。LangChain/DeepAgents 能力只在行为契约符合时 SHALL 直接采用；缺失时 SHALL 由 Noesis 的窄 middleware 或 runtime adapter 补足。

每次模型调用 SHALL 只有一份 canonical request。可从当前权威源重建的稳定内容 SHALL NOT 被固化进 conversation summary。

Compaction SHALL 按最终 request 预算判断，预算至少覆盖 system instructions、conversation、tool results 与 tool definitions。淘汰的 history SHALL 在摘要替换前具有可恢复记录；摘要失败 SHALL NOT 以错误文本不可逆替换原 history。

**tool-result replacement SHALL 满足 append-only 投影契约**：同一份历史在任何两次模型调用间投影结果逐字段一致；超过单条预算（`runtime.tool_output_max_chars`）的工具结果与 assistant 大工具参数 SHALL 在内容第一次进入有效历史时一次替换定型，此后按记录重放同一替换文本，SHALL NOT 存在「先保留原文、后按消息年龄改写」的两段式替换；SHALL NOT 对已替换文本做二次替换。并行批次的工具结果 SHALL 只受单条预算约束，SHALL NOT 因批次合计超限被强制替换（上下文总量护栏归 compaction 层）。

#### Scenario: 投影幂等

- **WHEN** 同一份历史（含超限工具结果与大参数）被连续两次投影进模型请求
- **THEN** 两次投影的消息序列 SHALL 逐字段一致
- **AND** 第二次投影 SHALL NOT 产生新的替换记录或新增 artifact 写入

#### Scenario: 并行批次只受单条预算约束

- **WHEN** 一次并行工具调用产生多条结果，每条都在单条预算内但批次合计较大
- **THEN** 全部结果 SHALL 原样进入有效历史
- **AND** SHALL NOT 因批次合计超限替换其中任何一条

#### Scenario: Preview 是配置预览

- **WHEN** 设置服务预览某用户与 Agent Profile 的上下文配置
- **THEN** preview SHALL 展示场景 prompt 及配置的 Skills/Memory 来源
- **AND** SHALL NOT声称等于最终 Provider request，也不得调用模型、创建 checkpoint或写入数据

#### Scenario: 最终 Request 触发压缩

- **WHEN** conversation history 单独未达到阈值，但稳定指令或 tool definitions 加入后达到 compaction threshold
- **THEN** 系统 SHALL 在发送模型前执行 compaction
- **AND** SHALL NOT 仅因 history 计数较小而直接进入超限终态

#### Scenario: 压缩后重建稳定内容

- **WHEN** history 被 summary 与 preserved tail 替代
- **THEN** 当前场景指令、Skills、Memory、工具定义与动态时间 SHALL 由各自 owner 保持可用
- **AND** preserved tail 已包含的 conversation 内容 SHALL NOT 重复注入

#### Scenario: Tool Pair 不可切断

- **WHEN** canonicalization 或 compaction 处理 tool call、invalid tool call、tool result 或 thinking block
- **THEN** 下一次模型请求 SHALL 保持 Provider 接受的配对与顺序
- **AND** SHALL NOT 从关联 call/result 中间切断 preserved tail

## ADDED Requirements

### Requirement: read_file SHALL 在源头封顶输出

read_file 工具的返回内容 SHALL 在工具层截断到 `runtime.read_file_max_chars`（默认 20,000 字符，约为主 Agent 单条工具结果预算的 85%），截断时 SHALL 保留行号并附「使用 offset 参数从截断处续读」提示；封顶 SHALL 同时作用于主 Agent 与子 Agent 的 read_file。新鲜读取 SHALL NOT 依赖预算中间件的事后替换来控制体量。

#### Scenario: 超限读取截断并可续读

- **WHEN** read_file 返回内容超过 `read_file_max_chars`
- **THEN** 返回内容 SHALL 被截断到上限以内并附续读提示
- **AND** 提示 SHALL 说明按行号使用 offset 参数继续读取

#### Scenario: 封顶与结果替换互不重叠

- **WHEN** read_file 输出在源头封顶以内
- **THEN** 该输出 SHALL NOT 触发 tool-result replacement（预算中间件不产生替换记录）

### Requirement: Runtime Context SHALL 以冻结头部块注入

运行时动态上下文（日期、时区、workspace）SHALL 在会话首个 agent run 边界解析一次并冻结为头部块：内容只到**日期粒度**，经 private state 持久化，之后每次模型调用以消息序列首位的 SystemMessage 投影且逐字节不变。跨日时 SHALL NOT 改写冻结块，SHALL 在本轮请求**尾部**追加日期纠正声明；附件集合变化（非空且与上次记录不同）时 SHALL 在尾部追加附件声明。历史中间位置 SHALL NOT 注入不持久化的上下文块。

#### Scenario: 同日多轮前缀稳定

- **WHEN** 同一会话同一天内进行多轮对话
- **THEN** 每轮请求的头部冻结块 SHALL 逐字节一致
- **AND** SHALL NOT 存在日期或时间逐轮变化的内容

#### Scenario: 跨日尾部纠正

- **WHEN** 会话跨过日期边界后继续
- **THEN** 冻结块 SHALL 保留原日期不动
- **AND** 本轮请求 SHALL 在尾部追加新日期声明

#### Scenario: 子 Agent 继承冻结块

- **WHEN** 子 Agent 在同一会话内启动且继承 private state
- **THEN** 子 Agent SHALL 复用父会话的冻结块，SHALL NOT 重新解析产生不同头部内容

### Requirement: web_fetch SHALL 输出单份正文并有界截断

web_fetch 的工具输出 SHALL 只携带一份抓取正文（检索结果结构中的 snippet 字段），SHALL NOT 同时在顶层字段重复存储同一文本。`fetch_max_chars` 默认值 SHALL 为 16,000 字符。超过上限的页面 SHALL 按头 75% + 尾 25% 截断且切点对齐 markdown 行边界；全文 SHALL 尽力写入 agent backend（`/web_pages/` 前缀，有界上限），页脚 SHALL 说明已展示/全文的字符量、保存路径与精确续读 offset。provider 层 SHALL NOT 预先截断正文。

#### Scenario: 单份存储

- **WHEN** web_fetch 返回页面正文
- **THEN** 输出 JSON 中该正文 SHALL 仅出现一次
- **AND** 检索来源注册与展示 SHALL 从 results 结构读取

#### Scenario: 超限页面头尾截断并可续读

- **WHEN** 抓取的页面超过 `fetch_max_chars`
- **THEN** 返回内容 SHALL 保留头部与尾部（行边界对齐）并标记中间段省略
- **AND** agent backend 可用时 SHALL 全文落盘，页脚给出保存路径与指向省略段的 read_file offset
- **AND** backend 不可用时 SHALL 退化为纯头尾截断并如实说明全文未存储
