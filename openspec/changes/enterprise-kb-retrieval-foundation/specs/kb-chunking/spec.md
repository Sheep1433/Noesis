## Purpose

本能力定义 Noesis **知识库分块（chunking）** 模块行为：在 Markdown 中间态上按场景 preset 切分文本、合并入库处理参数、产出可写入 Qdrant 的分片记录，并保证生效参数可追溯。供 `knowledge-base` 入库流水线与离线评测复用。

## ADDED Requirements

### Requirement: 分块 preset 枚举

系统 SHALL 支持以下 `chunk_preset_id`：`general`（默认）、`qa`、`book`、`laws`。系统 SHALL 将历史值 `markdown_headers` 规范化为 `general` 的标题感知路径，且 SHALL NOT 因传入 `markdown_headers` 而采用与 `general` 不一致的独立算法。

#### Scenario: 未指定 preset 时使用 general

- **WHEN** 合并后的 `processing_params` 未包含 `chunk_preset_id`
- **THEN** 系统 SHALL 使用 `general` preset 执行分块

#### Scenario: markdown_headers 别名兼容

- **WHEN** `chunk_preset_id` 为 `markdown_headers`
- **THEN** 系统 SHALL 按 `general` preset 处理，且写入 payload 的 `effective_processing_params.chunk_preset_id` SHALL 为 `general`

### Requirement: processing_params 三层合并

系统 SHALL 通过 `resolve_effective_processing_params` 按优先级合并入库参数：**集合默认（MySQL）** → **文档持久化覆盖** → **当次 ingest 的 request_once**。同一键名高层 SHALL 覆盖低层。仅当次覆盖 SHALL NOT 写回文档级持久化默认值。

#### Scenario: 当次上传覆盖 preset

- **WHEN** 集合默认为 `general`，当次上传请求指定 `chunk_preset_id=qa`
- **THEN** 该次入库 SHALL 使用 `qa` 分块
- **AND** 集合默认配置 SHALL 保持 `general`

### Requirement: 分块单一调度入口

系统 SHALL 提供 `chunk_text_for_kb`（或等价函数），输入为 Markdown 文本、`file_id`、`filename` 与合并后的 `effective_params`，输出为分片记录列表。每条记录 SHALL 至少含 `content`、`chunk_index`、`file_id`、`filename`；MAY 含 `header_path`、`start_char_pos`、`end_char_pos`。

#### Scenario: general preset 产出非空分片

- **WHEN** 输入为非空 Markdown 且 preset 为 `general`
- **THEN** 系统 SHALL 返回至少一个非空 `content` 的分片记录

#### Scenario: 未知 preset 回退

- **WHEN** `chunk_preset_id` 为系统不识别的字符串
- **THEN** 系统 SHALL 回退 `general` 并记录 warning 日志

### Requirement: preset 语义边界

- `general` SHALL 支持按分隔符与 token 上限合并（`naive_merge` 语义），并保留 Markdown 标题路径元数据。
- `qa` SHALL 优先识别问答对结构（问题-答案），适合 FAQ、题库类文本。
- `book` SHALL 强化章节标题识别与层级合并，适合长章节文档。
- `laws` SHALL 按法条层级组织与合并，适合制度、法规类文本。

#### Scenario: qa preset 对 FAQ 文本

- **WHEN** 文本含明确「问：」「答：」或等价格式且 preset 为 `qa`
- **THEN** 产出分片 SHALL 尽量保持一问一答在同一 chunk 或相邻可追溯 chunk 内

### Requirement: 生效参数写入 payload

对本能力落地后新索引的 Qdrant 点，系统 SHALL 在 payload 写入 `effective_processing_params` 快照（含 `chunk_preset_id`、`chunk_parser_config`、`chunk_engine_version`）。旧分片 MAY 无该字段，SHALL NOT 因此被拒绝检索。

#### Scenario: 新索引分片含 preset 快照

- **WHEN** 使用 `book` preset 完成入库
- **THEN** 分片 payload 的 `effective_processing_params.chunk_preset_id` SHALL 为 `book`

### Requirement: 分块失败回退

当 preset 分块抛出异常或返回空列表时，系统 SHALL 使用内置滑窗分块（基于 `chunk_size` / `chunk_overlap`）完成入库，并 SHALL 记录 warning 级日志。

#### Scenario: 异常时滑窗回退

- **WHEN** preset 分块因异常中断
- **THEN** 系统 SHALL 仍写入至少一个分片（除非源文本为空）
- **AND** SHALL 记录包含 preset 名称的 warning
