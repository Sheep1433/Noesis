# 决策：openspec archive 三个变更的 delta 修复（composer-session-tools / replace-jwt-with-server-sessions / converge-agent-sandbox）

状态：implemented
日期：2026-07-23
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**背景**：三个已实现完成的 change 因 delta spec 与 main spec header/内容不一致，`openspec archive <name> -y` 一直失败，长期挂在 `openspec/changes/`。

**openspec CLI（`@fission-ai/openspec` 1.3.1）合并机制要点**（源码见 `specs-apply.js` / `requirement-blocks.js`）：

- MODIFIED 按「header 精确文本」在 main 中查找旧 requirement 并整块替换（含全部子段落/Scenario），**不是**逐段 diff/merge；写回的 header 必须与查找 key **完全一致**——也就是说 **MODIFIED 不能用来改标题**，改标题必须拆成 `REMOVED`（main 现有精确 header）+ `ADDED`（新 header）。
- 校验器对 requirement 的「必须含 SHALL/MUST」「必须有 Scenario」检查，只看 header 后**第一段**正文（`requirement.text`），第二段及以后的正文会在 `openspec change show --json` 里被丢弃（但不影响 archive 时的 raw 文本合并，因为合并用的是原始文本块而非 parsed JSON）——排查 validate 报错时不要被 `--json --deltas-only` 的 `requirement.text` 截断误导。
- `openspec archive` 会对**整份 rebuilt main spec**做严格校验，会连带暴出与本次 delta 无关的历史遗留问题（如某 requirement 用裸列表当 Scenario、缺 SHALL），必须一起修掉才能通过。
- 无 dry-run 参数；调试用 `node --input-type=module -e "import {Validator} from '.../dist/core/validation/validator.js'; ...validateSpecContent(name, content)"` 直接对着手工拼好的 rebuilt 文本跑校验，比反复 archive 报错更快定位是哪个 requirement（报错的 `requirements.<N>` 是 0-based 的 requirement 序号，需配合 `grep -n "^### Requirement:"` 数序号）。

**处理方式**：
- `composer-session-tools`：requirement 标题已含 SHALL，但正文第一段不含 SHALL → 改写首段带上 SHALL；另有一条 MODIFIED header 文本对不上 main（对应 `agent-fault-operation` 的 MCP 加载描述）→ 改 header 对齐 main 精确文本并把新语义并入正文；顺带修了 `agent-common-qa` 里历史遗留的「会话 SHALL 持久化 kb_collections」缺 Scenario 问题。
- `replace-jwt-with-server-sessions`：delta 本身已不含 REMOVED 段、MODIFIED header 已对齐 main，直接 archive 通过，未做改动。
- `converge-agent-sandbox`（AIO → Docker Exec 收敛，改动最大）：`agent-sandbox`、`container-deployment`、`skills-filesystem` 三个 delta 里几乎所有 MODIFIED header 都是「新 docker-exec 语义标题」对不上 main 的「旧 AIO 语义标题」→ 统一改造为 REMOVED（main 精确旧标题 + Reason/Migration 说明）+ ADDED（delta 原有新标题与正文），只保留标题真正未变的两条（`沙箱环境与密钥`、`沙箱 idle 回收 SHALL 尊重 in-flight Agent`）走 MODIFIED；`agent-runtime-paths` delta 里两条 MODIFIED header 改回 main 精确标题即可（语义变化足够小，不需要拆 REMOVED/ADDED）。

**结果**：三者均已 `archive` 到 `openspec/changes/archive/2026-07-23-*`，main specs（`agent-sandbox`、`agent-runtime-paths`、`container-deployment`、`skills-filesystem`、`user-auth`、`composer-session-tools` 等）已反映实现现状（Cookie session 鉴权；docker-exec 沙箱非 AIO；Composer MCP/Skills 会话级配置）。仍有 3 个与本次无关的历史 spec 校验失败（`kb-chunking`、`kb-document-parse`、`kb-evaluation`），未处理，留给对应领域后续修。
