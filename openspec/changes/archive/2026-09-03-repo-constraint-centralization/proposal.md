# 约束仓库化：开发纪律 skills 迁入、决策记录体系与最小执法层

## Why

Noesis 对 coding Agent 的开发纪律约束目前分裂在两处：仓库内的 AGENTS.md / openspec / 3 个仓库级 skill，和用户个人目录 `~/.agents/skills/` 下的 skill。AGENTS.md「代码质量 Skills」表强制要求的三个 skill（code-review、code-simplification、diagnosing-bugs）全部住在用户级目录——任何一个没有该用户配置的 Agent 实例打开 Noesis 时，这些强制流程直接失效。约束应该住在仓库里，因为仓库是所有 Agent 实例共同可见的最低公约数。

同时，实测开发中暴露的三类问题在现有体系内没有防线：一是 Agent 互审总能翻出大量低价值问题（机器已证明的属性被反复提及、blocker 与 nit 混排、被审方表演性认同）；二是注释与文档中的「会话视角残留」（"之前"、"已被 review 否决"、向审查者辩解的文字）随时间积累为过期注释；三是设计决策散落在 commit message、openspec 归档件与个人记忆中，被否掉的方案无处可查，同一裁决反复被重新争论。背景证据与外部方案比较见 `docs/research/dsh-agent-collaboration.md`。

## What Changes

- **迁入三个开发纪律 skill**：`code-review`、`code-simplification`、`diagnosing-bugs` 从 `~/.agents/skills/` 迁移（move，非 copy）到仓库 `.agents/skills/`，用户级副本删除，单一归属。迁入时修复经质量验证发现的问题：code-review 的外部生态死引用（`/setup-matt-pocock-skills`、`docs/agents/issue-tracker.md`）与 spec 源查找对 openspec 的适配；diagnosing-bugs 的 `CONTEXT.md`/ADR 引用改指向仓库既有文档（`hitl-loop.template.sh` 随 skill 携带，非死引用）；code-simplification 的代码示例压缩到 Noesis 技术栈（TS + Python）。
- **迁入时做 Noesis 化改造**：`code-review` 并入审查经济学三条（CI/gate 已证明的属性不得作为发现；blocker 与 suggestion 分离；逐条技术性回应禁止表演性认同）并挂接高关注区；三个 skill 各补「Sources of truth」节，链接仓库权威文档而不复述。
- **新建写作卫生 skill**（`noesis-prose-hygiene`）：会话视角残留的缺陷分类与「仓库读者可独立解析」唯一检验，报告制执行。
- **新建轻量影响面工具**（`scripts/change-scope`）：给定 base ref 输出改动路径集、分层归类与各层 owning checks 映射——使审查经济学与最小证据原则可执行，而非口号。
- **新建决策记录体系**（`docs/decisions/`）：生命周期目录（proposed/implemented/rejected）+ 强制备选方案节 + 取代审计；`docs/NOTES.md` 既有决策卡片机械拆分迁入；openspec 归档流程增加「design.md 长期 rationale 提炼为决策记录」接缝。
- **最小机器执法层**：md 链接校验（校验 docs/、AGENTS.md、决策记录内的相对链接与锚点）与决策记录格式校验两个脚本进 CI。
- **修订 AGENTS.md**：代码质量表声明仓库归属与单一归属规则；协作约定并入审查经济学引用与决策记录规则（非平凡改动同提交附决策记录）；开发验证节引用 change-scope。保持索引角色，不内联 skill 内容。
- **不迁移**：个人知识管理与生活类 skill 留在用户级。

## Capabilities

### New Capabilities

- `repo-collaboration`: 仓库对 coding Agent 的协作约束体系——开发纪律 skill 的仓库归属与单一来源、审查经济学纪律、写作卫生、影响面工具、决策记录生命周期、最小文档执法。

### Modified Capabilities

（无——本变更不触及任何产品功能 spec 的 requirement。openspec-archive-change skill 的行为增强以任务形式落地，属工具流程调整。）

## Impact

- `.agents/skills/`：新增 4 个 skill 目录（3 迁移 + 1 新建）；`openspec-archive-change` skill 增加提炼步骤。
- `~/.agents/skills/`：删除 code-review、code-simplification、diagnosing-bugs 三个目录。
- `scripts/`：新增 change-scope、verify-md-links、verify-decision-format 三个脚本。
- `.github/workflows/ci.yml`：新增文档执法 job。
- `docs/`：新增 `decisions/` 目录；`NOTES.md` 决策卡片拆分迁入后按内容分流（决策进 decisions/，DeepDoc vendor 修改记录归 engineering/）。
- `AGENTS.md`：代码质量表、协作约定、开发验证节修订。
- 无产品代码、API、SSE、依赖变更；对运行时零影响。
- 迁移后用户在其他项目使用这三个通用工程 skill 时需自行声明（接受此代价，当前主要开发工作在 Noesis）。
