## Purpose

本能力定义 Noesis **知识库文档解析** 模块行为：将上传文件转为统一 Markdown 中间态，供分块与入库使用；支持按 `parser_id` 与文件类型选择解析实现，并缓存解析产物以避免重复计算。

## ADDED Requirements

### Requirement: 解析器工厂

系统 SHALL 提供 `ParserFactory`（或等价入口），根据 `parser_id` 与文件扩展名选择解析实现，返回 `ParsedFile`（含 `clean_markdown`、`raw_markdown`、`file_name`、`file_type` 等字段）。

#### Scenario: 默认 markitdown 解析 docx

- **WHEN** `parser_id` 未指定且上传 `.docx`
- **THEN** 系统 SHALL 使用 markitdown 路径产出 Markdown 文本

#### Scenario: 不支持的扩展名

- **WHEN** 文件扩展名不在允许列表内
- **THEN** 系统 SHALL 返回 400 或业务约定错误，且 SHALL NOT 写入空向量点

### Requirement: 支持的 parser_id

系统 SHALL 支持至少：`markitdown`（默认）、`docling`（Office 增强）、`mineru`（PDF，可选）。`parser_id` MAY 在集合 `processing_params` 或当次上传参数中指定。

#### Scenario: 配置 PDF 使用 mineru

- **WHEN** 集合 `processing_params.parser_id=mineru` 且 MinerU 端点已配置
- **THEN** PDF 上传 SHALL 经 MinerU 解析为 Markdown

#### Scenario: mineru 未配置时 PDF 回退

- **WHEN** `parser_id=mineru` 但 MinerU 未配置
- **THEN** 系统 SHALL 回退 markitdown 解析 PDF 并记录 warning

### Requirement: Markdown 中间态缓存

成功解析后，系统 SHALL 将 Markdown 产物路径或对象存储键写入文档元数据，供重索引复用。重索引时若源文件未变且缓存存在，MAY 跳过重复解析。

#### Scenario: 重索引复用 markdown

- **WHEN** 对同一 `file_id` 触发重索引且源文件 checksum 未变
- **THEN** 系统 MAY 直接读取已缓存 Markdown 进入分块，无需再次调用外部解析器

### Requirement: 表格类文件行级文档

对 Excel/CSV 等表格文件，系统 SHALL 继续支持行级 `Document` 入库（`is_tabular` 路径），且 SHALL NOT 强制经 Markdown preset 分块破坏行语义。

#### Scenario: xlsx 按行入库

- **WHEN** 上传 `.xlsx` 且每行可转为非空文本
- **THEN** 系统 SHALL 产出行级分片而非强制 Markdown 标题分块

### Requirement: 解析与分块解耦

解析模块 SHALL NOT 内含分块逻辑；分块 SHALL 仅由 `kb-chunking` 模块在 Markdown 产出后执行。

#### Scenario: 解析输出仅 Markdown

- **WHEN** 解析完成
- **THEN** 输出 SHALL 为可供 `chunk_text_for_kb` 消费的文本，且不包含 Qdrant 写入逻辑
