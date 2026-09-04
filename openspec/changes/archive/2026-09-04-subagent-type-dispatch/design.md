# Design: subagent 类型分发与任务运行时身份澄清

## Context

现状三处事实（代码锚点）：

- `agents/subagents/tools.py` 的 `start_task` 入参只有 `description / prompt / run_in_background`，无类型维度；五个工具由 `agents/super_agent.py` 装配层临时 `tools.extend(...)` 挂载，是能力栈里唯一不走 middleware 的例外。
- worker 配方闭包在 `super_agent.py` 的 `_bg_worker_factory`：系统提示词（`PromptProfile.SUPER_AGENT_SUB`）、工具集（主 Agent 工具剔除后台任务工具与 `search_memory`，加只读记忆工具）、backend（`memory_read_only=True`）全部单一硬编码；`_compile_task_worker`（`super_agent.py:52`）本身已参数化。
- `BackgroundSubagentExecutor`（`agents/subagents/executor.py`）是双 kind 后台任务运行时：`kind="subagent"`（worker 编译、child session、HITL、followup）与 `kind="shell"`（无落库、无 followup、`agent_factory=None`）。subagent 特性经 `agent_factory / followup_factory / SubagentSessionPort` 注入而非内嵌；执行内核分流于 `_arun` / `_arun_shell`。约 10 处 `kind == "shell"` 分支散落在生命周期方法中。

约束：执行器是全仓不变量密度最高的模块（状态机、协作停止宽限与对账、`_finalize` 终态单一收口、锁纪律），任何设计不得改动其状态机与收口路径；主/子 run 管道已统一，前端按 child session id 消费任务。

## Goals / Non-Goals

**Goals:**

- `start_task` 支持按注册表枚举的 `subagent_type` 分发，新增一个种类 = 注册一个声明，不改装配代码。
- 任务身份进主 Agent graph state，上下文压缩后模型仍能列出手上全部任务。
- 子 Agent 工具面收编为 middleware，与 `BgNotifyMiddleware` 等同构。
- 执行器命名与职责对齐（后台任务运行时），shell 受益于澄清。
- v1 单类型 `general`，全链路行为与现状逐字一致。

**Non-Goals:**

- 不做文件式角色定义（descriptor 落库为其预留读取位，本变更只写不读）。
- 不拆执行器、不动状态机与终态收口。
- 不引入第二个真实种类（分发正确性由单类型回归 + 契约测试钉住）。
- 不改 shell 任务行为。

## Decisions

### D1：三层职责——声明面 / 注册表 / 运行时

```
NoesisSubagentMiddleware     SubagentRegistry            BackgroundTaskExecutor
工具面 + state + prompt 注入  type → 编译配方（声明）      生命周期运行时（类型无关）
```

**选型理由**：类型分发的全部需求都可以在不触碰执行器的条件下实现——`executor.start()` 本来就按任务接收 `worker_factory` 闭包（`_TaskEntry.agent_factory`），middleware 在调用前把 type 解析为具体工厂即可。执行器继续只认「一个按任务注入的工厂」，kind 维度维持现状。

**被否方案**：按上游 deepagents 的 `AsyncSubAgentMiddleware` 形态直接复用（其执行边界是远程 Agent Protocol 客户端，与本进程隔离 loop 模型冲突，套壳即两套执行语义）；拆分为 subagent / shell 两个执行器（复制终态收口与协作停止机器，是全仓最不该重复的代码）。

### D2：SubagentRegistry——声明与校验

```python
@dataclass(frozen=True)
class SubagentRole:
    name: str            # subagent_type 枚举值，注册时重名即 ValueError
    description: str     # 注入 system prompt，供模型选择
    worker_factory: Callable[[str | None], Any]   # model_id 覆盖入参，同现有签名
    interrupt_on: dict | None = None              # 缺省沿用现有 build_interrupt_on(memory_write_guard=False)
    model_id: str | None = None                   # 配置层模型绑定；None = 沿用父 Agent 模型
```

- 注册发生在 SuperAgent 装配期（`super_agent.py` 构造 registry，替代裸 `_bg_worker_factory`）；重名校验在装配期 fail loud，不推迟到首次委派。
- **模型绑定在配置层解析**：`model_id` 非空时该类型的 worker 以绑定模型编译、child session 记录该模型；`start_task` 不暴露模型选择参数——运行时只选类型，不选模型。既有 followup 按轮模型覆盖机制（`_apply_model_override`）在绑定基线之上继续生效，语义不变。
- v1 仅注册 `general`：worker 配方 = 现 `_bg_worker_factory` 原样搬家，`model_id=None`（沿用父模型，与现状一致），保证零行为变化。
- 递归委派防护维持现状（worker 工具集不含后台任务工具），不引入深度计数。此声明面即未来文件式角色配置的解析目标（配置文件字段 → `SubagentRole`，另立项）。命名取「角色」而非 spec/profile/kind：与 openspec 规格文档、既有 agent-profiles 能力（`PromptProfile` / `extra.agent_profile`）、任务 kind（subagent/shell）三个存量概念区分。

### D3：NoesisSubagentMiddleware——state、工具面、prompt

**state_schema**（学自家的七个带 state 的 middleware 家族模式）：

```python
class BgTaskIdentity(TypedDict):
    task_id: str
    child_session_id: str
    subagent_type: str
    description: str
    last_status: str    # 仅快照，权威状态实时查执行器/DB

bg_tasks: Annotated[dict[str, BgTaskIdentity], merge_reducer]  # 按 task_id 合并
```

**权威性规则（本设计核心约束）**：state 只承载身份与最后一次工具交互时的状态快照，用于压缩后重建任务清单；`check_task` / `list_tasks` 的状态与结果永远实时取执行器（miss 落 DB），绝不信 state。终态任务保留在 state 中不删，避免「压缩后模型不知道已收过哪些结果」。

**工具面收编**：五个工具从 `tools.py` 移入 middleware（构造时闭包捕获 registry、executor、launch/followup 等 service 回调），行为逐字保留；差异仅两处——`start_task` 增**必填** `subagent_type: Literal[...]`（schema 由注册表生成，缺失即 schema 校验拒绝）并在类型未注册时返回可诊断错误文本；工具返回值从纯字符串改为 `Command(update={...})`（回 ToolMessage 同时写 `bg_tasks`）。`super_agent.py` 的临时 `tools.extend` 段退役。

**prompt 注入**：`wrap_model_call` 向 system prompt 追加类型清单（`- name: description` 逐行）。

### D4：subagent descriptor 落库——版本化显式字段

`SubagentSessionService.launch()` 增 `subagent_type` 参数，写入 child session `extra` 下**独立键** `subagent`：

```json
{"subagent": {"version": 1, "type": "general", "model": "glm-5.3"}}
```

`model` 记录该类型解析后的生效模型（绑定模型或父 Agent 模型），与既有 `extra.model_id`（子会话模型显示）同源写入；冷恢复重建 worker 时按 descriptor 的 type + model 取配方，无需回读父会话状态。

**显式字段而非散键的理由**：extra 是自由合并的杂项容器，descriptor 用独立子 dict + 固定字段 + 读取时校验，未来加字段不改消费方解析、不受无关键污染；版本号给冷恢复重建留演进空间。历史 child session 无该键不回填——descriptor 只对本变更之后创建的会话有效，重启重建路径消费时缺失即按数据不完整处理（该消费路径 v1 尚未成型）。

消费场景今天只有一个：进程重启后重建 worker 的路径按 type 从注册表取工厂。v1 该路径未成型也无害——先写不读，是文件式角色定义立项前的预留位。

### D5：执行器改名——`BackgroundTaskExecutor`

- 改名 + 模块 docstring 重述职责（后台任务运行时，双 kind，subagent 特性经注入）；日志前缀 `bg subagent` 中 shell 相关改 `bg task`。
- `BackgroundTask` 增 `subagent_type: str = "general"` 字段，进 `to_dict()` 投影与任务卡。
- **不改**：状态机、`_TaskEntry` 结构、`_arun`/`_arun_shell` 分流、散落的 kind 分支（两种 kind × 十来处判断低于抽象阈值；触发线 = 第三种 kind 出现）。

### D6：SSE / 事件与错误契约

- 不新增 SSE 事件：任务卡走既有 task 事件，投影多 `subagent_type` 字段（前端旧版本忽略未知字段，无破坏）。
- `subagent_type` 校验失败的错误经 `start_task` 工具返回值（字符串错误文本）走既有工具错误三通道，不新增异常类型。
- `Command` 返回值与现有来源登记（`register_pending_sources`）共存：`check_task` 的返回文本逻辑不变，Command 只额外写 state。

## Risks / Trade-offs

- [工具返回值改 `Command` 与既有 ToolMessage 拼装 / 事件桥接的兼容性未知] → 迁移第 2 步单独成任务，先写 executor 侧行为逐字等价的契约测试（五个工具各一）再改返回形态；langgraph 工具返回 Command 时 ToolMessage 内容由 `Command.update.messages` 提供，桥接层消费的仍是 ToolMessage，验证一条即推广。
- [state 与 DB 双份任务记录可能漂移] → D3 权威性规则钉死「身份入 state、状态不入或仅快照」；契约测试断言 check_task 在 state 快照过期时仍返回实时状态。
- [改名波及面（import、日志 grep、既有文档引用）] → 全仓机械替换 + `python3 scripts/change-scope.py` 确认影响面；对外 API 无一处暴露执行器类名。
- [v1 单类型使分发路径缺真实第二消费方，容易埋「分发正确但没人用」的债] → 接受：单类型回归 + 契约测试覆盖 type → factory 解析；第二个种类立项时补集成验证。
- [worker 工具集按类型分化后，某类型误持后台任务工具导致递归委派] → `general` 配方沿用现有剔除逻辑；registry 构建处集中断言所有 worker 工具集不含 start_task（防线前移到装配期）。

## Migration Plan

按依赖序四步，每步可独立验证、可单独回滚：

1. **注册表 + descriptor 落库**：`SubagentRegistry` + `launch(subagent_type=...)` 写 `extra.subagent`，`general` 唯一类型。验证：后端起服务跑一轮真实委派，child session 落库带 descriptor；现有全量测试绿。
2. **middleware 收编**：五工具迁入 `NoesisSubagentMiddleware` + `bg_tasks` state + `Command` 写入 + prompt 注入 + `start_task` 增参数。验证：工具行为契约测试（与迁移前输出逐字对比）+ state 内出现 identity 断言。
3. **执行器改名**：`BackgroundSubagentExecutor → BackgroundTaskExecutor` + `BackgroundTask.subagent_type` 投影。验证：全量测试 + grep 无残留旧名。
4. **前端任务卡类型标识**（可选收尾）：投影字段透出。

回滚：1–3 各步独立 commit，revert 单步不波及他步；descriptor 为纯增量字段，回滚后历史会话的该键成死数据、无消费方，无害。

## Open Questions

（无——`subagent_type` 必填、不做历史兼容已定案：模型侧 schema 必填即拒绝缺省调用，历史 child session 无 descriptor 不回填。）
