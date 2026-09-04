## MODIFIED Requirements

### Requirement: 研究弧来源聚合展示

来源面板 SHALL 按**研究弧**聚合展示：弧为一条真实用户消息（`source_kind != bg_task_notice` 的 user 消息）到下一条真实用户消息之间的全部 assistant 消息；弧内**过程消息**（派发、进度叙事）SHALL NOT 渲染来源面板；弧内**最后一条**消息 SHALL 渲染该弧全部消息落库 retrieval parts 的聚合面板（按 canonical URL 去重；**平铺展示，不按贡献者分组**），被打断的弧以末条消息为聚合位。面板 SHALL 区分「引用 M / 共检索 N」：引用子集 SHALL 只认交付文本与报告文件中完整 `[citation:标题](ref)` 标记（ref 为原始 URL 或 kb 引用，与正文角标渲染同一语法）的 ref 精确命中（web 按 canonical URL、KB 按 kb ref 匹配弧内来源条目）；正文或报告文件中的裸 URL 与残缺标记（无 ref 括号）SHALL NOT 计入引用子集，命中的来源归入「其他检索来源」。归因文本 = 交付消息顶层正文 + 弧内写入文件内容（write_file / edit_file 的写入正文，文件交付场景的报告本体）；过程消息的叙述正文不参与判定。归因文本无完整引用标记时面板 SHALL 降级为仅「共检索 N」。面板 SHALL 为持久化消息数据的纯函数：来源 parts 落库即定稿（只追加、不回写历史），弧边界仅由消息历史决定，系统 SHALL NOT 维护会话级可变来源集合。

#### Scenario: 过程消息无面板、交付消息聚合

- **WHEN** 一轮研究被续跑拆为派发、进度、交付多条 assistant 消息，且收取来源落在过程消息上
- **THEN** 过程消息 SHALL NOT 渲染来源面板
- **AND** 交付消息 SHALL 渲染弧内全部来源的聚合面板（含落位在过程消息上的 parts）

#### Scenario: 引用分层

- **WHEN** 弧内共检索去重 40 个来源，交付正文与报告文件中的完整引用标记命中其中 12 个
- **THEN** 面板 SHALL 显示「引用 12 · 共检索 40」，默认展开 12 条引用项

#### Scenario: 结构化标记与文件内容归因

- **WHEN** 交付文本含 `[citation:标题](https://…)` 标记
- **THEN** 引用判定 SHALL 按标记 ref 精确命中弧内来源条目
- **AND** 交付物为写入文件的报告时，报告文件内容（write_file / edit_file 写入正文）中的完整标记 SHALL 参与引用归因，命中的来源计入引用子集

#### Scenario: 裸 URL 与残缺标记不升格为引用

- **WHEN** 交付正文出现来源 URL 的普通链接（无 `[citation:...]` 标记包裹），或模型输出无 ref 括号的残缺标记 `[citation:github.com]`
- **THEN** 对应来源 SHALL 归入「其他检索来源」，SHALL NOT 计入引用子集
- **AND** 正文 SHALL 保持模型原始 Markdown，平台 SHALL NOT 在 URL 出现处补插引用上标

#### Scenario: 多轮隔离与历史稳定

- **WHEN** 上一轮研究引用 30 个来源、同会话下一轮研究引用 40 个来源
- **THEN** 两轮交付消息的面板 SHALL 各自显示 30 与 40，SHALL NOT 合并
- **AND** 页面刷新后历史交付消息的面板 SHALL 与交付当时一致（纯函数重算）

#### Scenario: 文件交付降级

- **WHEN** 交付物写入 workspace 文件且交付说明正文与报告文件内容均不含完整引用标记
- **THEN** 该弧面板 SHALL 降级为仅「共检索 N」，SHALL NOT 展示无依据的引用子集

#### Scenario: 平铺展示与计数

- **WHEN** 弧内来源含主 Agent 自检索与多个子 Agent 贡献
- **THEN** 面板 SHALL 平铺展示全部去重来源（引用项在前，其余检索来源折叠其后），SHALL NOT 按贡献者分组
- **AND** 面板计数 SHALL 为去重数（retrieval part 的 origin 归因数据保留落库，不用于展示分组）

## ADDED Requirements

### Requirement: 报告文件预览引用编号 SHALL 与所属弧面板一致

chat 页预览 workspace 内由研究弧 write_file / edit_file 写入的 Markdown 文件时，文件内 `[citation:标题](ref)` 标记 SHALL 以**所属弧**（最近写入该文件的弧）的引用优先编号渲染为可点击编号上标；无所属弧的文件 SHALL 保持无编号上标渲染。文件预览中的 KB 引用徽章 SHALL 可点击并跳转受认证保护的对应 Collection 文档路由。文件路径与弧的映射 SHALL 为持久化消息数据的纯函数（后写覆盖先写），SHALL NOT 引入会话级可变状态。

#### Scenario: 报告文件预览编号对齐

- **WHEN** 用户在文件预览中打开某弧 write_file 写入的报告，报告内含 `[citation:标题](https://…)` 标记
- **THEN** 该标记 SHALL 渲染为编号上标，编号与该弧交付消息来源面板的条目编号一致
- **AND** web 引用上标 SHALL 可点击并使用安全外链策略打开原始 URL

#### Scenario: KB 徽章点击回源

- **WHEN** 文件预览中出现 ref 为 `kb:Collection名/文件名` 的引用徽章
- **THEN** 点击 SHALL 跳转到受认证保护的对应 Collection 文档路由

#### Scenario: 非弧写入文件不编号

- **WHEN** 预览的文件不属于任何研究弧的写入记录
- **THEN** 文件内引用标记 SHALL 渲染为无编号上标（现行行为），SHALL NOT 编造编号
