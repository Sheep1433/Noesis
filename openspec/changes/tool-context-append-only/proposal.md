# Proposal: 工具上下文 append-only 投影与预算收敛

## Why

四个实测问题指向同一结构性缺陷——**上下文投影会改写已发送给模型的历史**，以及**工具输出上限链路自相矛盾**：

1. **前缀缓存每步出血**：预算中间件对 assistant 大工具参数设了「最近 12 条保留原文」的滑动窗口，窗口边界随对话前移，每步原地改写一条已发送消息。实测一轮 18 步子 Agent run 中 step 16→17 输入不增反降 5,355 token，多步综合缓存命中率 67.7%（同形状 append-only 历史的结构值为 91.8%）。
2. **read_file 无源头上限形成卸载追逐循环**：Noesis 关闭 deepagents 的结果驱逐（预算中间件单点拥有替换语义）时，连带关闭了 read_file 的上游截断开关——两者共用一个参数。500 行读取约 30k 字符，必超 24k 单条预算被事后卸载成 600 字符梗概 + artifact 路径，模型转而读 artifact 又超限再卸载。近 7 天 DB 实测：read_file 超限 40 次，最大 10 条超限输出全部是读取 `/large_tool_results/` 下 artifact 的结果。
3. **批次合计层重复设卡**：并行批次合计 48k 预算把「每条都没超单条预算」的中间大小结果强制替换——8-9 条并行搜索合计 50-90k 即触发，砍掉的是并行检索的可用来源；上下文总量护栏本是压缩层职责。
4. **Runtime Context 每轮中途注入且不进历史**：时间戳块每轮插在最后一条用户消息前、位置与内容双变，上一轮全部新增在新一轮第一次请求再 miss 一遍；web_fetch 正文 4k 上限对深度研究过薄，且输出 JSON 把正文存两份。

## What Changes

- **预算中间件投影 append-only 化**：删除参数卸载的滑动窗口（大参数进入有效历史时一次定型）；删除批次合计层（只保留单条 24k 预算）；替换梗概从 600 字符提为头 2000 + 尾 1000；新增投影幂等契约（同一历史两次投影逐字段一致，含已替换文本不被二次替换的前缀哨兵）。
- **read_file 源头封顶**：新增 `runtime.read_file_max_chars`（默认 20,000），在 stack 装配层包装 read_file 工具（主/子 Agent 共用），超限截断 + 「用 offset 续读」提示；新鲜读取永不触发预算中间件的事后替换。
- **Runtime Context 冻结块化**：会话首轮把日期粒度（`Today's date is YYYY-MM-DD`）+ workspace 冻结成头部块（messages[0] 位置，存 private state、之后逐字节不变）；跨日不改写冻结块，新一轮在消息尾部追加纠正声明；附件集合变化时尾部声明；删除中途注入路径（`insert_late_context` 退役）。
- **web_fetch 单份正文与头尾截断**：删除输出 JSON 的顶层 `content` 双份存储（消费方只读 `results`）；`fetch_max_chars` 4096 → 16000。超限页面从头部硬截断改为头 75% + 尾 25%（markdown 行边界对齐），全文落盘 agent backend（`/web_pages/`），页脚给出保存路径与精确续读 offset——尾部结论/参考列表不再丢失，模型一次 read_file 即落进省略段。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`：Context Management 分层策略中的 tool-result replacement 行为收敛（单条预算、入口定型、投影幂等）；新增 Runtime Context 冻结块与 read_file 源头上限的行为要求。

## Impact

- 代码：`agents/middlewares/tool_result_budget_middleware.py`（主体）、`agents/middlewares/dynamic_context_middleware.py`（重写）、`agents/tools/read_file_bound.py`（新增）、`agents/tools/web_search_tool.py`、`agents/middlewares/stack.py`、`factory.py`、`late_context.py`（删除）、配置链（yaml_config / env / 五个 yaml）。
- 兼容性：checkpoint 中已有的 `_tool_result_replacements` 记录按 hash 重放，升级后首次请求对既有大参数做一次入口定型（一次性缓存重置）；展示层零变化（预算替换一直只作用于模型侧投影）。
- 预期效果：多步 run 缓存命中率向结构值收敛（18 步约 91.8%）；多轮问答消除每轮的注入位置分叉；read_file 卸载追逐循环机制性消失。
