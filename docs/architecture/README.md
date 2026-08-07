# 架构设计

这里解释 Noesis 当前长期有效的系统边界、组件职责、调用与数据流、状态、权限和失败处理。

架构文档不重复 OpenSpec 的完整 Scenario，也不提前把研究建议写成当前事实。尚未落地的方案留在 OpenSpec change 或 `docs/research/`。

- [Agent Memory](platform/agent-memory.md)：L0/L1/L2 分层、每日整理、检索与权限边界。
- [Agent Runtime](../engineering/agents/agent-runtime-design.md)：五类 runtime owner、Profile capability、tool envelope 与 stop reason。
