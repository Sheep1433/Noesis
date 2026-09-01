# 决策：Chat 对话面：去掉 draft，改回发送才物化

状态：implemented
日期：2026-07-22
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **修订**：产品确认未发送附件无需服务端保存，刷新丢失合理；删除 draft / staging / soft lifecycle。
- **现行定稿**：COMPOSING→SENDING→ACTIVE；点击发送才 ensure；附件保持方案 B（本地队列）；偏好三层仍在，overlay 仅内存。
- **历史文档路径（已删除）**：`docs/prd/platform/Chat对话面生命周期设计.md`；当时同步修改 `openspec/specs/chat-surface-lifecycle/spec.md`，落地 P0→P1→P2（无 staging 阶段）。
