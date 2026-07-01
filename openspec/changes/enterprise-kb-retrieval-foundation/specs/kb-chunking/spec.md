## Purpose

本能力定义 Noesis **知识库分块** 模块：消费 **DeepDoc 解析产物**，经 `DeepDocChunkAdapter` 转为可嵌入的 `Document` 列表。Phase 1 默认模板 `general`（结构/标题感知合并）；**预留** `chunk_template_id` 对接 RAGFlow 更多分块模板（book、paper、laws 等，后续变更）。**不提供 markitdown 降级分块路径**（聊天附件等场景仍 MAY 使用 Markdown 标题切分，但不属于知识库入库主路径）。

## Requirements

### Requirement: 默认分块路径 DeepDocChunkAdapter

当 `parser_id=deepdoc`（默认）时，系统 SHALL 经 `DeepDocChunkAdapter` 将 `DeepDocParseResult` 转为分片列表，而非对整篇 Markdown 做简单 `#` 行首切分。

Adapter SHALL：

- 合并 DeepDoc `blocks` 为语义连贯 chunk（尊重 `layout_type`、标题层级、token/`chunk_size` 上限）
- 表格 SHALL 尽量保持完整或按 DeepDoc 表格单元语义切分，**SHALL NOT** 无脑打散为纯文本行（除非 template 指定）
- 每条 chunk metadata SHALL 含：`chunk_index`、`file_name`、**MAY** 含 `page_no`、`bbox`、`layout_type`、`header_path`（若能从结构推断）

#### Scenario: DeepDoc 解析后产出非空分片

- **WHEN** `DeepDocParseResult` 含非空 `blocks`
- **THEN** Adapter SHALL 返回至少一个非空 `content` 分片

#### Scenario: 表格块完整性

- **WHEN** parse 结果含单个逻辑表格
- **THEN** 默认 template `general` SHALL 尽量使该表格处于同一 chunk 或带明确表格标记的相邻 chunk

### Requirement: 内存 Markdown 文本分块（非入库主路径）

当 `chunk()` 输入为 **str**（非 `ParsedFile` / DeepDoc 产物）时，系统 MAY 使用 `MarkdownChunker.split_markdown_with_headers` 供测试或聊天附件；**知识库入库 SHALL 经 DeepDoc → DeepDocChunkAdapter**。

#### Scenario: 字符串输入标题 metadata

- **WHEN** 直接对 Markdown 字符串分块且含 `# 章节`
- **THEN** 分片 MAY 含 `header_path` 或 `Header_n`

### Requirement: chunk_template_id 与 Phase 1 范围

`processing_params.chunk_template_id`（与 legacy `chunk_preset_id` 合并为同一字段）：

| 值 | Phase 1 |
|----|---------|
| `general`（默认） | **实现** — DeepDoc adapter 默认策略 |
| `book` / `paper` / `laws` / `qa` 等 | **预留** — 规格占位，实现返回 501 或 warning 回退 `general` |

`strategy=markdown_headers` SHALL 规范化为 `chunk_template_id=general`。

#### Scenario: 未指定 template

- **WHEN** 未传 `chunk_template_id`
- **THEN** SHALL 使用 `general`

#### Scenario: 预留 template 回退

- **WHEN** `chunk_template_id=book` 且 Phase 1 未实现
- **THEN** SHALL 回退 `general` 并 warning（除非配置 strict 模式）

### Requirement: Excel 路径

`.xlsx` / `.xls` / `.csv`：

- **优先**：DeepDoc Excel parser（与 upstream 对齐）
- **降级**：现有 pandas / CSVLoader 行级一片

行级结果 **SHALL NOT** 再经 Markdown 标题切分。

#### Scenario: DeepDoc 解析 Excel

- **WHEN** DeepDoc 可用且上传 `.xlsx`
- **THEN** SHALL 经 DeepDoc Excel 路径或降级 pandas，且 `is_tabular` 语义保持一行一片

### Requirement: processing_params 合并

合并顺序：**MySQL 集合默认** → **文档覆盖** → **当次 upload**。可配置：`chunk_template_id`、`chunk_parser_config`（`chunk_size`、`chunk_overlap`）、`parser_id`。

### Requirement: payload 快照

新索引点 SHALL 写入 `effective_processing_params`：`parser_id`、`chunk_template_id`、`chunk_parser_config`、`deepdoc_version`（若适用）、`chunk_engine_version`。

#### Scenario: DeepDoc 入库快照

- **WHEN** 经 DeepDoc + general template 入库
- **THEN** payload SHALL 含 `parser_id=deepdoc` 与 `chunk_template_id=general`

### Requirement: 单一分块入口

对外 SHALL 保持 `chunk(parsed_or_result, effective_params)` 单一入口；内部分派 DeepDoc adapter 或 MarkdownChunker。

#### Scenario: 入库流水线

- **WHEN** `qdrant_service` 执行文档入库
- **THEN** SHALL 经上述入口，且 **SHALL NOT** 在 Service 层重复实现分块逻辑
