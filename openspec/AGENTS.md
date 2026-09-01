# openspec 目录约定

本目录的 standing orders。openspec-* skill 由 openspec CLI 生成（`openspec update` 会重写它们），**禁止本地修改**——Noesis 特有的流程要求全部写在本文件，经 AGENTS.md 层级对任何 Agent 生效。

## 提案门禁（propose）

design.md 交出前必须过两道必答题，缺一即提案不合格：

1. **备选方案对比**：至少列出一个被否的做法及落选理由（「备选方案」节）。只讲选中方案不讲被否方案的设计，读者无法判断取舍是否成立。
2. **语义消费者说明**：每个新增的持久字段、事件、API、配置项、模块，指明一个依赖它的真实场景（本 change 内已承诺的消费者）；答不出的属于投机设计，从提案中删除。被否方案与最终决策落 `docs/decisions/`。

## 归档提炼（archive）

change 归档时把 `design.md` 中有长期价值的判断提炼为一条决策记录（`docs/decisions/implemented/`，新格式含备选方案节）：

- 提取未来改动仍需要的：约束性判断、被否方案及其落选理由、接受的代价。机械性或无长期决策的 change 跳过并说明。
- 日期取 change 首次提出日（proposal.md 创建日），不是归档日。
- 写之前按 `docs/decisions/README.md` 的取代规则检查同主题旧记录：完全取代则并入并删旧、部分取代则双向链接。

## spec 与 change 约定

- 主规格 `specs/<capability>/spec.md` 是可验收行为的唯一权威；归档时 delta 与主规格对齐（`openspec-archive-change` skill 驱动，无需人工比对）。
- spec/design 的 prose 遵守 `noesis-prose-standard`（可读性）与 `noesis-prose-hygiene`（会话残留）两个 skill 的标准——spec 难读的首要原因是推导过程上屏与大纲体。
- 正式 spec/design 不引用外部项目（第一性自洽叙述）；调研与外部比较写 `docs/research/`，需要时以内部调研文档链接作为证据出处。
