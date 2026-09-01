# 设计：约束仓库化

## Context

当前对 coding Agent 的约束分布（现状实测）：

- 仓库级：根 `AGENTS.md`、`frontend/backend/AGENTS.md`、openspec 主规格与变更、`.agents/skills/` 下 3 个 Noesis 专属 skill 与 openspec 工作流 skill。
- 用户级：`~/.agents/skills/` 下 19 个 skill，其中 `code-review`、`code-simplification`、`diagnosing-bugs` 三个被根 AGENTS.md「代码质量 Skills」表**强制引用**——即强制流程的实体住在仓库外。

问题：Agent 实例对约束的可见性取决于其运行环境配置；仓库是所有 Agent 实例共同可见的最低公约数，强制流程住在仓库外意味着它只对「恰好装了这三个 skill 的环境」生效。

三类实测缺陷：互审噪音（机器已证明的属性被反复提及、blocker 与 nit 混排、被审方表演性认同）；prose 会话视角残留（"之前"、"已被 review 否决"、向审查者辩解的注释）；设计决策散落（被否方案无处可查，同一裁决反复重新争论）。

外部方案比较与证据见 [docs/research/dsh-agent-collaboration.md](../../../docs/research/dsh-agent-collaboration.md)（调研记录）；本文档的设计推理自洽，不依赖该调研的内容。

**迁移对象的质量验证结论**（2026-09-01 实测全文审阅）：三个用户级 skill 内容质量均达到可保留标准——`diagnosing-bugs` 的可证伪假设与 red-capable 判据纪律完备（仓库外无对应替代）；`code-review` 的两轴结构正确且已含「工具已执法的跳过」雏形，但携带外部 skill 生态的死引用（`/setup-matt-pocock-skills`、`docs/agents/issue-tracker.md`），spec 源查找不认识 openspec；`code-simplification` 行为契约完整但形式过重（332 行、含 React 等非本仓库栈的多语言示例）。仓库级 `noesis-code-quality-audit` 与迁入两者职责不重叠（全库找债 / diff 把关 / 安全执行简化），全部保留。

## Goals / Non-Goals

**Goals:**

- 强制引用的开发纪律 skill 全部仓库归属，任何 Agent 实例打开 Noesis 即获得同一套约束。
- 每个 skill 单一归属：仓库有的，用户级不留副本。
- 补齐互审经济学、写作卫生两条语义防线，并为经济学提供可执行的影响面工具。
- 设计决策资产化：生命周期 + 被否方案强制记录 + 取代审计，与 openspec 归档接通。
- 最小机器执法：决策记录与文档链接的两类机械属性交 CI，不靠 Agent 自觉。
- AGENTS.md 保持索引角色。

**Non-Goals:**

- 不做生成式文档目录（tool-catalog 类，从源码再生成的参考）——收益依赖多包架构，Noesis 现阶段手写文档量可控。
- 不做 AGENTS.md 字数预算——当前无膨胀证据，出现膨胀再加。
- 不做决策记录的 hash 冻结归档与 append-only manifest——单人开发场景下格式校验 + git 历史已足够，机制留待需要时引入。
- 不迁移个人知识管理类 skill；不改任何产品代码、API、SSE 行为。

## Decisions

### D1. 迁移方式：move，非 copy 或改名 fork

三个 skill 整目录移动到 `.agents/skills/`，用户级删除。备选 copy（同名双份触发来源不定、修改必忘其一，与「禁止多套方案并行」冲突）与改名 fork（引用名与触发习惯作废而无行为收益）均否。接受代价：其他项目需要时各自声明，仍维持各仓库单一归属。

### D2. 保留自有 skill 骨架，迁入时修复验证发现的问题

质量验证支持保留自有版本；改造限于四类：

- **死引用清理**：code-review 移除 `/setup-matt-pocock-skills` 与 `docs/agents/issue-tracker.md` 依赖；spec 源查找第一优先级改为 openspec（change 名来自 commit/分支名或用户指定，其次才是散置文档）；diagnosing-bugs 的 `CONTEXT.md`/ADR 引用改指 `docs/architecture/` 与 `docs/debugging/`（`hitl-loop.template.sh` 随迁保留，标注为 skill 自带文件）。
- **瘦身**：code-simplification 示例压缩到 TS + Python（Noesis 栈），React/JSX 节删除；行为契约（输入/输出/异常/副作用顺序不变）不动。
- **Sources of truth 节**：链接根 AGENTS.md、`docs/architecture/`、openspec specs，不复述内容——skill 与仓库文档的双份陈述必然漂移，指向则不会。
- **职责边界声明**：code-review（diff 把关）/ code-simplification（安全执行简化）/ noesis-code-quality-audit（全库找债）三方在各自开头声明边界，防触发混淆。

### D3. 审查经济学并入 `code-review`，不单独开 skill

互审噪音的约束是 review 行为本身的纪律，与两轴结构属同一能力面：

1. CI / lint / 已通过 gate 证明过的属性，不得作为 review 发现提出——问题池收窄到机器盲区。
2. blocker 与 suggestion 强制分离；一条有实据的 blocker 优于一串吹毛求疵。
3. 收到 review 意见逐条技术性验证或反驳，禁止表演性认同。

同时挂接 Noesis 高关注区（SSE 持久化、Qdrant 异常、配置硬编码、密钥、MCP 远程执行，清单来源即根 AGENTS.md，引用不复制）。

### D4. 写作卫生独立为新 skill `noesis-prose-hygiene`

与 code-review 分开的理由：触发场景不同（写作时与语料审计时，而非 review 时）；覆盖面不同（注释、JSDoc、docs、openspec artifacts 的 prose）。核心设计：

- **唯一检验**：只能看到当前仓库的读者（无会话记录、PR 讨论、未提交草稿），能否解析其中每个引用、验证每个主张？不能，则改写为以仓库为视角的陈述或删除。
- **缺陷分类**（第一性推导：凡只有产生这段文字的那次会话才能解析的视角都是残留）：死引用、变更叙述（"之前是 X"）、审查编排（"review 中被否决"）、向审查者辩解（注释应陈述不变量，不是辩护词）、对冲残留（"暂时这样应该够用"）、控制流复述。分类给中文检索示例。
- **按位置必写覆盖底线**：公共 API 文档必须写异常/副作用/所有权/取消时机；内部注释只写代码表达不了的契约。
- **过度修正陷阱**：命题保真优先——删除前枚举事实性从句，删的只能是装饰与转写；`TODO(name):`、lint 抑制理由等合法标记明确排除。
- **报告制**：默认报告发现与建议改写，不自动改写（误报代价高于漏报）。

### D5. 影响面工具 `scripts/change-scope`：经济学的可执行前提

「CI 已证明的属性禁提」需要知道哪些检查覆盖哪些路径，「按影响面审查/选最小证据」需要先算出影响面——没有工具，D3 的三条是口号。设计为最小自实现（约百行 Python，无外部依赖）：

- 输入：base ref（默认 dev；显式传入优先，**不猜测**——错误 base 的影响面比没有影响面更危险）。
- 输出：committed 路径集（`base...HEAD`）、工作区脏路径（staged/unstaged/untracked）、按层归类（backend / frontend / docs / openspec / scripts / deploy）、每层的 owning checks 映射（backend → `uv run pytest tests/api_contract` + 启动冒烟；frontend → `pnpm lint` / `pnpm build`；docs/openspec → 文档执法 gate）。
- 定位为**参考信息**而非执法：Agent 与人都以它为审查和选检查的起点，覆盖不到的由人补；不阻塞任何流程。

### D6. 决策记录体系 `docs/decisions/`

- **路径编码生命周期**：`{proposed|implemented|rejected}/YYYY-MM-DD-主题.md`，文件名日期为首次提出日。
- **文件格式**（最小骨架，格式由脚本校验）：`# 决策：<标题>` + `状态：<proposed | implemented | rejected — 一行理由>` + `日期：`，正文从 `## 问题`（不依赖方案可独立理解）开始，implemented 必含 `## 备选方案`（否了什么、为什么输——没有这一节，同一裁决会被反复重新争论）。
- **同提交交付**：非平凡改动（改变行为/架构/跨文件契约/流程）在同一提交中新增或更新决策记录；「实现一个 proposed 记录」的改动把该记录改写为 implemented 现在时态并核实路径与机制。
- **取代审计**：新记录落档时检查同主题旧记录；完全取代则把旧记录的独特 rationale 并入新记录后删除旧记录并修复入链，部分取代则双向交叉链接。
- **openspec 接缝**：`openspec-archive-change` skill 增加一步——归档时把 change 的 design.md 中有长期价值的决策依据与被否方案提炼为 implemented 记录（openspec 归档件是变更期文档，随时间失去约束力；决策记录是长期权威）。
- **NOTES.md 迁移**：既有决策卡片按日期标题机械拆分迁入 `implemented/`（一张卡一文件）；DeepDoc vendor 修改记录不是决策，归入 `docs/engineering/`。迁移是机械操作，不改写内容。

### D7. 最小执法层：两个脚本进 CI

- `scripts/verify_md_links.py`：校验 `docs/**`、`AGENTS.md`、`openspec/changes/**`（活跃变更）内相对链接目标存在、锚点可达，拒绝裸文件名引用（不可解析即无法导航）。同时执法 D2 的指向关系——skill 与决策记录内的链接失效即红灯。
- `scripts/verify_decision_format.py`：校验头部三行格式、Status 与所在生命周期目录一致、implemented 含备选方案节。
- CI 接线：`.github/workflows/ci.yml` 新增独立 job（仅 Python 标准库，秒级）。设计原则：**能机械检查的属性不依赖 Agent 自觉，也不进人肉 review**。

### D8. AGENTS.md 修订保持索引角色

代码质量表声明仓库归属 + 单一归属规则；协作约定加一行审查经济学引用与一行决策记录规则（非平凡改动同提交附记录）；开发验证节引用 change-scope。不在 AGENTS.md 内展开任何 skill 或体系内容——常驻上下文每个字都进每个会话，展开手册等于对所有无关任务收税。

### D9. skill frontmatter 维持工具链兼容子集

仅 `name` + `description`。不引入当前工具链不识别的调用策略字段——不被识别的字段提供的是虚假安全感；工具链支持后再扩展。

## Risks / Trade-offs

- [其他项目失去这三个通用 skill] → 接受（当前主要开发在 Noesis）；需要时在目标项目声明，维持各仓库单一归属。
- [用户级未来新装同名 skill 形成遮蔽] → 单一归属规则写入 AGENTS.md，冲突时以仓库版本为准。
- [中文检索示例误报] → noesis-prose-hygiene 报告制不自动改写；示例从保守集起步，实测后扩充。
- [change-scope 分层误判（如一个文件跨多层）] → 分层规则按一级目录前缀，跨层文件按全部命中层输出 owning checks 的并集；定位为参考信息，人可覆盖。
- [NOTES.md 拆分迁移的改动噪音] → 机械拆分单独一个提交，不与机制引入混在同一 diff；内容零改写便于 review。
- [决策记录成为负担后 Agent 敷衍填写] → 备选方案节的校验只查存在不查质量，质量由 code-review 语义把关；同时「更新既有记录即满足规则」防止记录增殖。
- [迁移后 skill 触发路径变化] → 全新会话实测三个 skill 触发与加载（ZCode 对 `.agents/skills/` 的发现已被现有仓库 skill 证实）。

## Migration Plan

纯文件移动 + 新增文件 + CI 接线，无运行时影响。步骤见 tasks.md；回滚 = git revert（skill 移回用户级 + 还原 AGENTS.md + 移除脚本与 CI job）。

## Open Questions

- noesis-prose-hygiene 的中文检索示例初版覆盖范围——实现时从 Noesis 仓库现有注释与 docs 实测采样校准，不凭空枚举。
- `code-review` 的高关注区挂接在清单变长时是否拆 `references/` 子文件——初版内联。
- change-scope 的 owning checks 映射初版按 CI 现状（pytest/lint/build）起步，后续随测试分层（api_contract / integration）细化。
