# Tasks：记忆质量与可审计性增强

## 1. Store 层：frontmatter 权威 + 索引投影

- [ ] 1.1 `store.py`：定义冻结 frontmatter 字段集常量（type/label/description/tags/created/updated/sources）与渲染函数；`_render_entry` 改为 frontmatter + 正文（保留 Why/适用条件散文节，移除来源/更新时间散文行）
- [ ] 1.2 `store.py`：`read_entry_file` 重写——YAML frontmatter 解析为主、原散文解析为 fallback（存量/手写条目容错，见 spec「frontmatter 损坏容错」场景）；返回值增加 tags/created/updated，description 无 frontmatter 时回退正文截断
- [ ] 1.3 `store.py`：`upsert_entry` 增加 `description`/`tags`/`created` 参数；更新路径合并来源、维护 updated、保留用户手工字段与既有 description（未提供新值时不清空）；frontmatter 与索引行在同一次调用中生成（`_sync_index_line` 用 frontmatter description）
- [ ] 1.4 `store.py`：`rebuild_index` 改为 frontmatter label/description 机械投影；`search` 与 `read_entry` 经新解析路径回归（返回原文不变）
- [ ] 1.5 `store.py`：`migrate_legacy_entries(user_id)`——幂等迁移存量条目（散文解析结果补写 frontmatter，created 取首个来源日期）；迁移状态写入 `.consolidation_state.json`
- [ ] 1.6 单测（`backend/tests/`）：frontmatter 渲染/解析往返、YAML 损坏容错、索引投影一致性、迁移幂等（重复执行跳过已迁移）、散文 fallback 行为

## 2. 抽取：路由防呆 + description 生成 + 决策落 journal

- [ ] 2.1 `extraction.py`：`ExtractedEntry` 增加 `description` 字段；`ExtractionResult` 增加 `excluded: list[ExcludedItem]`（gist + reason）
- [ ] 2.2 `_EXTRACTION_PROMPT`：description 两段式约束（「一句话结论；何时调用」）；时效性内容禁入稳定类型硬规则；灰色地带 few-shot 对照示例（decision vs preference、experience vs gotcha、goal vs decision）
- [ ] 2.3 `extraction.py:_apply`：journal 追加「抽取决策」块（新建/更新及理由、排除内容及理由）；description 传入 `upsert_entry`
- [ ] 2.4 sweeper 启动路径接入 `migrate_legacy_entries`（首次按用户执行一次）
- [ ] 2.5 抽取 fixture 扩展（`backend/tests/`）：时效性内容路由进 goal、description 含触发场景语义、排除理由落 journal、既有守卫用例回归

## 3. 整理：裁决优先级 + 操作前快照 + 治理信号

- [ ] 3.1 `consolidation.py`：`ConsolidateAction` 增加 `new_description`（空则保留原值，merge 以保留方为准）；`_CONSOLIDATION_PROMPT` 矛盾裁决三级优先级（用户显式修正 > 稳定类型 > 时间与证据）+ 动态内容不得静默改写稳定条目规则
- [ ] 3.2 `consolidation.py:_apply_action`：merge/rewrite/remove 执行前将改前条目全文（含 frontmatter）快照追加进当日 journal（块头标记「整理快照 · 原条目路径」）
- [ ] 3.3 `consolidation.py`：frontmatter type 与目录不一致的机械检测与归位（以所在目录为准修正 frontmatter type，不移动文件；journal 记录）；`_recent_journal` 排除快照块
- [ ] 3.4 整理 fixture 扩展：改写/淘汰前快照存在性、rewrite 后 description 同步（new_description 与保留语义）、动态内容不改写稳定条目、type 归位、裁决优先级

## 4. Agent 侧：提示词与工具对齐

- [ ] 4.1 `agents/prompts/memory.py`：Agent 主动写入条目的格式说明改为 frontmatter 模板；确认写入路径仍统一走 `upsert_entry`、经 HITL 确认（行为不变，仅格式说明更新）
- [ ] 4.2 稳定前缀回归：删除注入后 `RefreshingMemoryMiddleware`（USER.md + 索引）通道不受影响；Agent 经 `search_memory`/`read_file` 可召回条目原文

## 5. 召回模式切换（被动注入 → Agentic 检索）

- [ ] 5.1 删除 `services/memory/selection.py`、`agents/middlewares/memory_entries_middleware.py` 及全部挂载点（`super_agent.py` / `factory.py` / `agents/middlewares/stack.py` / `middlewares/__init__.py`）；同步删除对应单测；清理 MemoryConfig 中仅注入使用的项（`selection_model`、注入预算全量判定等）
- [ ] 5.2 `agents/tools/memory_tools.py`：`search_memory` 装配参数增加 `run_id` 与 db 句柄（root run 装配写入，subagent 只读不写），命中后合并写入 `run.memory_context['entries']`（读-合并-写，去重追加）；返回结果附条目年龄提示（沿用 `stale_warning_days` 阈值）
- [ ] 5.3 `agents/prompts/memory.py`：记忆使用指引增加召回纪律（涉及用户偏好/历史决策/既往经验/当前目标先检索再产出；索引每轮可见，按 description 决定读全文或直接用索引行信息）
- [ ] 5.4 抽取输入回归：`_load_injected` 读取工具召回聚合的 `run.memory_context`（语义不变）；防自强化用例回归（复述召回条目不记录、修正召回条目即更新）
- [ ] 5.5 召回行为评测：应召回场景断言（含记忆线索的对话 → Agent 调用 `search_memory`）加入 offline-evals/fixture；memory on/off paired 任务成功率口径沿用

## 6. 验证与收尾

- [ ] 6.1 `cd backend && uv run pytest tests/ -q` 全量回归（重点 `tests/` 记忆相关与 `api_contract/`）
- [ ] 6.2 手动验证：构造存量散文条目 → 启动后端触发迁移 → 确认 frontmatter 补写、索引投影一致、Agent 可经工具召回；结束后停掉 Agent 启动的服务进程
- [ ] 6.3 归档：`openspec` 主规格 `agent-memory-cortex/spec.md` 与本 delta 对齐（含：frontmatter 损坏容错、时效性禁入稳定类型、抽取决策落 journal、改写与淘汰前快照、动态内容不得静默改写稳定条目、frontmatter 类型与目录不一致归位、存量条目惰性迁移；旧注入 requirement 移除、新召回 requirement 落主规格）
