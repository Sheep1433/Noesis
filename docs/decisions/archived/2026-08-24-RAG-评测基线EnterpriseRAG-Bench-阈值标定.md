# 决策：RAG 评测基线：EnterpriseRAG-Bench 阈值标定

状态：implemented
日期：2026-08-24
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **规模**：500 题下载，211 题可评测（GT 文档全部可得）+ 20 题 info_not_found 负拒备用；1/10 试点入库（erb-eval 集合 48 篇 / 268 chunks，chunk_size=2000/overlap=200）。
- **结论**：21 题阈值校准 Recall@1=100%；rerank 分中位数 GT 0.199 vs 无关 0.078 → 定版：平台默认阈值 0.1、erb-eval（跨语中→英）0.05。原始数据 `/tmp/erb_scan_raw.json`。
- **顺手修复**：source 路径泄漏根治、xgboost 依赖、引用溯源 `file:` 协议容错。
