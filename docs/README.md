# Noesis 文档

本目录只保存需要随代码演进的正式文档。可验收行为以 `openspec/specs/` 为准；PRD 解释产品目标、架构和数据流，不重复完整 SHALL/Scenario。

## 文档地图

| 目录 | 职责 |
|------|------|
| `prd/` | 当前产品与架构设计 |
| `bug/` | 尚需跟踪的 Bug；修复后更新状态，长期经验移入 debugging |
| `debugging/` | 多次排查后仍值得保留的根因与诊断方法 |
| `test/` | 跨模块测试与评测设计 |
| `NOTES.md` | Noesis 决策卡片与 DeepDoc vendor 修改记录 |

## 权威来源

| 内容 | 来源 |
|------|------|
| 产品介绍、启动和部署 | 仓库 `README.md` |
| 开发规则、目录入口 | `AGENTS.md`、`frontend/AGENTS.md`、`backend/AGENTS.md` |
| 可验收行为 | `openspec/specs/` |
| 尚未实现的变更 | `openspec/changes/` |
| 架构解释与产品数据流 | `docs/prd/` |
| 已完成变更历史 | `openspec/changes/archive/`、Git history |

## 核心 PRD

- [SSE 流式数据设计](prd/platform/SSE流式数据设计.md)
- [聊天记录设计](prd/platform/聊天记录设计.md)
- [知识库 RAG 底座](prd/knowledge-base/知识库RAG底座详细设计.md)
- [测试用例生成](prd/agent-test-case/测试用例生成设计.md)
- [故障运维 Agent](prd/agent-fault-operation/故障运维设计.md)

## 维护规则

1. 新行为先写 OpenSpec change，实现完成后归档并同步主规格。
2. PRD 只保留当前方案，不保存 v2、旧方案和版本对比。
3. 代码路径变化时同步更新相关 PRD。
4. 已修复 Bug 不长期堆在 `bug/`；有复用价值的根因写入 `debugging/`。
5. 个人清单放 `docs/TODO.local.md`，该文件不提交。
