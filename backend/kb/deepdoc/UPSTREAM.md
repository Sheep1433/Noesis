# RAGFlow DeepDoc Vendor Pin

| 字段 | 值 |
|------|-----|
| upstream | https://github.com/infiniflow/ragflow |
| commit | `828c5789f651d4c4ebe4645190b8b8d244144fe0` |
| license | Apache-2.0（见同目录 `LICENSE`） |
| 拷贝范围 | `deepdoc/parser/`、`deepdoc/vision/`（不含 `deepdoc/server/`） |

## 拉取 upstream（维护者）

```bash
RAG_COMMIT=828c5789f651d4c4ebe4645190b8b8d244144fe0
git clone --depth 1 https://github.com/infiniflow/ragflow.git /tmp/ragflow
cd /tmp/ragflow && git fetch --depth 1 origin "$RAG_COMMIT" && git checkout "$RAG_COMMIT"
rsync -a --exclude='server/' deepdoc/ /path/to/Noesis/backend/kb/deepdoc/
```

手工修改须同步 [`docs/NOTES.md`](../../../docs/NOTES.md)「DeepDoc Vendor 修改清单」。
