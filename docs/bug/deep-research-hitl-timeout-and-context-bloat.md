# Deep Research：HITL 恢复、运行时限与上下文膨胀

> 状态：🆕 新增  
> 发现日期：2026-08-04  
> 环境：本机 Noesis，`SUPER_AGENT_QA`  
> Session：`1c6eea66-60b1-4cf5-a7bb-21628e4e67e5`  
> Run：`9181df89-e4b7-47cf-9bef-bacec0390493`  
> Assistant message：`5280c912-8f2e-4ee8-b2ed-1c0032f1c513`

## 结论

本轮 `/deep-research-v2` 没有生成最终报告，直接原因是 Run 从首次启动开始连续计算 900 秒，HITL 等待约 237 秒也占用运行时限。Run 在写入 Phase 5 后、准备生成 Phase 6 最终报告时触发 `RUN_TIMEOUT`。

HITL 恢复还使首批 3 个并行 `task` 以新的 `tool_call_id` 再次出现；子 Agent 全量事件进入父消息，进一步增加执行时间、消息体积和模型上下文。最终 assistant 单行达到 2,591,068 字节，上下文达到 89,815 tokens。

## 用户影响

- 用户等待 15 分钟后只得到 `partial` 消息，没有 `reports/final-report.md`。
- 页面展示了大量阶段进度，但缺少正式交付物。
- HITL 批准后可能重复执行已经开始的并行子任务、搜索和外部读取。
- 历史消息加载、落库检查点和后续模型调用需要处理约 2.59 MB 的单条 assistant 内容。
- Agent 声称 Phase 3 完成，但 `excluded-sources.json` 实际不存在。

## 界面截图对应关系

| 用户看到的现象 | 原文档覆盖情况 | 归属 |
|----------------|----------------|------|
| Run 结束后仍显示“正在继续生成” | **未覆盖，现补充** | Bug 7：页面生成态未随权威 Run 终态结束 |
| 部分 SubAgent 显示“执行失败” | **部分覆盖** | Bug 2：HITL 后父 task 被停止/重建；Bug 9：UI 只显示笼统失败 |
| 成功 SubAgent 结果展示 `Command(update=...)` | **未覆盖，现补充** | Bug 8：task 返回值未做业务解析，结果区域使用纯文本 `<pre>` |
| SubAgent、思考块、web_fetch 卡在“运行中/正在执行” | **未覆盖，现补充** | Bug 9：父子 part 终态没有完整归并到 UI |
| SubAgent 内出现“本次工具执行已停止” | **部分覆盖** | Bug 2；该文案来自 HITL/终态 reconcile，不一定代表对应工具真实失败 |

截图还表明父 task 与子 part 存在互相矛盾的状态：父 task 可显示“失败”，内部已完成的搜索仍显示“已完成”；另一父 task 显示“进行中”，内部较早的 reasoning 和 fetch 长期保留流式状态。这不只是展示文案问题，说明持久化 snapshot 或前端归并没有得到一致终态。

## 运行时间线

| 时间 | 事件 | 结果 |
|------|------|------|
| 15:35:19.256 | Run 注册并开始执行 | 启动固定 900 秒 watchdog |
| 15:37:10.839 | 子 Agent 内只读 `execute` 请求审批 | Run 进入 `hitl_pending` |
| 15:41:07.544 | 用户批准 HITL | 等待约 237 秒后恢复 |
| 15:48:13 | 写入 `raw-sources.json` | Phase 2 完成 |
| 15:48:38 | 写入 `filtered-sources.json` | Phase 3 部分完成 |
| 15:49:16 | 写入 `analysis/insights.md` | Phase 4 完成 |
| 15:49:42 | 写入 `analysis/validation-matrix.md` | Phase 5 完成；context 89,815 / 128,000 |
| 15:50:19.314 | watchdog 触发 | `RUN_TIMEOUT`，Run 收口为 `partial` |

DB 终态：

```text
status=partial
finish_reason=limit_exceeded
error_code=RUN_TIMEOUT
user_error_message=本轮生成时间过长，已停止
started_at=2026-08-04 15:35:19.256
finished_at=2026-08-04 15:50:19.324
```

## 🆕 Bug 1：HITL 等待占用普通 Run 时长

### 现象

Run 在 `hitl_pending` 等待用户约 237 秒，这段时间仍计入 `run_max_duration_seconds=900`。恢复后只剩约 9 分钟继续执行。

### 代码证据

`backend/noesis_server/domain/chat/runs/manager.py`：

- `start()` 创建 `_expire_running_run()` watchdog。
- `_expire_running_run()` 固定 `sleep(max_run_duration_seconds)`。
- `transition(..., HITL_PENDING)` 只创建独立的 HITL timeout，没有暂停普通 watchdog。
- `resume()` 恢复 producer，但没有按剩余 active execution time 重建 watchdog。

这与系统同时提供 `run_hitl_pending_timeout_seconds=86400` 的设计冲突：HITL 理论上可等待一天，实际上普通 900 秒 watchdog 会先终止 Run。

### 预期

- 普通 Run 时长只统计 `running/retrying` 的有效执行时间。
- `hitl_pending` 使用独立 HITL timeout，不消耗普通 Run 时长。
- resume 后沿用恢复前剩余的有效执行预算。

### 回归验收

构造短时限测试：运行 100 ms → HITL pending 超过普通时限 → resume。审批等待期间不得产生 `RUN_TIMEOUT`；恢复后仅在剩余 active budget 用尽时超时。

## 🆕 Bug 2：并行子 Agent 遇到 HITL 后重新创建父 task

### 现象

首次并行创建的 3 个父 task：

```text
019fcbb4-2b5d-7a31-8874-5acf32167d3d  error  本次工具执行已停止
019fcbb4-2b5e-7212-9761-86e1a026a0fe  error  本次工具执行已停止
019fcbb4-2b5e-7212-9761-86fafef6ba52  error  本轮生成时间过长，已停止
```

HITL resume 后又出现 3 个新的父 task call：

```text
019fcbb8-190b-7a93-bf70-ade14593a307  success
019fcbb8-190b-7a93-bf70-adf2d440be2e  error
019fcbb8-190b-7a93-bf70-ae0e5a033d27  success
```

中文子任务失败后又创建第三个 task：

```text
019fcbbb-d95d-7072-98d2-617c4d2fc19a  error  duration_ms=109281
```

整轮共持久化 7 个 `task` part，只有 2 个 success、5 个 error。恢复后的父 task 使用新 `tool_call_id`，不符合“同一逻辑 Run / 同一工具身份继续”的预期。

### 风险

- 已启动子任务可能重复执行 web search、外部 API 请求和文件写入。
- 并行 task 的部分结果被错误显示为停止，随后又出现新的 task 卡片。
- HITL 从单个子工具暂停扩大为整个并行批次重新运行。

### 预期

- 子 Agent 内工具进入 HITL 时，父 `task` 保持原 `tool_call_id` 和 `approval_pending`。
- resume 继续原 checkpoint，不重新创建兄弟 task。
- 已完成的并行 task 不得重新执行；未完成 task 应从原状态继续或明确收口。

### 回归验收

同时启动 3 个 task，其中一个 task 内触发 execute HITL。批准后断言：

1. 父 task 仍为原 3 个 `tool_call_id`；
2. 已完成 task 的调用次数仍为 1；
3. pending task 从原 checkpoint 继续；
4. 终态没有重复父 task part。

## 🆕 Bug 3：子 Agent 全量轨迹进入父消息，导致消息和上下文膨胀

### 数据证据

```text
assistant content        2,591,068 bytes
parts                    271
tool parts               112
retrieval parts           67
reasoning parts           52
text parts                40
session context       89,815 / 128,000 tokens
```

工具分布：

| 工具 | 次数 | 累计 part 大小 |
|------|-----:|---------------:|
| `web_fetch` | 44 | 约 521 KB |
| `web_search` | 27 | 约 509 KB |
| `task` | 7 | 约 18 KB；成功结果正文约 230 KB、157 KB |
| `execute` | 10 | 约 21 KB |

子 Agent 的 reasoning、text、tool、retrieval 全部按 `parent_task_call_id` 写入父 assistant。同一批搜索内容还可能同时出现在：

- assistant tool/retrieval parts；
- `workspace/summary_offload/web_search-*.txt`；
- 子 Agent 落盘研究文件；
- 父 Agent 汇总后的 sources/analysis 文件。

### 现有约定冲突

`docs/NOTES.md` 已记录：子 Agent 小结默认不超过 400 字，长文落盘。当前运行仍把完整子轨迹和大段 task 返回内容写入父消息，约定没有形成运行时约束。

### 预期

- 子 Agent 对父 Agent 只返回短结构化摘要、artifact 路径和失败摘要。
- 大型搜索/抓取结果只保留一个权威 artifact，不在父模型上下文重复展开。
- UI 可保留子任务状态，但历史消息不应持久化所有内部 reasoning 和完整网页正文。
- 对单个 task 返回、单条工具输出和 assistant snapshot 设置字节/token 上限。

## 🆕 Bug 4：Deep Research 缺少预算感知和“先保证报告”策略

### 现象

`deep-research-v2` 默认 `depth=deep`，强制执行多个阶段，并使用固定来源指标：

- 最少 20 个来源；
- 至少 15 篇论文；
- 至少 5 个竞品；
- 通用模板还包含政策、专利等类别。

Skill 没有读取 Run 剩余时间、上下文余量或工具调用次数，也没有规定接近上限时停止检索并生成报告。本轮直到最后约 37 秒才进入 Phase 6，最终没有写出报告。

### 预期

- 研究开始时分配阶段预算，预留报告生成时间和 token。
- 达到时间、token、搜索次数或来源数量阈值后停止扩展检索。
- 先创建可持续更新的 `final-report.md` 骨架，再逐阶段补充。
- 不适用的政策、专利、竞品要求允许跳过并说明原因。
- timeout 前至少交付基于已有证据的 partial report，而不是只留下进度文案。

## 🆕 Bug 5：来源去重和失败停止条件不足

### 现象

- 27 个 web search query 文本不同，但结果高度重复。
- Karpathy gist 在搜索结果中出现 11 次，同一 YouTube 视频出现 10 次。
- Karpathy gist 实际 fetch 3 次，`llm-wiki.net` fetch 2 次，同一知乎 URL fetch 2 次。
- 知乎已明确返回 403/抓取失败后，后续子任务仍再次尝试。
- GitHub API 已返回 unauthenticated rate limit，Agent 仍继续围绕相同数据源切换调用方式。

### 预期

- 父 Run 维护跨主 Agent/子 Agent共享的 canonical URL registry。
- 同一 URL 成功抓取后默认不重复抓取；失败达到阈值后记录为 unavailable。
- query 应按新增唯一来源数评估收益，连续低收益时停止该检索分支。
- provider fallback 和 HTTP 状态应进入结构化失败记录，供其它子 Agent避开相同失败路径。

## 🆕 Bug 6：阶段完成状态没有校验实际产物

### 现象

Agent 输出“Phase 3 完成”，但 Skill 规定的以下文件缺失：

```text
research/domains/llm-applications/llm-wiki/sources/excluded-sources.json
research/domains/llm-applications/llm-wiki/reports/final-report.md
```

其中 final report 是因为 timeout，`excluded-sources.json` 则在宣告 Phase 3 完成时就没有生成。

### 预期

- 阶段完成前按 manifest 检查必需文件存在、非空且格式有效。
- Todo 状态由产物校验结果驱动，不能只依据模型自述。
- Run partial/error 时，最终状态应列出已完成产物、缺失产物和可继续位置。

## 🆕 Bug 7：Run 已终态，页面仍卡在“正在继续生成”

### 现象

DB 中 Run 已是：

```text
status=partial
finish_reason=limit_exceeded
error_code=RUN_TIMEOUT
```

界面底部仍显示“正在继续生成”，没有结束 loading，也没有稳定展示本轮 timeout/partial 结果。

### 代码线索

`frontend/src/views/chat.vue` 中：

```ts
function showAssistantReplyLoading(index: number, role: string): boolean {
  return role === 'assistant' && isLastAssistantMessage(index) && stylizingLoading.value
}
```

该提示只依赖页面级 `stylizingLoading`，没有直接验证当前消息对应的权威 Run 状态。只要 timeout/partial 分支没有可靠清除此 ref，最后一条 assistant 就会持续显示“正在继续生成”。

### 预期

- `completed | partial | error | interrupted` 任一权威终态都必须关闭当前 session/run 的 loading。
- loading 应绑定 `session_id + run_id`，不能仅由全局布尔值控制最后一条 assistant。
- timeout 后显示明确的“本轮生成时间过长，已停止”和“重新执行/继续”入口。
- 重新进入会话时，以 run snapshot 为准，不得从残留的本地 loading 状态恢复“正在继续生成”。

### 回归验收

触发 `RUN_TIMEOUT`，断言收到终态或重新加载历史后：

1. `AssistantStreamingIndicator` 不存在；
2. 输入区恢复可用；
3. 页面显示 partial/timeout 提示；
4. 切换会话再返回也不会重新显示生成中。

## 🆕 Bug 8：成功 SubAgent 结果展示内部对象字符串，而非 Markdown 正文

### 现象

成功的学术子任务结果区域直接显示：

```text
Command(update={'files': {}, 'messages': [ToolMessage(content='数据收集完毕。以下为最终结构化小结...')...]})
```

用户真正关心的是 `ToolMessage.content` 内的 Markdown 小结；`Command`、`update`、`files`、`ToolMessage` 属于内部实现结构，不应作为产品内容展示。

### 代码证据

`frontend/src/utils/parseTaskTool.ts` 目前只识别：

```text
Task Succeeded. Result:
```

否则把完整 output 原样作为 result。`frontend/src/components/SubagentCollapse/index.vue` 又使用：

```vue
<pre>{{ resultDisplay }}</pre>
```

因此后端返回 `Command(update=...)` 时，前端既没有提取业务正文，也不会渲染 Markdown。

### 产品行为

成功的 SubAgent 结果应该渲染成 Markdown，但前提是先得到结构化的业务结果：

1. 最优方案：后端 `task` 工具输出稳定 JSON/typed payload，例如 `result_markdown`、`artifacts`、`summary`。
2. 前端读取 `result_markdown`，交给现有 `MarkdownPreview` 渲染。
3. `Command(...)` 原始值仅在开发调试信息中可见，不进入默认结果区域。
4. 不建议前端长期用正则解析 Python `repr`；它不稳定，也无法可靠处理转义和嵌套结构。

### 回归验收

- task 返回 Markdown 标题、列表、链接和代码块时，结果区域按 Markdown 正常展示。
- 页面不出现 `Command(update=...)`、`ToolMessage(...)` 等内部结构。
- 超长结果按现有安全截断策略处理，并提供 artifact 链接或展开入口。
- Markdown 使用现有安全渲染链路，不直接注入未清洗 HTML。

## 🆕 Bug 9：SubAgent 父子块终态不一致，遗留“运行中/正在执行/思考中”

### 现象

截图中同一个 SubAgent 已经历约 109 秒，内部后续步骤已经完成或父 Run 已 timeout，但仍可见：

- 父 task：“进行中”；
- 较早 reasoning：“思考中… / 运行中”；
- 两个 `web_fetch`：“正在执行”；
- 后续 `web_fetch` 已显示“已完成”。

这说明 part 不是简单按时间顺序自然结束：早期 part 没有收到或没有应用终态，而后续 part 已经落库。

### 代码线索

- `ReasoningBlock` 完全依据 `child.status === 'streaming'` 显示“思考中/运行中”。
- `parseTaskToolOutput()` 只要父 part 的 `state/status` 是 running，就显示 SubAgent“进行中”。
- 前端已有 `finalizeStreamingParts()`，能把顶层 streaming reasoning/text 和非终态 tool 收口；需要确认它在 `partial/RUN_TIMEOUT`、HITL resume、历史 snapshot 覆盖时是否对嵌套 `parent_task_call_id` parts 全量执行。
- 后端 `reconcile_nonterminal_tools()` 处理 tool state，但 reasoning/text 的完成边界仍依赖事件完整到达；HITL/timeout 可能打断对应 end event。

### 与“本次工具执行已停止”的关系

后端在 HITL、RunCompleted、RunAborted 和异常终态会对非终态工具执行 reconcile，并写入“本次工具执行已停止”。本轮 HITL 只应保留待审批 action 及其父 task，但其它正在并行执行的 task/子工具被统一取消，造成：

- 实际已产生部分结果的 SubAgent 显示失败；
- 未收到终态的 child part 仍显示运行中；
- 被 reconcile 的 part 显示“本次工具执行已停止”，但没有说明是 HITL 暂停、兄弟任务取消还是 Run timeout。

### 预期

- 父 task 的展示状态应由自身终态和全部 child part 自底向上计算。
- 父 task 进入 terminal 后，所有 child reasoning/text/tool 必须是 terminal UI 状态。
- 已有输出但缺少 end event 的 reasoning 在父 task 终态时应显示“思考过程/完成”，不能显示“思考中”。
- timeout 对应 `timed_out`；HITL 取消的兄弟任务对应 `cancelled`；真实工具异常对应 `failed`，三者不能统一显示“执行失败”。
- “本次工具执行已停止”应附带机器原因，前端转换成可理解的具体状态。

### 回归验收

覆盖以下组合：

1. 并行 task + 子工具 HITL + approve；
2. 并行 task + Run timeout；
3. 子工具 network error，但子 Agent仍能产出降级结果；
4. 页面刷新后从 DB snapshot 重建。

每个场景都断言：父 task 和 child parts 没有遗留 `running/streaming/approval_pending`，并且失败、停止、超时语义与真实原因一致。

## 已有产物

```text
research-plan.md
sources/raw-sources.json
sources/filtered-sources.json
sources/english-web-research.md
analysis/insights.md
analysis/validation-matrix.md
```

缺失：

```text
sources/excluded-sources.json
reports/final-report.md
```

## 稳定反馈样本

DB 查询：

```sql
SELECT id, status, length(content::text), content
FROM t_chat_message
WHERE id = '5280c912-8f2e-4ee8-b2ed-1c0032f1c513';

SELECT id, status, finish_reason, error_code,
       started_at, finished_at, snapshot
FROM t_agent_run
WHERE id = '9181df89-e4b7-47cf-9bef-bacec0390493';
```

原始日志：

```bash
rg -n '1c6eea66-60b1-4cf5-a7bb-21628e4e67e5' \
  .data/logs/2026-08-04_error.log
```

修复后的端到端验收应重跑相同主题，并至少满足：

1. HITL 等待不占普通 Run 时长；
2. resume 后父 task 身份不变且无重复执行；
3. assistant 消息与上下文受预算限制；
4. timeout 前生成可读报告；
5. 阶段完成状态与实际文件一致。

## 范围说明

- 本文件只记录本机 LLM Wiki 研究 Run 暴露的问题。
- 远程服务器磁盘耗尽和 PostgreSQL recovery 是独立事故，不属于本 Bug 样本。
- 本轮只完成诊断和记录，尚未修改实现。
