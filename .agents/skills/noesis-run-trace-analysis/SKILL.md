---
name: noesis-run-trace-analysis
description: >-
  Analyze one Noesis Agent run from local Postgres and backend logs, with optional
  Langfuse enrichment, to diagnose tool failures, permission errors, cancel or
  partial turns, duplicate web_search, context growth, subagent calls, tool counts,
  retrieval volume, provider context length, and token accounting. Use when the
  user shares a Noesis session id, asks to 分析轨迹 / 分析运行 / 看 trace, or
  provides a Langfuse trace for supplemental evidence.
---

# Noesis Run Trace Analysis

本地优先：**Postgres + backend logs = 默认事实来源**；**Langfuse = 可选的模型级观测补充**。
不要因为 Langfuse 不可达就放弃本地分析，也不要只看一条 trace 或只信 `status=success`。

Noesis 连接细节、API、陷阱见 [references/reference.md](references/reference.md)。

单轮指标脚本：

```bash
set -a; source backend/.env; set +a
backend/.venv/bin/python \
  .agents/skills/noesis-run-trace-analysis/scripts/analyze_run.py \
  --session-id <SESSION_ID>
```

省略 `--session-id` 会分析最近更新的 session。脚本默认自动扫描
`.noesis/logs/*.log`。需要机器可读结果时加 `--json`；日志不在默认目录时可
重复传入 `--log-path <LOG_FILE>`。如果已有 Langfuse trace JSON，再加
`--langfuse-json <TRACE_JSON>`，脚本才会补充每次 generation 的 provider usage。

## 何时启用

- 用户提供 Noesis `session_id` / `run_id`
- 用户要求统计一轮的 subagent、工具、检索、上下文长度或 token 用量
- 用户丢 Langfuse 链接 / `peek=` / `trace id`，需要补充模型级证据
- 要查：工具失败、路径权限、重复搜索、检索质量、中断、沙箱缺依赖（node/pip）

## 进度清单

```
- [ ] 0. 确定 session/run 范围；没有输入时先选最近更新的 session
- [ ] 1. 运行 `analyze_run.py` 获取本地数据库 + backend logs 统计
- [ ] 2. 对照 DB 消息、`t_agent_run` 终态和日志错误信号
- [ ] 3. 如果有 Langfuse，扩展到同 session 全量 traces，勿只盯单条 peek
- [ ] 4. 专扫 execute / 工具失败文本（不靠 status）
- [ ] 5. 评 web_search：query 设计、结果质量、提供方和重复来源
- [ ] 6. 区分精确 provider usage、数据库快照和字符粗估，输出根因分层
```

## Step 0 — 本地数据源

1. 默认读取本地 PostgreSQL：`t_chat_session`、`t_chat_message`、`t_agent_run`。
2. 默认扫描 `.noesis/logs/*.log`，只提取目标 session 的日志统计，不打印原始日志行。
3. 本地数据库连接失败时，先检查 Docker Postgres 或通过 `--dsn` 指定连接串。
4. 本地分析不要求 Langfuse 可用。

## Step 1 — 运行本地分析脚本

从用户提供的 session id 开始；没有 session id 时分析最近更新的 session：

```bash
set -a; source backend/.env; set +a
backend/.venv/bin/python \
  .agents/skills/noesis-run-trace-analysis/scripts/analyze_run.py \
  --session-id <SESSION_ID>
```

重点查看：工具和 `task` 次数、part 状态、retrieval 来源量、工具输出体量、
`session.extra.context`、`t_agent_run` 终态，以及日志中的超时、取消、输出超限和上报失败信号。

## Step 2 — Langfuse 可选增强

Langfuse 只在服务已部署并且凭据/地址可达时使用。它主要补充：

- 每次 generation 的 provider `input_tokens` / `output_tokens`
- LLM observation 的开始/结束时间和父子关系
- 模型调用级别的完整输入输出和错误信息

如果用户提供 URL，从 URL 提取：`projectId`、`traceId`（`peek=` 或 `/traces/<id>`）和
`timestamp`，再用 trace 的 `sessionId` 拉同 session 全部 traces。

Langfuse UI/API 不可达时，保留本地 DB + 日志结论，不把连接失败误判成 Noesis 运行失败。

## Step 3 — Langfuse 取证

对目标 + 相邻 traces：

- observations：`TOOL` / `GENERATION` / `CHAIN`；`level=ERROR`；`statusMessage`
- 保留：工具名、input、output、start/end、parent 关系
- 注意：root `output=null` + cancel scope → 多半客户端中断 / middleware cancel

可选：下载 JSON 后跑扫描脚本（相对 Noesis 仓库根）：

```bash
python3 .agents/skills/noesis-run-trace-analysis/scripts/scan_trace.py /tmp/lf_trace.json
```

若用户关心一轮的模型调用次数和上下文长度，优先使用：

```bash
backend/.venv/bin/python \
  .agents/skills/noesis-run-trace-analysis/scripts/analyze_run.py \
  --session-id <SESSION_ID> \
  --langfuse-json /tmp/lf_trace.json
```

## Step 4 — DB 与日志对照

本地运行优先查当前工作区 Postgres；生产环境再通过受控连接查远程 Noesis Postgres。

按 `session_id` 拉全量消息，对齐时间戳与 user 文案。重点：

| DB 字段 | 用途 |
|---------|------|
| `status=partial` | 用户中断 / 流未完成（常与 Langfuse cancel 对应） |
| `content.parts` | reasoning / text / tool（含 input、output、duration_ms） |
| `session.extra.context` | token 占用（爆上下文线索） |

单轮指标口径：

| 指标 | 数据源 | 解释 |
|------|--------|------|
| 工具调用次数 | `t_chat_message.content.parts[]` 中 `type=tool` | 已落库的工具 part 数，不等于 Langfuse observation 数 |
| subagent 调用次数 | 工具 part 中 `name=task` | 包含重试/取消的 task tool 调用，必须同时看状态 |
| 工具结果长度 | tool part 的 `input/output` 字符数 | 只表示落库体量，不是 token 用量 |
| retrieval 数量 | `type=retrieval` 及其 `results[]` | 可能包含跨 query 重复来源，不能直接当独立来源数 |
| 当前上下文长度 | `session.extra.context.current_tokens` | 最后一次已落库的主 Agent provider input snapshot；可能早于 run 终态，不是整轮累计，也不覆盖 subagent |
| 每次精确上下文/token | Langfuse `GENERATION.usage` | 只有存在 provider usage 时才可作为精确 accounting |
| 整轮终态 | `t_agent_run.status/error_code` | 优先于单个 tool part 的 status 判断整轮是否失败 |

没有 Langfuse usage 时，脚本仍可输出 DB 指标，但必须明确说明：字符除以 4
只是粗略估算，不能替代 provider 返回的 `input_tokens`/`output_tokens`。

## Step 5 — 失败扫描（强制）

**禁止**仅用 `parts[].status==success` 或 Langfuse `level!=ERROR` 下结论。

对每个 `execute` / shell 类工具，扫描 output 文本：

- `exit code` ≠ 0、`Command failed`
- `No such file or directory`、`command not found`
- `Permission denied`、`Read-only`、`EACCES`
- `ModuleNotFoundError`、`Cannot find module`、`ENOENT`
- `pip` / PEP 668 / `--break-system-packages` / `/.local`
- `node` / `npm` / `nodejs` 缺失

沙箱常见根因：skill 假设有 Node，镜像只有 Python；`cd … &&` 被错误包装；pip 无写家目录。

## Step 6 — 检索质量

对每条 `web_search`：

1. **Query 设计**：是否关键词堆叠、多意图塞一条、缺 `site:` / 官方源约束  
2. **提供方**：`provider` / `ddg_backends`（弱后端会系统性偏题）  
3. **结果**：标题/URL/snippet 是否离题、过时、跨 query 重复噪声  
4. 区分：**搜索词问题** vs **后端/索引问题** vs **Agent 未筛选就采用**

重复搜索：完全相同 query 计数；近义反复搜且无收敛也要记。

## Step 7 — 输出模板

用简体中文；先给结论。

```markdown
## 结论
（1–3 句：主根因）

## 范围
- Langfuse：project / trace(s) / session
- DB：session / 相关 message id / status

## 时间线
| 时间 | 轮次 | 关键动作 | 结果 |

## 问题清单
| 严重度 | 类型 | 证据（LF obs / DB part） | 根因分层 |

类型建议：`cancel` | `tool_fail` | `permission` | `env_mismatch` | `search_query` | `search_provider` | `dup_search` | `context_blowup`

## 双源差异
- Langfuse 独有：…
- DB 独有：…
- 两边一致：…

## 建议
（可执行的下一步，不写空话）
```

## 硬性规则

1. 用户给单条 peek 时，**必须**扩展到同 session 邻轮  
2. `execute` 失败以 **output 文本** 为准，不以 DB `status` 为准  
3. Langfuse 不可达时，仍完成本地 DB + 日志分析  
4. 密钥只读服务器 env，**禁止**写入仓库 / Memory / skill 文件  
5. 默认不改生产配置；改 PK/SK 需用户明确要求
