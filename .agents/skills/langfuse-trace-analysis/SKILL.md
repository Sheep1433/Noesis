---
name: langfuse-trace-analysis
description: >-
  Analyze Noesis Agent Langfuse traces together with remote chat DB to diagnose
  tool failures, permission errors, cancel/partial turns, duplicate or low-quality
  web_search, and environment mismatches (node/pip). Use when the user shares a
  Langfuse URL/trace id, asks to 分析轨迹 / 分析 Langfuse / 看 trace, or wants to
  locate Agent runtime issues across observability and persisted messages.
---

# Langfuse Trace Analysis（Noesis）

双源分析：**Langfuse = 执行取证**；**远程 DB = 产品落库结果**。  
不要只看一条 trace 或只信 `status=success`。

Noesis 连接细节、API、陷阱见 [reference.md](reference.md)。

## 何时启用

- 用户丢 Langfuse 链接 / `peek=` / `trace id` / `sessionId`
- 要查：工具失败、路径权限、重复搜索、检索质量、中断、沙箱缺依赖（node/pip）

## 进度清单

```
- [ ] 0. 可达性（隧道 / 项目 / 密钥）
- [ ] 1. 解析输入 → session 全量 traces（勿只盯 peek）
- [ ] 2. 拉 Langfuse trace 详情 + observations
- [ ] 3. 拉 DB 同 session 消息（尤其 status=partial）
- [ ] 4. 专扫 execute / 工具失败文本（不靠 status）
- [ ] 5. 评 web_search：query 设计 + 结果质量 + 提供方
- [ ] 6. 对照输出报告（根因分层）
```

## Step 0 — 可达性

1. Langfuse UI 若公网 8888 不通：本地隧道  
   `ssh -fN -L 8888:127.0.0.1:8888 zzqroot`  
   打开 `http://127.0.0.1:8888`
2. 确认项目：生产应对 **Noesis-prod**；历史误配密钥可能在 **Noesis-local-dev**
3. API 用 backend 容器内当前 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`（Basic Auth）

## Step 1 — 解析输入，拉全 session

从 URL 提取：`projectId`、`traceId`（`peek=` 或 `/traces/<id>`）、`timestamp`。

用该 trace 的 `sessionId` **列出同 session 全部 traces**（前后轮常含真正的 pip/node 失败）。

## Step 2 — Langfuse 取证

对目标 + 相邻 traces：

- observations：`TOOL` / `GENERATION` / `CHAIN`；`level=ERROR`；`statusMessage`
- 保留：工具名、input、output、start/end、parent 关系
- 注意：root `output=null` + cancel scope → 多半客户端中断 / middleware cancel

可选：下载 JSON 后跑扫描脚本（相对 Noesis 仓库根）：

```bash
python3 .agents/skills/langfuse-trace-analysis/scripts/scan_trace.py /tmp/lf_trace.json
```

## Step 3 — DB 对照

在 zzqroot Noesis Postgres：`t_chat_session` / `t_chat_message`。

按 `session_id` 拉全量消息，对齐时间戳与 user 文案。重点：

| DB 字段 | 用途 |
|---------|------|
| `status=partial` | 用户中断 / 流未完成（常与 Langfuse cancel 对应） |
| `content.parts` | reasoning / text / tool（含 input、output、duration_ms） |
| `session.extra.context` | token 占用（爆上下文线索） |

## Step 4 — 失败扫描（强制）

**禁止**仅用 `parts[].status==success` 或 Langfuse `level!=ERROR` 下结论。

对每个 `execute` / shell 类工具，扫描 output 文本：

- `exit code` ≠ 0、`Command failed`
- `No such file or directory`、`command not found`
- `Permission denied`、`Read-only`、`EACCES`
- `ModuleNotFoundError`、`Cannot find module`、`ENOENT`
- `pip` / PEP 668 / `--break-system-packages` / `/.local`
- `node` / `npm` / `nodejs` 缺失

沙箱常见根因：skill 假设有 Node，镜像只有 Python；`cd … &&` 被错误包装；pip 无写家目录。

## Step 5 — 检索质量

对每条 `web_search`：

1. **Query 设计**：是否关键词堆叠、多意图塞一条、缺 `site:` / 官方源约束  
2. **提供方**：`provider` / `ddg_backends`（弱后端会系统性偏题）  
3. **结果**：标题/URL/snippet 是否离题、过时、跨 query 重复噪声  
4. 区分：**搜索词问题** vs **后端/索引问题** vs **Agent 未筛选就采用**

重复搜索：完全相同 query 计数；近义反复搜且无收敛也要记。

## Step 6 — 输出模板

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
3. 密钥只读服务器 env，**禁止**写入仓库 / Memory / skill 文件  
4. 默认不改生产配置；改 PK/SK 需用户明确要求
