# 2026-09-03 接口测试执行记录（待后续 AI 分析）

**状态**：✅ 已审查修复（2026-09-03 开发轮：问题 1/2/5/6/7 已修复，问题 3/4 判非接口缺陷；逐项裁决见各节「裁决」）
**日期**：2026-09-03
**执行方**：ZCode（测试角色，只记录不修复）；审查修复：ZCode（开发角色）

## 环境与范围

- 后端 `uvicorn app:app` @ 8089（用户启动，加载**工作区未提交代码**：unify-run-delivery 42/42 + 本轮测试基建改动）；前端 vite preview @ 4173；sandbox-runner @ 8090。
- 测试账号：`test`（demo 账号；用户确认该账号即测试专用，测试数据落其名下属预期）。
- **用模（重要更正）**：test 账号虽在设置页配置了默认模型 `token/glm-5.3-flash`（自定义 Provider「基元律动」），但 **run 的模型解析链并不消费该偏好**——`_resolve_model_for_query`（`noesis/services/qa/helpers.py:198-247`）的取值顺序为「请求显式 model_id → 会话 extra.model_id → 平台默认目录项」，`user_llm_preferences` 无任何消费者。DB `model_calls` 证据：13:14 的 assistant 消息实际调用模型为 **`kilo-auto/free`**（config.yaml 平台默认，api.kilo.ai 网关）。因此本轮 LLM 用例仍受 kilo 免费池限流/稳定性影响，「网关因素已排除」的说法**不成立**。
- 执行命令与顺序（与 [AGENTS.md](../../AGENTS.md)「接口测试执行步骤」一致）：
  1. `uv run pytest tests/api_contract -q` → **21 passed**
  2. `uv run pytest tests/api -m 'integration and not llm' -q` → **39 passed**（42s）
  3. `uv run pytest tests/api -m integration -q` → **61 passed / 9 failed / 1 skipped**（24:11）

## 结果汇总

| 层 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| api_contract | 21 | 0 | 0 |
| tests/api 快速轮（非 LLM） | 39 | 0 | 0 |
| tests/api 全量（含 LLM） | 61 | 9 | 1 |

9 条失败全部集中在 6 个真实 LLM 用例文件，未出现任何 401/鉴权/包络形状类失败。

**重复执行**：全量轮因误操作实际并发跑了两份（12:38 与 12:44 启动，各自完整结束，32:52 / 24:11）。两轮失败集合一致（后者多一条 `test_active_run_endpoint_lifecycle`，计 10 failed / 59 passed / 2 skipped）。失败**可稳定复现**、非偶发；但两轮并发共享同一后端，负载互相叠加，分析时知晓即可。

## 问题清单（按证据强度排序）

### 问题 1（最关键）：run 已在 DB 终态，`GET /api/chat/runs/{id}` 长时间返回陈旧非终态

> **裁决**：✅ 已修复（双根因，均实证）。
>
> **根因 A（陈旧快照）**：终态事件（RunCompleted/RunError）只应用在 projection 的 **clone** 上（pending_terminal 流程），`handle.state` 在 commit 时换绑，但 `handle.snapshot_provider` 仍指向原始 projection 对象。LLM 重试中间件发的 `run-status(retrying)` 帧把原始 projection 钉死在 RETRYING（重试恢复后无任何帧置回 running），于是 commit 后 `RunService.get` 在整个 300s 终态保留窗口内持续返回 `retrying` + `finish_reason=None`。日志实证：六个 run 窗口内 429 重试密集（全天 290 次 Transient LLM error），`agent_run_reclaimed ... status=completed/error` 证明内存 handle 本身已到终态、唯独 provider 陈旧。修复：`_commit_terminal_candidate` commit 分支同步换绑 `snapshot_provider` 到终态 projection。
>
> **根因 B（run 误杀）**：重试帧 payload 的 `attempt_id` 是**单次模型调用的重试位次**，被 projection 当成 run 级 attempt 全局抬升（第二次重试 → handle.attempt_id=2）；并行/在途模型调用按起点戳记的 attempt=1 帧随后被 `apply_event` 的严格一致校验判为 `StaleAttemptEvent` 致命异常，杀死 producer、健康 run 以 RUN_FAILED 收尾（日志实证 run 82f2045c/eaf1224f/1b09d638 均死于此）。修复：projection 不再从 run-status 帧 bump attempt；producer 一律以 run attempt 发布（帧的 attempt 戳只是遥测字段，进 `model_calls.attempt`）。回归测试：`tests/test_run_retry_terminal_read.py`。

**现象**：三个用例在 180–300 秒轮询窗口内始终等不到终态，但事后查 DB，对应 run 早已终态：

| run_id 前缀 | 用例 | DB 事实 | 测试表现 |
|---|---|---|---|
| `61554b1e` | test_bg_subagent_real::test_super_agent_launches_bg_subagent_to_terminal | completed / stop / **17s** | 轮询 300s 未到终态 |
| `82f2045c` | test_conversation_flow_real::test_multi_turn_conversation_history | error / RUN_FAILED / **13s** | 轮询 180s 未到终态 |
| `5e6a5345` | test_conversation_flow_real::test_session_title_after_first_question | completed / stop / **9s** | 轮询 180s 未到终态 |

**证据定位**：`RunService.get`（`backend/packages/noesis-core/src/noesis/services/run_service.py:729-740`）读取顺序为——内存注册表 handle 优先（`authoritative_snapshot` 或 `snapshot_provider(last_sequence, status, attempt_id)`），仅当 `run_manager.get` 抛 `KeyError`（run 不在内存）时才落回 DB 行。测试的等待辅助函数（两个文件均接受 `{"completed","partial","error","interrupted"}` 全部终态、1s 间隔轮询 GET）在 DB 已终态后仍持续读到非终态，说明该时间窗内内存快照与 DB 行不一致。

**初步假设（供分析验证）**：终态落库路径与内存 handle 收口不同步——handle 的 `authoritative_snapshot`/`status` 未在 finalize 时更新，或 handle 存活期长于终态广播。附带怀疑同一根因波及问题 2。

**复现**：全量轮下两轮并发执行均复现（两轮的 bg 子 Agent / conversation_flow 等待类用例同形态失败）；本轮 run 实际仍走 kilo 网关（见「用模」更正），部分失败 run 的 DB 终态为 error/RUN_FAILED（100s/13s），网关因素**不能排除**；但 `61554b1e`（completed/stop/17s）、`5e6a5345`（completed/stop/9s）两条为**正常完成**却在测试窗口内始终被 GET 报告非终态，与网关无关，属真实的读路径不一致。

### 问题 2：SSE 订阅流在 run 终态后未推送 `run.finished`/`[DONE]`

> **裁决**：✅ 已修复（原记录的「未推送终态帧」判断证伪；真实根因两条均已修）。
>
> **机制澄清**：终态 envelope 在 terminal commit 时必然 fanout（`_commit_terminal_candidate` committed 分支），正常完成 run 的契约测试本轮也全绿。三个用例失败的真实构成：
> 1. `_collect_run_stream` 的 `finish_reason` 只从 `finish` 事件名取值——该帧名在 unify-run-delivery 终态词汇统一后**线上不出现**（唯一流终止事件是 `run.finished`，见 [chat-streaming.md](../engineering/platform/chat-streaming.md) §4.2b），导致 `events.succeeded` 对任何健康 run 恒为 False（1d9f42ab 完成仍失败即此因）。修复：助手识别 `run.finished` 载荷。
> 2. 1b09d638/eaf1224f 的 run 本身被问题 1 根因 B（StaleAttemptEvent）误杀为 RUN_FAILED——error 终态的 run 按断言语义本就不该 `succeeded`。根因 B 修复后此类误杀不再发生。

**现象**：三个用例的 `collect_run_stream` 在 180s deadline 内只收到 `run-snapshot` 等早期帧，`done=False` 且 `error=None`（流未断、无错误帧），而 DB 中对应 run 已终态：

- `1d9f42ab`（test_common_qa_real_llm::test_common_qa_kb_answer_uses_numbered_citation）— DB completed/stop/19s
- `1b09d638`（test_super_agent_real_llm::test_super_agent_deep_research）— DB error/RUN_FAILED/**100s**
- `eaf1224f`（test_super_agent_real_llm::test_super_agent_web_answer_uses_markdown_citation）— DB error/RUN_FAILED/13s

**初步假设**：终态事件未到达该订阅者——与问题 1 同属「终态未同步到内存/订阅侧」家族；也可能是 RunEventBus 订阅建立时序（订阅先于 run 创建的用例无此问题，姊妹用例多轮对话同样等待方式但通过）。注意 `test_chat_stream_contract` 的全部流断言本轮通过，说明正常完成的 run 终态帧正常，异常集中在 **error 终态**（1b09d638/eaf1224f）与特定路径（1d9f42ab）。

### 问题 3：后台子 Agent 目录为空

> **裁决**：❌ 非 Bug（接口层）——模型行为缺陷，DB 实证。
>
> 失败轮次的两个父会话（05dd4cd0 / b7d740f2）**0 个子会话落库**：父 run 3e20f8c7 直接拒答（"I can't help with that."），父 run 82a7a942 把工具调用模板当正文输出（`<|tool_calls_section_begin|>…`）——均为 `kilo-auto/free` 免费池路由到的模型不具备工具调用能力/服从度，`start_task` 从未被成功调用，目录空是正确结果。对照：同日 12:52 会话 89d8d66d 的父 run 61554b1e 正常调用 `start_task` 并创建子会话 2f0ebba0，目录链路本身无缺陷。问题 7 修复后 run 将消费 `token/glm-5.3-flash` 偏好，绕开 kilo 免费池，此类模型服从度失败应显著减少。

**现象**：test_bg_subagent_real::test_next_turn_after_bg_task_terminal —— 父 run 终态后轮询 `GET /children/catalog` 300s，`tasks` 始终 `[]`，子会话从未出现。

**初步假设**：与问题 1 相关（目录状态读自内存/任务注册表）；无法排除 `start_task` 工具未被模型调用（见问题 4 的模型服从度）——分析时先查该 run 的 tool 事件落库。

### 问题 4（模型行为依赖，非接口缺陷）：glm-5.3-flash 未按提示调用工具

> **裁决**：❌ 非 Bug——实为 `kilo-auto/free`（非 glm-5.3-flash，见「用模」更正）模型行为。
>
> 与问题 3 同族：curl_fetch 用例的 run 未发起任何工具调用，属免费池模型工具服从度问题。该断言（要求模型必须调用某工具）对模型选择敏感，判定为环境依赖而非接口回归；问题 7 修复后用模回到 test 账号配置的偏好模型，若仍不调用再按模型能力问题重审。

- test_super_agent_real_llm::test_super_agent_curl_fetch —— `未调用 execute 工具: []`（模型未发起任何工具调用）。
- test_bg_subagent_real::test_super_agent_launches_bg_subagent_to_terminal —— 父 run completed（17s）但该轮断言在 children 之前已因问题 1 失败；其 start_task 是否被调用未定（与其姊妹用例 next_turn 的 `目录: []` 一并分析）。

模型换为 `token/glm-5.3-flash` 后工具服从度与 kilo 免费池不同，此类断言（要求模型必须调用某工具）对模型选择敏感，分析时建议先确认工具事件是否落库再定级。

### 问题 5（连带）：`GET /runs/{id}` 快照缺 `finish_reason`

> **裁决**：✅ 已修复——与问题 1 根因 A 同源。
>
> `finish_reason` 随终态事件写入 projection 的 clone，原始 projection（snapshot_provider 绑定对象）上恒为 `None`。provider 随终态 commit 换绑后，GET 快照返回终态 projection 的 `finish_reason`/`error_code`。回归断言含于 `tests/test_run_retry_terminal_read.py::test_terminal_commit_rebinds_snapshot_provider`。

test_chat_run_real_llm::test_run_snapshot_terminal —— 消费流后 GET 快照 `finish_reason=None`（run 相关行见问题 1/2 的 DB 记录）。与问题 1 的内存快照形状疑似同源。

### 问题 6：`read_file` 工具 runtime 注入在真实调用链失效（前一轮修复未生效）

> **裁决**：✅ 已修复——前一轮修复（保留 runtime 形参）无效的根因不在签名，在**注解求值方式**。
>
> langchain 的 `StructuredTool._injected_args_keys` 用 `signature()` 的**原始注解对象**判定注入键（不做字符串解析）；`read_file_bound.py` 顶部的 `from __future__ import annotations` 把 `runtime: ToolRuntime` 延迟成字符串 `"ToolRuntime"`，注入键探测为空集 → `_parse_input` 按 args_schema（无 runtime 字段）`model_dump` 时剥掉 ToolNode 注入的 runtime → `aread_file_bounded() missing 1 required positional argument: 'runtime'`。deepagents 原工具与 shell_tool（execute）没有 future import，注解是真对象，因此同族只有 read_file 失效——与「部分自定义工具正常」的观察吻合。修复：删除该文件的 future import（文件内已留注释锁定此约束）；回归测试 `test_runtime_injection_survives_full_toolnode_chain` 走真实编译图 + ToolNode 注入链（直调 `func(runtime=...)` 的旧测试覆盖不到此路径，即前一轮的验证盲区）。

**现象**：13:14（运行当前工作区代码的后端上，含 09:03 的 read_file 修复）assistant 消息 tool part 仍记录 `read_file status=error`，后端日志报 `apply_read_file_bound.<locals>.aread_file_bounded() missing 1 required positional argument: 'runtime'`。用户在前端演示时同样复现。

**证据与初步分析（供分析者验证）**：
- 09:02 的同类错误发生在修复前（当时报的是 deepagents 原始函数 `async_read_file` 缺 runtime）；13:14 报的是修复后的包装函数 `aread_file_bounded` 缺 runtime——**两代实现都以同一方式失败**，说明缺陷不在包装签名本身，而在更底层的调用链：langgraph ToolNode 注入的 `runtime` 参数没有到达工具函数。
- 可疑机制：`StructuredTool._parse_input`（`langchain_core/tools/base.py:780-840`）在 `tool.invoke` 时用 `args_schema`（deepagents `ReadFileSchema`，仅 file_path/offset/limit）做 `model_validate + model_dump`，pydantic v2 默认 ignore extra——**注入的 `runtime` 键在此被静默剥掉**。
- 推论：问题可能波及同族全部 FilesystemMiddleware 工具（write_file/edit_file/ls 同样带 runtime 形参）；而部分自定义工具（如 start_task）能正常工作，说明并非所有 runtime 工具都坏，差异点需定位（工具替换时机/执行路径差异）。
- 上一轮修复只验证了「包装函数签名含 runtime」（单测断言签名），未验证「运行时注入真的送达」，属验证盲区。

**复现**：任意 Agent run 中调用 read_file 即现（用户 11:22 演示会话与 13:14 集成 run 均复现）。

### 问题 7：设置页「默认对话模型」偏好无消费者（前端显示 kilo 属实）

> **裁决**：✅ 已修复——`user_llm_preferences.default_model_id` 读侧此前零消费者（目录端点 default_id 已消费，run 解析链没有）。
>
> 修复：`_resolve_model_for_query` 的取值顺序改为「请求显式 model_id → 会话 extra.model_id → **用户默认偏好** → 平台默认目录项」，偏好命中自定义模型时与显式选择同路注入运行时快照；偏好指向已删除模型时 `resolve_catalog_entry` 兜底回平台默认，不抛错。展示层无需改动（目录端点 default_id 已优先偏好）。回归测试：`tests/test_qa_model_resolution_contract.py` 两条偏好用例。

**现象**：用户在设置页把默认对话模型设为 `token/glm-5.3-flash`，但前端消息框模型仍显示 kilo，且 run 实际调用 kilo（`model_calls` 证据，见「用模」）。

**根因定位**：run 的模型解析链 `_resolve_model_for_query`（`noesis/services/qa/helpers.py:198-247`）取值顺序为「请求显式 model_id → 会话 `extra.model_id` → `get_default_model_id()`（config.yaml 平台默认）」；**`user_llm_preferences.default_model_id` 在解析链中无任何读取点**。前端消息框展示的是会话/目录默认（kilo），与实际调用一致——展示层没有错，错在设置项本身不生效。

**待定问题（交分析者）**：设置页「默认对话模型」的产品意图与解析链应如何衔接（偏好应作为无显式选择时的默认，写入新会话 extra 或解析链回退位）；另需确认前端 ModelSelector 的初始选中值来源是否也需同步。

## 已跳过（非问题）

- 1 条 skipped：`gateway_skip` / parse 用例的环境依赖 skip 路径本轮未触发网关降级文案，实际 1 条 skip 为 scheduled parse（@llm 标记用例内的模型服从度 skip 分支）。

## 备注（非问题，供分析上下文）

- 上轮（kilo 网关 era）的 401 连坐、retrying/429、DDG 不可达三类失败本轮未再出现——kilo 相关失败随模型偏好切换自然消失。
- `test 账号内 api-test-* 会话、Provider/审计行由接口测试产生属预期`（用户裁决：test 账号即测试专用）。
- 本轮未修改任何代码；执行步骤已写入仓库根 [AGENTS.md](../../AGENTS.md)「接口测试执行步骤」节（分层命令、账号约定、失败判定、记录要求）。
