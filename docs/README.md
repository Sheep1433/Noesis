# Noesis 文档

本目录保存 OpenSpec 之外仍值得长期阅读的技术材料。OpenSpec 管理变更范围、决策和可验收行为；这里解释研究依据、当前架构和有分享价值的工程问题。

## 文档地图

| 目录 | 回答的问题 |
|---|---|
| `research/` | Noesis 当前情况怎样，外部项目怎样实现，有哪些选择和证据？ |
| `architecture/` | 当前系统如何组成，边界、数据流和约束是什么？ |
| `engineering/` | 某个困难机制为什么难，如何实现、验证和排障？ |
| `bug/` | 当前仍需跟踪哪些 Bug？ |
| `debugging/` | 哪些根因和诊断方法值得再次使用？ |
| `test/` | 跨模块测试与评测如何设计？ |
| `NOTES.md` | Noesis 决策卡片与 DeepDoc vendor 修改记录 |

## 与 OpenSpec 的边界

| 内容 | 权威来源 |
|---|---|
| 变更动机和范围 | `openspec/changes/<change>/proposal.md` |
| 本次变更的技术决策 | `openspec/changes/<change>/design.md` |
| 可验收行为 | `openspec/specs/` 与 change 内 `specs/` |
| 实现任务 | `openspec/changes/<change>/tasks.md` |
| 调研证据与外部方案比较 | `docs/research/` |
| 当前长期架构 | `docs/architecture/` |
| 高难度实现与经验 | `docs/engineering/` |

`openspec-explore` 可以执行代码调查、外部调研和方案比较，但没有固定输出，也不会默认生成 `research.md`。需要保留完整研究过程时写入 `docs/research/`，再将最终结论分别写入 OpenSpec artifacts。

## 核心文档

- [知识库 RAG 架构](architecture/knowledge-base.md)
- [SSE 流式数据](architecture/platform/chat-streaming.md)
- [Durable Agent Run 与断线恢复架构](architecture/platform/durable-agent-runs.md)
- [聊天记录与持久化](architecture/platform/chat-persistence.md)
- [设置控制面](architecture/platform/settings-control-plane.md)
- [故障运维 Agent](engineering/agents/fault-operation-agent.md)
- [测试用例生成](engineering/agents/test-case-generation.md)
- [Agent 评测运行指南](engineering/agents/agent-evaluation.md)
- [Agent Runtime 设计（Proposed）](engineering/agents/agent-runtime-design.md)

## 何时新增文档

至少满足一项再新增：

- 存在不直观的约束或重要技术取舍；
- OpenSpec 归档后仍需要持续阅读；
- 能帮助其他工程师理解、诊断或借鉴实现。

普通 CRUD、小配置修改和可以直接从代码或 OpenSpec 看清的内容不单独写长文。

## 维护规则

1. 研究报告记录日期、外部项目版本或 commit，并区分事实、推测和 Noesis 建议。
2. 研究建议不等于当前实现；落地后同步更新对应架构或工程文档。
3. 架构文档只描述当前方案，不保留 v2、旧方案和版本对比。
4. 代码路径变化时同步检查文档链接和实现入口。
5. 已修复 Bug 不长期堆在 `bug/`；有长期价值的根因写入 `debugging/`。
6. 个人清单放 `docs/TODO.local.md`，该文件不提交。
