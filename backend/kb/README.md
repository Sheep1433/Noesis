# 知识库模块（`backend/kb/`）

## 架构

| 子模块 | 职责 |
|--------|------|
| `deepdoc/` | RAGFlow DeepDoc 移植（Apache-2.0） |
| `document_parse/` | `ParserFactory` → `DeepDocParseResult` |
| `chunk/` | `DeepDocChunkAdapter` + 参数合并 |
| `retrieval/` | `KbRetrievalService`（hybrid → rerank → top_k） |
| `rerank/` | DashScope text-rerank |
| `embedding/` | 向量嵌入 |

## DeepDoc 模型下载

首次使用 PDF/扫描件解析前下载 ONNX 权重：

```bash
cd backend
uv sync
uv run python -m kb.download_models
```

默认目录：`.data/rag/res/deepdoc/`（可通过 `config.yaml` → `kb.deepdoc.model_dir` 或 `KB_DEEPDOC_MODEL_DIR` 覆盖）。

镜像环境变量（可选）：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## Docker

Compose 将 `noesis_data` 卷挂载到 `/data/noesis`。生产配置见 `deploy/config.docker.yaml`：

```yaml
kb:
  deepdoc:
    model_dir: /data/noesis/rag/res/deepdoc
```

宿主机预下载模型到卷内路径，或在容器内执行上述 download 脚本。

## 评测

```bash
cd backend
uv run python -m evals.kb.run --collection requirement_docs --dataset evals/kb/fixtures/sample.jsonl
```

## Vendor 维护

手工修改 `deepdoc/**` 须同步 `docs/NOTES.md`；升级 upstream 前阅读 `deepdoc/UPSTREAM.md` 与 vendor 清单。
