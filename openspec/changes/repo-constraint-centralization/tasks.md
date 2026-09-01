# 任务清单

## 1. Skill 迁移与修复（move，非 copy）

- [ ] 1.1 将 `~/.agents/skills/code-review/` 整目录移动到 `.agents/skills/code-review/`
- [ ] 1.2 将 `~/.agents/skills/code-simplification/` 整目录移动到 `.agents/skills/code-simplification/`
- [ ] 1.3 将 `~/.agents/skills/diagnosing-bugs/` 整目录移动到 `.agents/skills/diagnosing-bugs/`
- [ ] 1.4 确认用户级目录下三个同名 skill 已删除，无残留副本
- [ ] 1.5 code-review 清理外部生态死引用：移除 `/setup-matt-pocock-skills`、`docs/agents/issue-tracker.md`、`.scratch/` 依赖；spec 源查找第一优先级改为 openspec change（change 名取自 commit/分支/用户指定）
- [ ] 1.6 diagnosing-bugs 将 `CONTEXT.md` 与 `scripts/hitl-loop.template.sh` 引用改为指向 `docs/architecture/` 与 `docs/debugging/`
- [ ] 1.7 code-simplification 示例压缩到 TS + Python，删除 React/JSX 节；行为契约（输入/输出/异常/副作用顺序不变）零改动

## 2. 迁入 skill 的 Noesis 化改造

- [ ] 2.1 三个 skill 各补「Sources of truth」节：链接根 AGENTS.md 协作约定、`docs/architecture/` 相关页、openspec specs，不复述其内容
- [ ] 2.2 `code-review` 并入审查经济学三条（CI/gate 已证明属性禁提、blocker/suggestion 分离、逐条技术性回应禁止表演性认同），并链接高关注区清单（SSE 持久化、Qdrant 异常、配置硬编码、密钥、MCP 远程执行）
- [ ] 2.3 三个 skill 与 `noesis-code-quality-audit` 在各自开头声明职责边界（diff 把关 / 安全执行简化 / 全库找债）
- [ ] 2.4 核对 `code-simplification` 与 `diagnosing-bugs` 行为契约未被改动（简化不改行为顺序；诊断先稳定反馈再定位根因、禁止先加 fallback）

## 3. 新建写作卫生 skill

- [ ] 3.1 从 Noesis 仓库现有注释、`docs/`、openspec archived changes 采样会话视角残留实例，校准中文检索示例初版（"之前/原先/后来改成/上述方案被否/见讨论稿"等保守集）
- [ ] 3.2 编写 `.agents/skills/noesis-prose-hygiene/SKILL.md`：唯一检验标准（仓库读者可独立解析）、缺陷分类（死引用/变更叙述/审查编排/辩解性注释/对冲残留/控制流复述）、按位置必写覆盖底线、命题保真与过度修正陷阱、报告制执行模式
- [ ] 3.3 用 3.1 采样实例验证 skill 内容：每个采样残留都能被分类与检验标准覆盖，合法标记（TODO、lint 抑制理由）被明确排除

## 4. 决策记录体系

- [ ] 4.1 创建 `docs/decisions/{proposed,implemented,rejected}/` 目录结构与 README（格式骨架、生命周期规则、取代审计规则）
- [ ] 4.2 `docs/NOTES.md` 决策卡片按日期标题机械拆分迁入 `implemented/`（一卡一文件，内容零改写，独立提交）；DeepDoc vendor 修改记录迁至 `docs/engineering/`
- [ ] 4.3 `openspec-archive-change` skill（及对应 source-command 副本）增加归档提炼步骤：design.md 长期 rationale 与被否方案提炼为 implemented 决策记录
- [ ] 4.4 补一条本变更自身的 implemented 决策记录（作为体系首条，验证格式与流程）

## 5. 影响面工具与最小执法层

- [ ] 5.1 实现 `scripts/change-scope.py`：显式 base ref（默认 dev 且输出中声明）、committed + 脏路径集、层归类（backend/frontend/docs/openspec/scripts/deploy）、各层 owning checks 映射；纯标准库
- [ ] 5.2 实现 `scripts/verify_md_links.py`：校验 `docs/**`、`AGENTS.md`（根与子树）、活跃 openspec changes、`docs/decisions/**` 的相对链接与锚点，拒绝裸文件名引用；先修复存量死链再接 CI
- [ ] 5.3 实现 `scripts/verify_decision_format.py`：头部三行格式、Status 与生命周期目录一致、implemented 含备选方案节
- [ ] 5.4 `.github/workflows/ci.yml` 新增文档执法 job，接入 5.2 / 5.3 两个脚本

## 6. AGENTS.md 修订

- [ ] 6.1 「代码质量 Skills」表更新：声明 skill 仓库归属，加入单一归属规则（仓库 skill 禁止在用户级保留同名副本）
- [ ] 6.2 「协作约定」节加两行引用：审查经济学三条（指向 code-review skill）；决策记录规则——非平凡改动同提交附决策记录（指向 `docs/decisions/`）
- [ ] 6.3 「开发验证」节引用 `scripts/change-scope` 作为选检查的起点
- [ ] 6.4 确认根 AGENTS.md 未内联任何 skill 操作内容，引用保持一行索引

## 7. 验证

- [ ] 7.1 全新 Agent 会话（无用户级依赖）实测三个迁移 skill 可被发现并按 description 触发
- [ ] 7.2 实测 `noesis-prose-hygiene` 对一段含残留的样例文本输出报告制结果（只报告不修改）
- [ ] 7.3 用审查经济学三条 + change-scope 跑一次真实 diff 的 code-review，确认报告结构符合 blocker/suggestion 分离且不含 CI 已证明项
- [ ] 7.4 对本变更自身的 diff 运行 change-scope 与三个校验脚本，全部通过（吃自己的狗粮）
