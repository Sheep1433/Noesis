## 1. 检索解析下沉（同构）

- [x] 1.1 把 `retrieval_payload` 解析与 retrieval part 构造从 `langgraph_bridge` 抽为共享模块（`noesis/chat/event_mapping/retrieval.py`），桥接层改为调用；行为零变化回归（主会话来源面板、`retrieval-results-available` SSE）
- [x] 1.2 `executor._child_projection_content` 接入共享解析：`web_search` / `web_fetch` / `search_knowledge_base` 工具结果生成 retrieval parts，工具 part 输出替换为「检索到 N 条来源」摘要
- [x] 1.3 回归测试：子会话落库内容含 retrieval parts 且与主会话同构；工具输出摘要化；无检索任务零变化；投影体积受既有预算约束
- [x] 1.4 前端：`SubagentConversationView` 把子会话 retrieval parts 作为 retrieval-results 传给既有渲染器，子会话来源面板（会话内 canonical URL 去重）展示

## 2. 来源身份与归一化

- [x] 2.1 canonical URL 归一化函数（去 tracking 参数、协议 / host 归一）后端实现 + 前端 `citationKey` 归一到同一规则；前后端规则测试对齐（共享用例集）
- [x] 2.2 retrieval part 结构与 `retrieval-results-available` 负载增加可选 `origin` 字段（`{kind: main|subagent, label}`，缺省 main）；前端 `messageParts.ts` 解析 origin，旧数据无字段按 main 归组

## 3. 跨边界传递与登记

- [x] 3.1 子会话去重来源清单提取（从子会话落库 retrieval parts，canonical URL 去重、有界上界）；终态通知负载增加结构化 `sources` 字段
- [x] 3.2 `check_task` 返回文本附有界来源清单段（受 `tool_output_max_chars` 约束）；无来源不附空占位
- [x] 3.3 主 run 桥接层：通知注入与 `check_task` 输出携带的来源清单登记为带 origin 标记的 retrieval parts，落在收取发生的 assistant 消息上；通知注入的用户消息落库内容不受污染（高风险区检查项）
- [x] 3.4 回归测试：通知 / check_task 两条通道的来源登记；多子 Agent 同源（同 canonical URL）在主会话合并为单条目带多 origin；跨弧不合并

## 4. 研究弧聚合展示（前端）

- [x] 4.1 研究弧边界计算：真实用户消息（排除 `source_kind = bg_task_notice`）切分弧；弧内过程消息不渲染来源面板、末条消息渲染聚合面板（含落位在过程消息上的 parts）
- [x] 4.2 聚合面板：canonical URL 去重、按贡献者分组（主 Agent 检索 / 子 Agent『任务标题』）、组可折叠、多 origin 徽标、计数为去重数
- [x] 4.3 引用分层：URL 归因（仅交付消息正文）→「引用 M · 共检索 N」；文件交付正文无来源 URL 时降级为仅「共检索 N」
- [x] 4.4 纯函数与隔离回归：多轮研究面板互不渗透（30/40 不合并）；刷新后面板与交付当时一致；被打断的弧以末条消息为聚合位、无 parts 不渲染
- [x] 4.5 前端全量回归（vitest）：弧聚合、分组去重、归因过滤、旧数据兼容（无 origin retrieval part 按 main 归组不报错）

## 5. 收尾验证

- [x] 5.1 后端全量 pytest + 前端 lint / build 通过
- [x] 5.2 高风险区检查：SSE `retrieval-results-available` 负载向后兼容（新增可选字段）；消息落库内容兼容（存量消息无 origin / 无子会话 retrieval parts 渲染不变）；通知注入的一次性与不落库语义保持
- [ ] 5.3 手动验收（真实深度研究会话）：子会话抽屉有来源面板；交付消息聚合面板分组正确、引用数小于检索数；同会话两轮研究面板隔离；刷新后面板稳定
