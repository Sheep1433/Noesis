# Tasks: session-history-search

> 依赖：offline-evals 的 delta 基于 eval-metrics-baseline 归档后的主规格；实施顺序上本 change 在其之后。

## 1. 检索基础

- [x] 1.1 Alembic 迁移：`t_chat_message.content` 建 pg_trgm GIN 索引（幂等；含 extension 存在性检查）+ `t_chat_session` 增加 `compaction_cutoff_seq` 列
- [x] 1.2 `noesis/services/history_search.py` 检索服务：单会话检索（关键词 + 压缩边界过滤 + top-k + 按 sequence 升序 + 滚动形态 around_sequence/window）与跨会话发现（user_id 归属过滤 + 按会话分组 + 排除当前会话 + kind/parent 血缘信息返回），附单测（真实 PG 或测试容器）
- [x] 1.3 压缩完成时写 `t_chat_session.compaction_cutoff_seq`（被压缩前缀最大 message_sequence；摘要消息不落库，边界存会话列），单测钉住
- [x] 1.4 GIN 索引写入侧调参：`fastupdate`/pending list 上限按消息表写入速率设定，迁移含 ANALYZE，索引膨胀监控说明写入服务模块注释

## 2. 工具层

- [x] 2.1 `agents/tools/history_search_tool.py`：`search_history`（可选 session_id 定点 + before_compaction + 滚动深读 around_sequence/window + 单条截断与总量上限 + parts 纯文本渲染）/ `search_sessions`（返回含 kind/parent 血缘）两个 StructuredTool（闭包绑定 user_id 与 session_id；description 含 SOURCE-FIRST 规则与"原文层 vs 蒸馏层"分工说明），附单测（fake service）
- [x] 2.2 SuperAgent 工具装配点默认挂载两个工具；GeneralQA 不挂；挂载面单测
- [x] 2.3 边界缺失降级：摘要消息无 `compaction_cutoff_sequence` 时检索全历史并标注"边界未知"，单测

## 3. 评测 recovery 臂

- [x] 3.1 `evals/compression`：fixture transcript 进程内 trigram 索引 + 与产品同 schema 的 `search_history` 工具注入作答模型（作答侧限制为检索片段，不可读全量）
- [x] 3.2 `--arms` 支持 `recovery` 臂；summary 单列「检索兜底收益」（recovery recall% − 闭卷压缩臂 recall%），报告与单测对齐
- [x] 3.3 实跑：cc-0146daeb fixture 双臂（current + recovery）对照，量化兜底收益并记录

## 4. 文档与规格

- [x] 4.1 `evals/README.md` 压缩线补 recovery 臂说明；`docs/engineering/` 对应文档更新（若压缩文档存在则单文件演进）
- [x] 4.2 决策记录：会话检索选确定性全文（pg_trgm）而非向量、两窄工具拆分、SuperAgent 挂载策略、归档文件不作为检索源（含被否方案与业界先例取向）
- [x] 4.3 `python3 scripts/verify-md-links.py` 与 `python3 scripts/verify-decision-format.py` 本地过

## 5. 收尾验证

- [x] 5.1 全量后端测试绿（`uv run pytest tests/ -q`）
- [x] 5.2 实机冒烟：SuperAgent 长会话触发压缩后，用 `search_history` 找回压缩前细节一次（手工会话验证）
- [x] 5.3 code-review（仓库规范 + 本 change spec 两轴），按审查经济学过 blocker
