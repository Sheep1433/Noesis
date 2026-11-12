# Delta: agent-heads — 新增能力

## ADDED Requirements

### Requirement: 核心/头分离契约

Agent 运行时 SHALL 以单一核心进程承载（SuperAgent 装配、双档 profile、能力开关、权限、状态），CLI / headless / web / 桌面端 SHALL 作为头（head）挂接。核心侧对头的唯一出口 SHALL 为 `HeadSession` 会话接口（发消息、订阅事件流、确认应答、停止控制）：本地头（CLI / headless）经进程内直连消费该接口，远程头（web / 桌面）消费该接口的头协议投影。头 SHALL NOT 直接引用 agent 内部装配（factory / 中间件栈 / builder）。CLI 交互层 SHALL 只消费该接口——这是 web 头与桌面头复用同一交互层的前提。

#### Scenario: 头替换不改核心

- **WHEN** 新增一个头（web / 桌面端）或替换 CLI 交互层
- **THEN** 核心进程与 `HeadSession` 接口 SHALL 无改动
- **AND** 新头 SHALL 只依赖头协议（事件词汇 + 传输编码）

### Requirement: 双档 profile

核心 SHALL 支持两档 profile：轻档（无工作目录与沙箱；工具面为能力开关勾选的检索与记忆）与重档（`--project` 工作目录、文件系统、skills、子 Agent）。KB / web / 记忆 SHALL 为与档位正交的按会话能力开关，两档均可勾选。档位选择 SHALL 由显式参数或入口表达，不依赖意图分类器。

#### Scenario: 轻档零配置会话

- **WHEN** 不带 `--project` 启动 CLI 会话
- **THEN** 会话 SHALL 不挂载文件系统工具与沙箱，仅含勾选的能力开关工具

#### Scenario: 重档全工具会话

- **WHEN** 以 `--project <dir>` 启动
- **THEN** 会话 SHALL 挂载全工具面且 Agent 路径与宿主机路径一致

#### Scenario: 能力开关正交

- **WHEN** 任一档位会话勾选/取消 KB 能力
- **THEN** 该会话的工具面 SHALL 相应挂载/卸载知识库检索，档位其他行为不变

### Requirement: 头协议（事件词汇与传输）

头协议 SHALL 复用既有 SSE 事件词汇表（`run-snapshot` / `text-*` / `tool-input-*` / `hitl-required` / `run.finished` 等，权威记录于 chat-streaming.md §4.2b）作为唯一事件层，SHALL NOT 另行发明词汇。传输编码 SHALL 按头适配（进程内直连、HTTP/SSE、WebSocket/stdio），事件语义 SHALL 跨传输一致。头与核心 SHALL 协商协议版本；事件词汇演进 SHALL 走 deprecation 流程并由契约测试钉住。

#### Scenario: 跨传输事件一致

- **WHEN** 同一会话分别经 CLI（进程内）与 web 头（HTTP/SSE）消费
- **THEN** 两头收到的事件序列与语义 SHALL 一致

#### Scenario: 远程头版本协商

- **WHEN** 远程头（web / 桌面，经协议传输连接核心）与核心协议版本不匹配
- **THEN** 连接 SHALL 给出可解释错误，不产生未定义行为
- **AND** 进程内本地头与核心同进程，版本恒一致，不触发协商路径

### Requirement: 桌面衍生纪律

CLI SHALL 按可衍生桌面产品的标准构建：交互层与核心分离（薄头）、协议版本化、事件词汇单一来源。桌面端（如 Tauri 包装的头）MAY 作为后续变更实现，SHALL NOT 在本能力中要求；其可行性由上述三条纪律保证。ACP（Agent Client Protocol）MAY 作为外部编辑器/桌面的适配层接入，其评估结论 SHALL 记录于头协议冻结时。

#### Scenario: 薄头约束

- **WHEN** 审查 CLI 交互层代码
- **THEN** 其对核心的全部调用 SHALL 经 `HeadSession` 接口与头协议事件
- **AND** 直接 import agent 内部模块的走线 SHALL 被视为违规
