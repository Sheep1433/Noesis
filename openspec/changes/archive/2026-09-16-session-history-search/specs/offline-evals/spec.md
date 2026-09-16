# Delta: offline-evals

## MODIFIED Requirements

### Requirement: 消息压缩评测

`evals.compression` SHALL 以「压缩后任务保持率」为 headline 评测消息压缩：评测 SHALL 将真实长会话导出并脱敏为 transcript fixture，由 LLM 从将被压缩的区域生成事实 recall 题库（按 transcript 内容缓存以保证可复现），对每个评测臂（压缩策略档）仅凭压缩后上下文闭卷作答，judge 按 2/1/0（正确/部分/错误）判卷，headline 指标 SHALL 为 recall% @ retained tokens。评测 SHALL 包含 uncompacted 对照臂（不压缩直接闭卷作答，作为 recall 上限），并 SHALL 报告任务保持率 Δ（压缩臂 recall% − uncompacted 臂 recall%）。多策略档 SHALL 经参数化的压缩配置生效。judge 模型 SHALL 与摘要及作答模型分离；judge 解析失败 SHALL 重试后剔除并单列失败率，SHALL NOT 以 0 分计入 recall%。摘要识别 SHALL 依赖压缩中间件写入的结构化标记，SHALL NOT 依赖内容启发式猜测。可种植事实的合成 fixture SHALL 保留为零 LLM 冒烟档。

评测 SHALL 支持 `recovery` 臂：作答模型在压缩后上下文基础上额外获得会话历史检索工具（对 fixture 原文建进程内 trigram 索引，工具 schema 与产品一致），其余条件与压缩臂完全相同。`recovery` 臂与闭卷压缩臂的 recall% 差值 SHALL 报告为「检索兜底收益」。recovery 臂作答 SHALL 限制为检索结果片段（top-k、有长度上限），SHALL NOT 允许读入全量 transcript。

#### Scenario: headline 指标口径

- **WHEN** 压缩评测完成运行
- **THEN** summary SHALL 以 recall% @ retained tokens 为 headline 指标
- **AND** SHALL 同时报告 uncompacted 臂 recall% 与任务保持率 Δ

#### Scenario: uncompacted 对照臂

- **WHEN** 以默认评测臂运行
- **THEN** uncompacted 臂 SHALL 跳过压缩、以同一题库闭卷作答并判卷
- **AND** 其 recall% SHALL 作为该 fixture 的 recall 上限呈现

#### Scenario: recovery 臂对照

- **WHEN** 以含 recovery 的评测臂运行
- **THEN** recovery 臂 SHALL 与闭卷压缩臂同题同判卷，唯一差异为作答模型可用会话检索工具
- **AND** summary SHALL 单列「检索兜底收益」（recovery recall% − 闭卷压缩臂 recall%）
- **AND** recovery 臂的作答上下文 SHALL NOT 包含全量 fixture 原文

#### Scenario: judge 解析失败不污染分数

- **WHEN** judge 输出无法解析为 2/1/0 判分
- **THEN** 该题 SHALL 重试一次，仍失败则剔除出 recall% 分母
- **AND** summary SHALL 单列 judge 解析失败率

#### Scenario: 零 LLM 冒烟

- **WHEN** 使用可种植事实的合成 fixture 运行
- **THEN** 评测 SHALL 不调用任何 LLM 即完成压缩与事实存活断言
