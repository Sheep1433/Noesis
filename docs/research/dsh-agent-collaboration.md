# dsh（deepseek-harness）的 Agent 协作方式调研

> 状态：Research
> 调研日期：2026-09-01
> 证据版本：deepseek-harness 上游 `141eb6fef8`（0.1.0-rc.8 合并点，本地 fork `git@github.com:Sheep1433/deepseek-harness.git`）
> 关联：无（候选借鉴项落地时再挂 OpenSpec change）

## 1. 调研目标与范围

本报告回答一个问题：deepseek-harness（下称 dsh）的开发者是如何用 coding Agent 协作开发这个仓库的——他们建立了哪些流程、约束和留痕制度来保证 Agent 产出的质量。

调研范围是**协作方式**，不是产品代码：dsh 本身是一个 Agent harness，其 `packages/` 下的 subagent、debate-room 等属于「他们开发出来的产品能力」，不在本报告关注范围内。关注对象是仓库里的制度层：`AGENTS.md` 体系、`.agents/`（notes + skills）、`scripts/` 的 gate、git 历史中的协作痕迹。

调研动机：Noesis 目前的 Agent 协作依赖 `AGENTS.md` + openspec + 用户个人记忆约束，实际开发中长期存在几个痛点——代码与文档不一致、过期注释干扰判断、Agent 产出的方案质量难控、Agent 互审总能翻出新的低价值问题、人类审查无法覆盖全部内容。本报告评估 dsh 的制度对这些问题有哪些可借鉴的解法。

## 2. 证据前提：上游内容与本地内容的区分

调研过程中发现一个必须记录的事实，避免后续读者踩同样的坑：

- `.agents/`（notes + skills）、根/子树 `AGENTS.md`、`scripts/` 的 60+ gate 脚本、lefthook/knip 配置**均为上游已提交内容**（`.agents/` 下被 git 跟踪的文件有 2198 个，其中 implemented 决策记录 1634 篇）。
- 顶层 `debate-implementation-report.jsonl`、`feature-discussions/`、`packages/experimental/`（debate-room / agent-team / agent-hub）是**本地未跟踪文件**——是本仓库作者在自己的 fork 上按上游规范用 Agent 实现 debate-room 时产生的，不是上游交付物。初次调研时曾误将这些当作上游证据引用。
- git 历史中 `codex/product-subagent-failure-facts-codex` 与 `-claude` 双分支、`ds-review-bot` 相关提交是上游真实历史。

一个有价值的旁证：本地 Agent 会话遵循上游制度干活时，自动产出了与上游同构的决策记录（三文件组、生命周期路径）和实施报告。这说明这套制度可以被 Agent 严格遵循，制度文件本身即足够明确的指令。

## 3. dsh 的协作体系

### 3.1 三层约束结构

dsh 对 coding Agent 的约束分三层，各管一段、互不重复：

| 层 | 载体 | 管什么 | 进入上下文的方式 |
|---|---|---|---|
| 常驻规则 | 根/子树 `AGENTS.md` | standing orders，每条 1–3 行 | 每个会话必然加载 |
| 流程知识 | `.agents/skills/`（12 个 skill） | 「怎么做好某类事」的操作手册 | frontmatter 描述触发，按需加载 |
| 机器执法 | `scripts/` 60+ gate + lefthook + CI | 一切可机械检查的属性 | 违规时红灯 |

衔接方式是关键：`AGENTS.md` 里的规则不展开解释，只写一行然后**按名指向 skill**（如推送前检查走 `dsh-pre-push-checks`）；skill 开头必有「Sources of truth: read, don't re-summarize」节，指向契约原文而不自己复制内容；gate 兜底机械属性。即 **`AGENTS.md` 是索引，skill 是手册，gate 是执法**。

两个配套细节：

- 根 `AGENTS.md` 有**字数预算 gate**（≤1600 词），因为它每个会话都进 context。规则太长要么压缩、要么给 gate 抬上限并留决策记录。文件内还有「如何修订本文件」的自指说明。
- skill 的 frontmatter 带 invocation policy（`modelInvocable` / `userInvocable` / `disable-model-invocation`）。例如翻译 skill 标记了禁止模型自行触发，只许用户显式点名——防止 Agent 擅自启动重型流程。

### 3.2 决策记录：Agent Notes

`.agents/notes/` 是唯一的设计决策文档体系，共 1453 篇（implemented 1096 / archived 287 / rejected 22 / proposed 50）。制度要点：

- **同 PR 硬性交付**：Every non-trivial change MUST add or update at least one Agent Note in the same PR。
- **强制写被否方案**：每篇必有 Alternatives considered 节。原文理由：「A decision recorded without what it beat invites re-litigation」——不记录否掉了什么，团队会反复重新争论。
- **路径编码生命周期**：`{proposed|implemented|rejected|archived}/{feature|bug-fix|simplification|architecture|process|testing}/yyyy-mm-dd-topic.md`，分类是封闭集，由脚本 + gate 强制。
- **取代审计（supersession）**：写新 Note 时必须检查是否取代了覆盖同一决策的旧 Note，归档/合并/交叉链接在同一次变更里完成。
- **归档冻结**：低未来价值的 implemented Note 移入 `archived/` 后加 hash sidecar、append-only manifest，任何再编辑都被 gate 拒绝。归档判断按未来决策价值（有校准示例），明令禁止按字数、年龄或配额。
- rejected Note 的保留条件：仅当被拒方案「仍然是一个诱人的错误」且 Note 解释了它为什么输。失败决策也是组织知识。

这套体系本质是 ADR（业界成熟实践）加了三样自创强化：路径编码生命周期、hash 冻结归档、取代审计。

### 3.3 12 个 skill 的分工

按功能分四组（均为上游 `.agents/skills/` 内容，`.claude/skills` 是它的 symlink，一套资产多平台复用）：

审查与质量——

- `dsh-code-review`：给 Agent 审查员的审查宪法。先跑 `change-scope` 命令算出 diff 影响面，再按 9 条 blocking requirements + 15 条 manual checks 审查。详见 3.4。
- `dsh-pre-push-checks`：最小相关证据原则。按 diff 影响面选最窄的检查，明令不默认跑全量测试、不重复跑已通过的检查（「CI 拥有穷举覆盖」）。覆盖率用 `--coverage.include` 精确圈定源文件范围。
- `dsh-find-simplifications`：把「找可简化点」变成证据驱动的提案。符号消费者先分级（生产代码 / 测试与文档 / 模糊区），只有无生产消费者的才算强候选；产出写 proposed Agent Note 而不是清单。

文档与文字——

- `dsh-doc-standards`：文档放置、tutorial/reference 分类、语料审计、字数预算红灯后的固定处理顺序（relocate → condense → raise）。
- `dsh-prose-standard`：通用写作标准，管一切 prose（注释、JSDoc、prompt、诊断、UI 文案）。核心规则「写到能保住命题为止，删掉推理转写、重复与装饰」；按位置列必写覆盖（公共 JSDoc 必须写 throws/副作用/所有权/取消时机；内部注释只写代码表达不了的契约）。
- `dsh-trim-cot-leakage`：专治「思维链泄漏」——视角留在写作会话里的文字。8 类缺陷模式：死引用（`design §4.7`）、stack/PR 视角、变更叙述（"used to"、"no longer"）、审查编排（"Rejected in review:"）、向审查者辩解、控制流叙述、对冲残留、创作语言混杂。唯一检验：「HEAD 上的读者没有会话记录，能否解析每个引用」。配套过度修正陷阱清单，防止删掉真事实。
- `dsh-translate-docs`（禁模型自行触发）：中英三文件对（`foo.md + foo.zh.md + foo.i18n.yaml` 记录双方 blob hash）；已有配对的更新走 briefing 驱动的最小编辑（纯代码块 diff 直接机拼，散文 diff 委派 subagent 且不重读规则库），新文档才整篇翻译且「编排 Agent 不自己翻」；术语表是双向合同。配套自定义 git merge driver 自动合并翻译配对冲突。
- `dsh-doc-site-sync`：仓库 Markdown 是唯一可编辑源，文档网站是被测试的投影，生成目录禁手改禁提交。

决策资产——

- `dsh-archive-agent-notes`：决策语料的整理流程，按未来决策价值分类，附校准示例（533 词的收进归档 vs 248 词的事件溯源架构保留）。

Git 流程——

- `dsh-merging-stacked-prs`：依赖 PR 栈的落地规程，强制 GitHub 原生 stack 对象，不满足条件直接 hard-stop，禁止手工模拟。
- `record-browser-gif`：录演示 GIF 的操作规程。

所有 skill 开头都有一句相同的免责：「It is guidance, not a script / not a checklist」——传判断标准，不假装穷尽。

### 3.4 审查流程与审查经济学

dsh 的审查是三级流水线：gate + review bot 机器初审 → 持有 `dsh-code-review` 宪法的 Agent 深审（语义层）→ 人类终审（架构取舍与 blocker）。针对「Agent 互审总能翻出新问题」的经济学约束写死在宪法里：

- **机器已证明的属性不许再审**：「Omit issues already enforced by a green gate」——问题池被制度性收窄到机器盲区。
- **一条有实据的 blocker 胜过一串 nit**：blocker 与 suggestion 强制分离。
- **按影响面审查**：先跑 `change-scope`，不做全仓库漫游。
- **回应纪律**：「verify each claim and fix or rebut it on technical grounds without performative agreement」——逐条技术性验证或反驳，禁止表演性认同。这条直接抑制「你提我也提」的找茬螺旋。
- 宪法自己也承认边界：开头声明 guidance not checklist；manual checks 里写明「coverage 不是场景正确的证据，不信任 Agent 自己的报告」「与 Agent Note 有分歧算设计讨论，不是自动否决」。

### 3.5 文档一致性：四层防线

针对「代码和文档不一致」：目录类文档（tool-catalog、config-catalog 等）从源码再生成、禁手改（漂移在源头不存在）；「one home per fact」+ `verify-md-links` gate（每个事实只有一个家，别处必须用机器可校验的链接，裸文件名直接红灯）；同 PR 强制同步（改了行为必须同 diff 更新 README/JSDoc，Agent Note 里的事实随代码同 PR 更新）；`verify-package-readme-limitations` 等 gate 复查。此外每个包 README 必须有 Model Experience 节，写明模型看到什么、token 与 KV cache 成本——进模型上下文的资产，其成本被当作契约记录。

### 3.6 协作痕迹中的其他发现

- **同任务双 Agent 竞争**：git 历史中同一任务（subagent 失败事实建模）同时存在 `codex/...-codex` 与 `codex/...-claude` 两个分支，之后由整合分支收口。用真实任务做 Agent 的 A/B 评测，人类 reviewer 选优合并。
- **分支命名即元数据**：`codex/*`（Codex 产出）、`worktree/*`（独立 worktree 开发）、`agent/*`。
- **过程留痕规范化**：skill 里规定了任务结束的汇报格式（做了什么、跑了哪些检查、留了哪些边界案例），使 Agent 会话的产出可审计。
- **本地实验的同构性**：本仓库作者按上游制度跑的 debate-room 实现会话，自动产出了结构化实施报告（目标、时间窗、gate 重试记录、token 用量、子 Agent 明细），证明制度本身即是充分的 Agent 指令。

## 4. 与 Noesis 的横向比较

先看一个贯穿性的结构差异：**Noesis 的约束住在两个地方**——仓库（`AGENTS.md`、openspec、仓库级 skill）和**用户个人记忆系统**（`~/.agents/skills` 的 diagnosing-bugs / code-review / code-simplification，以及记忆中的汇报纪律、验证纪律、根因纪律）。dsh 的约束**全部住在仓库里**。这意味着换一个没有用户记忆的 Agent 实例（例如直接用任意 Claude Code 会话打开 Noesis），约一半流程约束失效。这是比任何单条规则都重要的差距。

### 4.1 两边都有的流程

| 流程 | Noesis | dsh 多出的优点 |
|---|---|---|
| 分层 `AGENTS.md` | 根 + frontend/backend 三份 | 字数预算 gate；每条规则 1–3 行 + 链到唯一 rationale；文件自指修订说明 |
| 流程 skills | openspec-* 8 个 + 仓库级 3 个（code-quality-audit / run-trace-analysis / product-facing-copy-audit） | invocation policy；「read, don't re-summarize」指向契约原文；规定汇报格式；skill 间显式声明边界 |
| 决策记录 | `docs/NOTES.md` 决策卡片 + openspec `design.md`（变更期） | 同 PR 硬性交付；强制 Alternatives considered；supersession 审计；归档 hash 冻结。Noesis 的 NOTES.md 自由格式、无生命周期、无取代检查 |
| Bug / 根因沉淀 | `docs/bug/` 状态流转 + `docs/debugging/` 根因沉淀 | 精神一致（Noesis 的「没长期价值就删」甚至更干净），dsh 优点仅在格式 gate。**不需要抄** |
| Review | code-review skill（两轴：规范 + spec） | 审查经济学三条（机器已证明不许再提 / blocker 与 nit 分离 / 逐条反驳禁止表演性同意）；change-scope 影响面工具。Noesis 的两轴结构不输，缺的是经济学 |
| 提交前验证 | 固定清单（后端改动必跑 `uv run app.py`、合并前 pytest+lint） | 最小证据原则与反全量的经济学；Noesis 是固定清单，无经济学，Agent 容易偷懒或无脑全量两个极端 |
| 简化审计 | noesis-code-quality-audit（发现导向） | 证据分级（消费者分生产/测试/模糊区）+ 提案导向（产出写决策记录而非清单）+「决策记录不是金标准」的推翻条件 |

### 4.2 dsh 有、Noesis 没有的流程

按与 Noesis 痛点的相关度排序：

1. **机器执法层**。Noesis 的 CI 只有 pytest + lint + build，文档层零校验。dsh 有 60+ gate：md 链接校验、字数预算、Agent Note 格式、per-file 覆盖率、包不变量等，由 `scripts/run-gates.ts` 按依赖图编排。Noesis 的全部文档纪律目前靠 Agent 自觉 + 人肉审查。
2. **CoT 泄漏修剪**（`dsh-trim-cot-leakage`）。直接对应「过期注释影响判断」痛点：Agent 恰恰是最容易留下 "used to"、"rejected in review" 类文字的作者，dsh 把这类残留编成了 8 种可检索的缺陷模式加探针。Noesis 的注释和 architecture 文档没有这道防线。
3. **通用写作标准**（`dsh-prose-standard`）。Noesis 的 product-facing-copy-audit 只管 UI 文案一个面；没有覆盖注释/JSDoc/文档的写作契约。
4. **change-scope 影响面工具**。一条命令算出 diff 触及路径与「脏层」，审查和测试选择都以它为起点。
5. **生成式目录**。从源码再生成、禁手改。Noesis 的 `docs/engineering/` 全手写。
6. **同任务双 Agent 竞争分支**。一种用真实任务评测 Agent 的方式。
7. **Stacked PR 规程**、**双语配对流水线**。前者 Noesis 单人流程用不上；后者单语不适用（但其「hash 配对记录进 PR 作为可 review 的一致性声明」思想可迁移到其它一致性场景）。

## 5. 候选借鉴清单

按性价比排序，均为候选，未经拍板：

- **P0 — 补机器执法层（成本最低，直击文档漂移）**：写 `verify-md-links` 脚本进 CI（校验 docs/ 与 `AGENTS.md` 的相对链接与锚点，拒绝裸文件名引用），给根 `AGENTS.md` 定字数预算。小改动，可直接在 dev 上做。
- **P1 — 决策记录升级**：`NOTES.md` → 轻量决策记录体系。只借三个机制：生命周期路径编码、Alternatives considered 强制节（Noesis 历史上多个裁决被反复推翻，正是缺此节的代价）、supersession 审计。可与 openspec 归档流程接通：archive 时把 `design.md` 中有长期价值的 rationale 提炼进决策记录，而非随 change 沉没。
- **P2 — 审查经济学写进仓库**：`AGENTS.md` 协作约定或仓库级 review skill 增加三条：gate/CI 已证明的属性不许出现在 review 发现里；blocker 与 suggestion 强制分离；收到 review 逐条技术性验证或反驳、禁止表演性同意。
- **P3 — CoT 泄漏修剪 skill**：将 8 类分类学适配为 Noesis 版仓库 skill，配检索探针。`AGENTS.md` 开发原则已有「禁止叙述性注释」，但缺可检索的缺陷模式。
- **P4 — 约束仓库化**：把当前住在用户个人记忆里的纪律（根因汇报、交付前动态验证）沉淀进仓库。记忆约束的是「有记忆的 Agent」，仓库约束的是「任何 Agent」。

P1 / P2 属方向性新能力，落地时各自开 OpenSpec change（design 以第一性口吻自洽叙述，不引用本报告中的外部项目，证据出处反向链接到本文档）；P0 可直接 dev 提交。

## 6. 不采用方案

- **per-file 100% 覆盖率 gate**：dsh 以专职团队 + CI 矩阵支撑，对 Noesis 现阶段过重。
- **`./invariant` 伴生文件**：依赖 Cordis 式插件架构（每个包有运行时不变量注册点），Noesis 架构不同。
- **双语配对流水线**：Noesis 单语。
- **Stacked PR 规程**：Noesis 为单人 + Agent 开发，dev→main 单向流已够。
- **1453 篇规模的决策记录体系**：规模是团队行为的产物，Noesis 借机制即可，不追规模。

## 7. 待验证问题

- `verify-md-links` 在 Noesis 的中文文档 + 大量 mermaid/代码块场景下的误报率，需要实现后实测。
- 决策记录与 openspec 的接缝：归档提炼的粒度（整篇 `design.md` 摘要 vs 只留被否方案与长期 rationale）没有定论，P1 立项时需要试跑几份样本。
- 审查经济学三条对互审质量的实际改善效果，需要在 research-harness 等多 Agent 协作场景中观察（该场景尚未落地，见 openspec `super-agent-research-harness`）。
- dsh 制度的 token 成本：per-session 常驻 `AGENTS.md` 有预算，但 12 个 skill + 按需加载的隐性成本未见他们披露数字，Noesis 引入 P2/P3 后应关注。

## 8. 资料来源

- dsh 仓库本地 fork：`~/Desktop/code/deepseek-harness`，上游 `141eb6fef8`（0.1.0-rc.8），调研日期 2026-09-01。
- 制度层原文：`AGENTS.md`、`packages/AGENTS.md`、`docs/AGENTS.md`、`.agents/notes/README.md`、`.agents/skills/*/SKILL.md`、`scripts/run-gates.ts`、`lefthook.yml`、`knip.json`、`.github/workflows/`。
- git 历史：作者分布、`codex/*` 双分支、`ds-review-bot` 相关提交。
- 本地实验产物（非上游）：`debate-implementation-report.jsonl`、`feature-discussions/2026-08-20-dsh-debate-room/`、`packages/experimental/`。
- 结论中对「上游 vs 本地」的判断依据：`git ls-files` 跟踪状态 + `git log --follow` 空 结果。
