# 设计：本地项目目录模式

## 1. 路线决策：CLI 优先，不动平台存储

TODO 起草时把「存储瘦身」（P0.3）当成一条均质任务，代码核验表明它按形态分裂为两个量级：

- **网页形态（app.py）去 PG**：`pg_manager` 模块级单例被 29 文件 156 处直引；Alembic 迁移与防双开锁用 PG advisory lock；`ChatService` 直写 ORM 无 repository 边界；checkpointer 是 `AsyncPostgresSaver`。SQLite 化是一个季度的工程。
- **CLI 路线（noesis-cli 扩展）**：`ChatSession` 组合根（`temporary_checkpointer` + `register_runtime_ports`）已验证不碰 PG；持久化从零写轻层即可。

**取 CLI 路线**。`uv run noesis --project ~/some/repo` 是目标入口；浏览器形态（P3.1）将来若需要，再加本地 web 入口复用同一组合根——不反过来先啃平台存储。代价：会话数据与平台不互通（本地 `.noesis/local.db` vs 平台 PG），接受——自用工具与服务平台本就是两种消费面。

防分裂约束：CLI 复用 `factory.create_noesis_agent` 与中间件栈装配，只替换「存储 + 文件后端 + 入口」三个组合根参数，**不 fork Agent 核心逻辑**；任何对 factory/stack 的改动平台侧照常回归（CI 即覆盖）。

## 2. 文件后端与路径语义

`AgentPathBackend`（`agents/backends/agent_path.py`）已承担「agent 绝对路径 → 后端真实路径」的映射，`strip_root` 剥 `/workspace` 前缀。本地模式：

- `inner` 用 `create_local_shell_backend()`（宿主机直接执行）；
- `strip_root=None`、`canonicalize=False`：**agent 看到的路径 = 宿主机真实路径**（不做 `/workspace` 平移——`--project` 下模型对「项目在 `/Users/xxx/repo`」的感知与用户一致，路径可直接复制交流）；
- workspace 根 = `--project` 目录（传入 factory 的 `workspace` 参数，动态上下文与提示词中的工作目录声明随之生效）。

沙箱 `execute` 工具在 local_shell 后端下直接在宿主机跑命令（无容器）——**这正是权限层必须先行于真实使用的原因**（TODO P1 顺序约束：权限必须在碰真实项目之前就位）。

## 3. 权限层

TODO P1.4 写的挂载点 `agents/runtime/tool_registry.py` 不存在。真实挂载点两层：

1. **路径边界（写操作）**：`AgentPathBackend` 外再包一层 `ProjectBoundaryBackend`（同 `SandboxBackendProtocol`）：写/删目标解析后落在项目根外 → 拒绝并返回可解释错误（「项目外路径：{path}，本次会话工作目录为 {root}」）。读放行。这个位置对中间件栈透明、对 `FilesystemMiddleware` 全部文件工具一次覆盖，且不依赖任何新机制。拦截器保持框架无关（只看工具名 + 参数 dict，返回值或错误文本）——参照 deepagents-code 的 `RepositoryBounds`：一条实现被多个消费方共享，符号链接逃逸在解析后判定。
2. **操作分级（执行/网络/写确认）**：复用 HITL——`HumanInTheLoopMiddleware(interrupt_on=...)` 按工具名中断的机制现成；扩展 `InterruptOnConfig` 支持参数级谓词（`execute` 的命令、网络工具的 URL、写工具的路径模式）判定是否需要确认。CLI 侧的确认 UI 是终端 y/n 提示（`noesis-cli` 交互已有先例）。全局模式取 deepagents-code 的三档词汇：`manual`（逐次确认）/ `auto`（项目内自动、高危仍确认）/ `yolo`（全自动，进入前需显式确认且界面常驻指示）——比 TODO 的「可配置放行」更完整，且已被官方产品验证。

**授权记忆**（P1.2「本次允许 / 永久允许」）：按「工具 + 参数模式」记录进 `.noesis/local.db` 的授权表；「永久允许」命中即免确认，「本次允许」进会话内缓存。CLI 的 `--yes-all` / 配置文件可预置放行集（等价 Claude Code 的 permission modes）。

**与 ReadBeforeWrite 衔接**：栈里已有「没读过的文件拒绝改」的 hash 门禁，权限层在其外——先边界/分级，再内容门禁，顺序固定。

## 4. 本地持久化

- **checkpointer**：新增可选依赖 `langgraph-checkpoint-sqlite`，CLI 组合根换 `AsyncSqliteSaver`（`temporary_checkpointer` 的 ContextVar 覆盖机制沿用）；路径 `.noesis/checkpoint.db`。换实现已有先例（noesis-cli 的 `MemorySaver` 即经此机制跑通）。选型与 deepagents-code（官方终端 Agent）一致，其生产细节需一并处理：aiosqlite 锁库的 busy_timeout 等待、状态目录权限硬化；会话格式带版本号（参照其 `state_migration`），未来格式变更可迁移。
- **会话锁**：同项目并发开两个 CLI 进程会争抢同一 `.noesis/` 状态（SQLite 写锁 + checkpoint 互踩）——加会话级文件锁（deepagents-code `_session_lock` 同款问题），第二个进程给出可解释错误而非静默互踩。
- **会话/消息/授权/待办**：`.noesis/local.db` 一个 SQLite 文件，直接 SQLAlchemy（aiosqlite）建四张轻表（session / message / permission_grant / todo_item）——**不经 ChatService / RunService / pg_manager**，CLI 自己的 repository。终态一致性保持「先比较再写入」（compare-and-set 单行 UPDATE 的 WHERE 带旧值），SQLite 单写者下足够，不引入平台的三段式复杂度。
- **记忆**：memory 包（文件式 store）本就落 `.noesis/users/`，与项目内 `.noesis/` 的关系按 TODO P0.1「按项目隔离」——项目根 `.noesis/` 即该项目的全部状态（DATA_DIR 机制已有 `NOESIS_DATA_DIR` 环境变量切点，CLI 设为项目内）。

## 5. 里程碑（对应 TODO P0→P1→P2 顺序）

1. **M1 能跑**：`--project` 参数 + local_shell 后端 + 路径直通 + `.noesis/` 落项目内。读写全放行（只在一次性测试仓库上用）。验收：裸 git 仓库零配置跑通对话 + 文件读写。
2. **M2 敢用**：ProjectBoundaryBackend + HITL 分级扩展 + 授权记忆 + 终端确认 UI。验收：项目外路径必弹确认；拒绝后 Agent 继续工作不卡死。
3. **M3 用得久**：SQLite checkpointer + local.db 会话/待办 + 记忆按项目隔离。验收：重启后会话可续；多项目互不串扰。
4. **M2 完成才进 P2 每日自用**（TODO 的顺序约束：权限先于真实项目）。

## 6. 与 deepagents-code 的关系及被否方案

deepagents-code（`dcode`，langchain-ai 官方终端编码 Agent，同 SDK 底座）已实现 TODO 的目标形态（真实目录 + approval 三档 + SQLite 持久化 + skills/subagents/memory），其已验证设计（三档模式、`AsyncSqliteSaver` + busy_timeout、会话锁、框架无关拦截器）本提案直接吸收，见 §3/§4。**不做的事与它划清**：本提案不造通用编码 Agent，是让 Noesis 的工作模型（QA 编排、记忆 consolidation、后台子 Agent、平台形态）能在本地目录运行——dcode 恰好没有的这层。

- **直接改用 dcode 放弃自研**：被否——dcode 的工作模型是通用编码 Agent（简单持久记忆、无 QA 编排/知识库/后台任务交付契约），TODO 的核心诉求是自己的工作模型落地本地，不是再要一个 Claude Code 替代品。
- **fork dcode 塞入 Noesis 装配**：被否——dcode 的 hooks/extensions/plugins 体系与 Noesis 的 middleware 栈是两套组合机制，fork 后双轨维护成本高于自建三步（M1-M3），且丢失与上游 SDK 的升级同步。
- **先做网页形态本地化（app.py SQLite 化）**：被否——156 处 pg_manager 直引无 repository 边界，工程量差 CLI 路线一个数量级；形态不是重点（TODO 原文），先用最短路径验证工作模型。
- **AgentPathBackend 里内嵌权限判断**：被否——该层职责是路径映射，塞分级逻辑会让「读放行/写询问」的策略与映射耦合；独立包装层可单独测试与替换。
- **新造中断机制做确认**：被否——HITL 栈内机制现成（`interrupt_on` + resume 链路），TODO P1.3 原文即要求复用。
- **本地模式复用 ChatService（改造其存储后端可插拔）**：被否——为兼容平台 ORM 全家桶而抽象 repository 层，是反向给平台重构铺路的路线；本地轻表 200 行内解决，不值得拖上 29 文件的依赖面。
