# repo-collaboration Specification

## Purpose
本能力规定仓库协作与工程执法层：开发纪律 skill 的仓库归属与单一来源、审查经济学纪律、影响面工具、写作卫生防线、决策记录生命周期与机械校验、文档链接校验，以及根 AGENTS.md 的一行索引角色。目标是对任何 Agent 实例提供不依赖个人配置的协作约束，且执法属性交给 CI gate 而非人工记忆。

## Requirements

### Requirement: 开发纪律 skill 仓库归属

被根 AGENTS.md 强制引用的开发纪律 skill（code-review、code-simplification、diagnosing-bugs）SHALL 存放于仓库 `.agents/skills/` 目录，且用户级 skill 目录（`~/.agents/skills/`）SHALL NOT 保留其同名副本（单一归属）。迁入版本 MUST NOT 保留指向仓库外 skill 生态或不存在的文件的引用。

#### Scenario: 任意 Agent 实例可见强制流程
- **WHEN** 一个未加载任何用户级 skill 配置的 coding Agent 实例打开 Noesis 仓库并读取根 AGENTS.md
- **THEN** 其可从 `.agents/skills/` 加载「代码质量 Skills」表强制引用的全部 skill，且 skill 内容不依赖仓库外文件

#### Scenario: 单一归属不漂移
- **WHEN** 任一已迁入仓库的开发纪律 skill 被修改
- **THEN** 修改只发生在仓库版本上，且用户级目录不存在同名 skill 提供第二份内容

### Requirement: 审查经济学纪律

仓库的 code-review skill SHALL 包含三条审查经济学约束：其一，已被 CI / lint / 通过的 gate 证明的属性 MUST NOT 作为 review 发现提出；其二，review 报告 MUST 将 blocker 与 suggestion 分离呈现；其三，skill MUST 指示对收到的 review 意见逐条技术性验证或反驳，MUST NOT 以未经验证的认同回应。

#### Scenario: 机器已证明的属性不进 review 发现
- **WHEN** Agent 审查一个 diff，且该 diff 的 lint、类型检查、相关测试已在 CI 中通过
- **THEN** review 报告不出现「lint 应通过」「类型应正确」类发现，仅报告机器检查无法覆盖的语义问题

#### Scenario: blocker 与 suggestion 分离
- **WHEN** Agent 产出一份 review 报告
- **THEN** 阻断性问题与建议性问题分别列出，且每条 blocker 附带位置、影响与证据

#### Scenario: 被审方逐条回应
- **WHEN** Agent 收到针对其改动的 review 意见
- **THEN** 其对每条意见给出技术性验证结果（采纳并修复，或基于技术理由反驳），不出现未经验证的认同表述

### Requirement: 影响面工具

仓库 SHALL 提供 `scripts/change-scope`，输入显式 base ref（默认 dev），输出改动路径集（committed 与工作区脏路径）、按层（backend / frontend / docs / openspec / scripts / deploy）的归类结果、以及各层的 owning checks 映射。工具 SHALL NOT 在未显式提供 base 时猜测比较基准。

#### Scenario: 审查以影响面为起点
- **WHEN** Agent 或人类对一个待审 diff 运行 change-scope 并传入 base ref
- **THEN** 输出列出全部改动路径及其层归属，并为每层给出该层 owning checks

#### Scenario: 不猜测 base
- **WHEN** 调用方未显式提供 base ref
- **THEN** 工具使用默认基准（dev）并在输出中显式声明所用的基准，供使用者核对

### Requirement: 写作卫生防线

仓库 SHALL 提供写作卫生 skill（noesis-prose-hygiene），定义会话视角残留的缺陷分类、以「仓库读者可独立解析」为唯一检验标准，覆盖注释、JSDoc、docs 与 openspec artifacts 的 prose。该 skill SHALL 采用报告制执行：默认报告发现与建议改写，MUST NOT 未经确认自动改写目标文件。

#### Scenario: 会话视角残留被识别
- **WHEN** 一段注释或文档包含只有产生它的会话才能解析的内容（如指向不存在的讨论稿编号、以"之前/后来改成"叙述仓库历史、向审查者辩解的文字）
- **THEN** skill 将其标记为会话视角残留，并给出以仓库当前状态为视角的改写建议

#### Scenario: 命题保真优先于删除
- **WHEN** 一段被标记的残留文字中包含事实性从句（契约、边界、失败模式）
- **THEN** 改写建议保留全部事实性从句，仅删除或改写视角残留部分，且不触碰 `TODO`、lint 抑制理由等合法标记

#### Scenario: 报告制不自动改写
- **WHEN** 用户要求审计某个范围的 prose 卫生
- **THEN** skill 输出发现清单与建议改写，目标文件保持未修改，直至用户确认

### Requirement: 决策记录生命周期

仓库 SHALL 提供决策记录体系 `docs/decisions/`，路径编码生命周期（`proposed/`、`implemented/`、`rejected/`）。每条记录 MUST 含 `## 问题` 节（不依赖方案可独立理解）；implemented 记录 MUST 含 `## 备选方案` 节（记录被否方案及落选理由）；rejected 记录的状态行 MUST 含一行落选理由。

#### Scenario: 非平凡改动同提交交付决策记录
- **WHEN** 一次提交改变了行为、架构、跨文件契约或开发流程
- **THEN** 该提交新增或更新至少一条决策记录；实现 proposed 记录的提交将该记录改写为 implemented 现在时态并核实其路径与机制

#### Scenario: 取代审计
- **WHEN** 一条新决策记录落档且存在同主题旧记录
- **THEN** 完全取代时旧记录的独特依据并入新记录后删除并修复入链；部分取代时两条记录双向交叉链接

#### Scenario: openspec 归档提炼
- **WHEN** 一个 openspec change 完成归档
- **THEN** 归档流程把该 change 的 design.md 中具有长期价值的决策依据与被否方案提炼为一条 implemented 决策记录

### Requirement: 决策记录机械校验

CI SHALL 运行决策记录格式校验：头部格式、状态值与所在生命周期目录一致、implemented 记录含备选方案节。校验 MUST NOT 检查内容质量（质量由语义 review 把关）。

#### Scenario: 格式违规被拦截
- **WHEN** 一条 implemented 记录缺少备选方案节，或其状态行与所在目录不符
- **THEN** CI 校验失败并指出具体文件与违规项

### Requirement: 文档链接校验

CI SHALL 运行 md 链接校验，覆盖 `docs/`、根与子树 `AGENTS.md`、活跃 openspec changes、决策记录：相对链接目标 MUST 存在、锚点 MUST 可达；裸文件名引用（不可解析为路径的引用方式）MUST 被拒绝。

#### Scenario: 死链被拦截
- **WHEN** 一篇文档新增指向不存在文件或不存在锚点的链接
- **THEN** CI 校验失败并指出链接所在文件与目标

### Requirement: skill 与仓库权威文档的指向关系

迁入与新建的 skill SHALL 以链接指向仓库权威文档（根 AGENTS.md、`docs/engineering/`、openspec specs）作为展开规则的来源，MUST NOT 在 skill 内复述这些文档的规则内容。

#### Scenario: skill 引用而非复制
- **WHEN** AGENTS.md 的某个被 skill 引用的规则发生修改
- **THEN** skill 无需同步修改，其读者经由链接获得的始终是权威文档的当前内容

### Requirement: AGENTS.md 索引角色

根 AGENTS.md 的「代码质量 Skills」表 SHALL 声明各 skill 的仓库归属，并 SHALL 包含单一归属规则；协作约定 SHALL 包含审查经济学与决策记录规则的一行引用（指向对应 skill 与目录）；AGENTS.md SHALL NOT 内联展开任何 skill 或体系的操作内容。

#### Scenario: 常驻上下文不膨胀
- **WHEN** 某个 skill 的操作内容增加或修订
- **THEN** 根 AGENTS.md 对该 skill 的引用保持一行索引不变，仅 skill 文件本身变化
