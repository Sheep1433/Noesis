## Purpose

本能力定义 Noesis **知识库文档解析** 模块：经 **移植的 RAGFlow DeepDoc**（`kb/deepdoc/`，库形态嵌入，**不**部署 RAGFlow 服务）完成版面理解、OCR、表格结构与多格式解析；输出统一中间结构供 `kb-chunking` 消费。**不提供 markitdown 降级**；模型未就绪时 SHALL 明确失败。

## Requirements

### Requirement: DeepDoc 为唯一解析引擎

系统 SHALL 将 RAGFlow [`deepdoc`](https://github.com/infiniflow/ragflow/tree/main/deepdoc) 模块移植至 `backend/kb/deepdoc/`（遵循 Apache-2.0，保留 LICENSE/NOTICE 与版本 pin），作为 **唯一** 解析实现（`parser_id=deepdoc`）。

DeepDoc SHALL 覆盖至少：**PDF、DOCX、EXCEL、PPT、Markdown、TXT/CSV**（与 upstream 能力对齐）。**不**引入 RAGFlow 完整应用栈、Task 队列或 ES/Infinity 索引。

#### Scenario: 默认 PDF 经 DeepDoc

- **WHEN** 上传 `.pdf` 且 `parser_id` 未指定或 `deepdoc`
- **THEN** 系统 SHALL 调用 DeepDoc 解析器产出结构化 parse 结果（文本块、表格、版面位置元数据）

#### Scenario: 纯 Markdown 轻路径

- **WHEN** 上传 `.md` / `.markdown`
- **THEN** 系统 SHALL 直读 UTF-8 并包装为 `DeepDocParseResult`（跳过 OCR）

### Requirement: DeepDoc 解析产物契约

解析模块 SHALL 输出 `DeepDocParseResult`（或等价 dataclass），至少含：

- `blocks[]`：文本块（`content`、`page_no`、可选 `bbox`、`layout_type`）
- `tables[]`：表格结构化内容或自然语言描述
- `figures[]`（MAY 为空）：图注与文本
- `source_file_name`、`parser_id=deepdoc`、`deepdoc_version`

**SHALL NOT** 在此阶段写入 Qdrant 或执行嵌入。

#### Scenario: PDF 含表格

- **WHEN** DeepDoc 解析扫描 PDF 含表格
- **THEN** 结果 SHALL 含 `tables` 条目且表格内容 SHALL 可映射为可检索文本

### Requirement: 解析器调度

系统 SHALL 提供 `ParserFactory`（或扩展 `DocumentParser`）：

| `parser_id` | 用途 |
|-------------|------|
| `deepdoc`（默认且唯一） | 全部知识库入库解析 |

传入非 `deepdoc` 的 `parser_id` SHALL 返回 **400/422 类可行动错误**，**SHALL NOT** 静默回退其它解析器。

#### Scenario: 模型未下载

- **WHEN** DeepDoc 权重不可用且上传 PDF
- **THEN** 上传 SHALL 失败并返回可行动错误（提示运行模型下载脚本）

#### Scenario: 拒绝 markitdown

- **WHEN** 请求 `parser_id=markitdown`
- **THEN** SHALL 拒绝并说明仅支持 `deepdoc`

### Requirement: 模型与运行依赖

DeepDoc 依赖（OCR / Layout / TSR 等）SHALL 通过配置声明：

- 权重来源：HuggingFace `InfiniFlow/deepdoc` + `InfiniFlow/text_concat_xgb_v1.0`
- 本地缓存目录：默认 `.data/rag/res/deepdoc/`（gitignore）
- GPU：MAY 可选；CPU 路径 SHALL 文档化性能预期

#### Scenario: 配置模型目录

- **WHEN** `config.yaml` 设置 `kb.deepdoc.model_dir`
- **THEN** 系统 SHALL 从该目录加载权重，不硬编码绝对路径

### Requirement: 解析产物缓存

成功 DeepDoc 解析后 MAY 将结构化 JSON 或 Markdown 摘要缓存至 `.data/kb_parse/{collection}/{file_id}.json`；重索引 checksum 未变 MAY 跳过重复解析。

### Requirement: 解析与分块解耦

解析模块 SHALL NOT 内含 Qdrant 写入；分块 SHALL 由 `kb-chunking` 的 `DeepDocChunkAdapter` 消费 parse 结果。

#### Scenario: 输出供 chunk 消费

- **WHEN** DeepDoc 解析完成
- **THEN** 输出 SHALL 传入 `kb-chunking`，而非直接 embedding

### Requirement: 合规与溯源

仓库 SHALL 在 `backend/kb/deepdoc/` 保留：

- upstream commit / version pin（`UPSTREAM.md`）
- Apache-2.0 LICENSE 与 NOTICE 中对 RAGFlow/InfiniFlow 的归属说明

**SHALL NOT** 在对外文档中声称自研 OCR/Layout 模型；SHALL 标注基于 RAGFlow DeepDoc 移植。

### Requirement: Vendor 手工修改须记入 NOTES

对 `backend/kb/deepdoc/`（及未来其它 vendored 上游模块），**凡偏离 upstream 的手动修改**（含 Noesis 适配层同目录内的 patch、路径/配置/依赖调整、行为变更），开发者 **SHALL** 在 `docs/NOTES.md` 同步追加记录，以便后续 diff/合并远程 RAGFlow。

每条记录 **SHALL** 至少含：

- **upstream pin**：RAGFlow commit / tag（与 `UPSTREAM.md` 一致）
- **修改日期**与**修改原因**（Noesis 集成需求）
- **文件路径**与**变更摘要**（必要时附 upstream 文件行号或函数名）
- **同步策略**：`keep`（永久 fork 点）/ `upstream`（待合回上游）/ `drop`（临时垫片，升级后可删）

未修改 upstream 文件的 Noesis 封装（如 `document_parse/factory.py`、`chunk/deepdoc_adapter.py`）**MAY** 不写 NOTES，但 **SHALL** 在 PR/提交说明中区分「vendor 内」与「vendor 外」。

#### Scenario: 修改 deepdoc 内 PDF 解析逻辑

- **WHEN** 开发者变更 `kb/deepdoc/parser/pdf_parser.py`（相对 pin 的 upstream）
- **THEN** 同次或下次合并前 **SHALL** 在 `docs/NOTES.md` 的 DeepDoc vendor 章节追加一条记录

#### Scenario: 仅新增 Noesis 适配代码

- **WHEN** 仅新增 `kb/chunk/deepdoc_adapter.py` 且未改 `kb/deepdoc/**`
- **THEN** **MAY** 不写入 DeepDoc vendor 修改清单

#### Scenario: 升级 upstream 前审查

- **WHEN** 计划将 RAGFlow deepdoc pin 升至新版本
- **THEN** 维护者 **SHALL** 先阅读 `docs/NOTES.md` 中全部 `keep`/`upstream` 项并逐项 reconcile
