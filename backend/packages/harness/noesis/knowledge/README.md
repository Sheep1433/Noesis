# 知识库模块（`noesis.knowledge`）

位于 harness 包 `packages/harness/noesis/knowledge/`，与 `noesis.storage`（DB engine/ORM/Alembic）、`noesis.repositories`（集合配置 repository）协同。平台 `noesis_server/api/knowledge_base_api.py` 经 `knowledge_base_service` 调用本模块。

## 架构

| 子模块 | 职责 |
|--------|------|
| `deepdoc/` | RAGFlow DeepDoc 移植（Apache-2.0） |
| `parser/` | `ParserFactory` → `DeepDocParseResult` |
| `chunking/` | `DeepDocChunkAdapter` + 参数合并 |
| `retrieval/` | `KbRetrievalService`（hybrid → rerank → top_k） |
| `rerank/` | DashScope text-rerank |
| `embedding/` | 向量嵌入、VLM 判定 |
| `implementations/qdrant.py` | `QdrantService`（Qdrant 客户端 + 入库/检索适配） |
| `base.py` / `factory.py` / `runtime.py` | `KnowledgeBase` ABC + 工厂 + 启动单例 |

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
