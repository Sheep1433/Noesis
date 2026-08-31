# platform-chat Delta

## MODIFIED Requirements

### Requirement: 消息列表与详情

系统 SHALL 提供按会话拉取消息历史的 API；返回结构 SHALL 支持前端按 parts 渲染（含 tool / reasoning / HITL 部件）。retrieval part SHALL 支持可选 `origin` 字段标记来源归属（主 Agent 自检索 / 具体子 Agent 任务；缺省视为主 Agent，旧数据兼容）。

#### Scenario: 历史含通道来源消息

- **WHEN** 同会话存在经 Telegram 入站写入的 user 消息
- **THEN** 网页历史 API SHALL 可见该消息（来源元数据 MAY 暴露）

#### Scenario: origin 字段兼容

- **WHEN** 历史 retrieval part 无 origin 字段
- **THEN** 前端 SHALL 按主 Agent 自检索归组渲染，SHALL NOT 解析失败

## ADDED Requirements

### Requirement: 研究弧来源聚合展示

来源面板 SHALL 按**研究弧**聚合展示：弧为一条真实用户消息（`source_kind != bg_task_notice` 的 user 消息）到下一条真实用户消息之间的全部 assistant 消息；弧内**过程消息**（派发、进度叙事）SHALL NOT 渲染来源面板；弧内**最后一条**消息 SHALL 渲染该弧全部消息落库 retrieval parts 的聚合面板（按 canonical URL 去重；**平铺展示，不按贡献者分组**——2026-08-31 用户裁决），被打断的弧以末条消息为聚合位。面板 SHALL 区分「引用 M / 共检索 N」：引用子集按「结构化引用标记优先、URL 归因兜底」判定——交付文本中的协议化引用标记 `[citation:标题](ref)`（ref 为原始 URL 或 kb 引用，与正文角标渲染同一语法）精确命中；模型输出的残缺标记（无 ref 括号）按 host / 标题宽容匹配；正文与报告文件中的裸 URL canonical 匹配兜底。归因文本 = 交付消息顶层正文 + 弧内写入文件内容（write_file / edit_file 的写入正文，文件交付场景的报告本体）；过程消息的叙述正文不参与判定。归因文本无任何信号（无标记也无 URL）时面板降级为仅「共检索 N」。面板 SHALL 为持久化消息数据的纯函数：来源 parts 落库即定稿（只追加、不回写历史），弧边界仅由消息历史决定，系统 SHALL NOT 维护会话级可变来源集合。

#### Scenario: 过程消息无面板、交付消息聚合

- **WHEN** 一轮研究被续跑拆为派发、进度、交付多条 assistant 消息，且收取来源落在过程消息上
- **THEN** 过程消息 SHALL NOT 渲染来源面板
- **AND** 交付消息 SHALL 渲染弧内全部来源的聚合面板（含落位在过程消息上的 parts）

#### Scenario: 引用分层

- **WHEN** 弧内共检索去重 40 个来源，交付正文出现其中 12 个 URL
- **THEN** 面板 SHALL 显示「引用 12 · 共检索 40」，默认展开 12 条引用项

#### Scenario: 结构化标记与文件内容归因

- **WHEN** 交付文本含 `[citation:标题](https://…)` 标记或模型残缺输出 `[citation:github.com]`
- **THEN** 引用判定 SHALL 按标记 ref（或残缺线索的 host / 标题）命中弧内来源条目
- **AND** 交付物为写入文件的报告时，报告文件内容（write_file / edit_file 写入正文）SHALL 参与引用归因，报告内标记 / URL 命中的来源计入引用子集

#### Scenario: 多轮隔离与历史稳定

- **WHEN** 上一轮研究引用 30 个来源、同会话下一轮研究引用 40 个来源
- **THEN** 两轮交付消息的面板 SHALL 各自显示 30 与 40，SHALL NOT 合并
- **AND** 页面刷新后历史交付消息的面板 SHALL 与交付当时一致（纯函数重算）

#### Scenario: 文件交付降级

- **WHEN** 交付物写入 workspace 文件且交付说明正文与报告文件内容均不含来源 URL 与引用标记
- **THEN** 该弧面板 SHALL 降级为仅「共检索 N」，SHALL NOT 展示无依据的引用子集

#### Scenario: 平铺展示与计数

- **WHEN** 弧内来源含主 Agent 自检索与多个子 Agent 贡献
- **THEN** 面板 SHALL 平铺展示全部去重来源（引用项在前，其余检索来源折叠其后），SHALL NOT 按贡献者分组
- **AND** 面板计数 SHALL 为去重数（retrieval part 的 origin 归因数据保留落库，不用于展示分组）
