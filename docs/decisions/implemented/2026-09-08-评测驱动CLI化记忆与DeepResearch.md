# 决策：记忆与 DeepResearch 评测改为 noesis CLI 子进程驱动

状态：implemented
日期：2026-09-08

## 问题

记忆与 DeepResearch 两条评测线通过 `evals/agent/_agent.py` 在评测进程内直接构造
SuperAgent，等于维护了第二条平台集成路径：评测自己建数据库会话行、自己管模型
快照绑定、自己清沙箱容器。平台每次演进它就碎一次——2026-09-08 单日实跑暴露
五类故障：`t_chat_session.user_id` 迁移为 UUID 列后字符串评测用户被拒、session id
超 VARCHAR(36) 被截断、全局 asyncpg 池绑定首个事件循环导致跨样本崩溃（建会话、
沙箱清理、agent 的 search_sessions 工具三处分别炸过）、会话沙箱 20 副本上限被
逐样本堆积、网关 402 欠费被错误中间件转成「正常空完成」落盘为有效数据。根因
不是数据集也不是指标，是「评测进程 import 平台零件重新拼装」这个架构。

## 决策

被测对象收敛为 `packages/noesis-cli`（本地 harness CLI，职责边界见
`docs/decisions/archived/2026-08-08-平台层Harness-与本地-CLI-的职责边界.md`），
评测与被测之间只隔 **argv + 环境变量 + stream-json 输出**（harbor/terminal-bench
同构契约）：

- CLI 扩展为 Claude Code 风格：`noesis chat -p "<问题>" --model <模型>
  --output-format text|json|stream-json`；`-p` 模式跑完即退、非零退出码即失败，
  会话行落库为默认行为（子 Agent 派发需要血缘），空收场（零文本零工具却报完成）
  判失败。被测模型凭据走 env 直连：`NOESIS_API_KEY` + `NOESIS_BASE_URL`
  （`evals/.env`，gitignored），跳过目录与用户模型解析。
- 新增 `evals/agent/cli_driver.py`：每题 spawn 一个全新 CLI 子进程（跨样本无共享
  事件循环/连接池/全局状态），解析 stream-json，持有 wall-clock 超时——杀进程
  组但保留已收到的部分结果（超时题仍可判卷）。
- `SANDBOX_BACKEND=local_shell` 由 driver 注入：评测不产生 runner 沙箱容器，
  逐样本清理逻辑整体删除。与 docker 沙箱生产语义存在已知偏差（工具本地执行），
  记忆/deepresearch 两线的工具面（read_file/检索/web）不受影响。
- 子 Agent 前台等待窗口从 deepresearch 的进程内 monkeypatch 转正为
  `SUBAGENT_FOREGROUND_MAX_WAIT_SECONDS` / `SUBAGENT_TASK_TIMEOUT_SECONDS` env。
- 记忆线判卷/汇总/续跑、deepresearch 断点续跑与产物结构不变；judge 留在宿主机
  侧单次 LLM 调用，沿用用户模型绑定。`evals/agent/_agent.py`（已无引用）删除。

压缩评测线不迁移：三组对照对 CompactionMiddleware 参数控制最深，维持 in-process。

## 备选方案

- **SSE 黑盒（打真实后端 HTTP）**：否——既有决策（2026-08-08 职责边界）明确
  不为评测复制一套 HTTP 层，且压缩线需要导入前注入触发阈值等参数控制，HTTP
  面不暴露这些旋钮。
- **维持 in-process、只修今天的故障**：否——修完五类故障后路径仍在，下一次
  schema 迁移或生命周期变更会再次静默烂掉（baseline 跑完后 UUID 迁移导致评测
  从「能跑」变「不能跑」，期间无人知晓，即为此模式的实测代价）。
- **容器内挂 docker socket 保持沙箱生产语义**：否——沙箱容器是兄弟容器，评测
  容器销毁不带走它们，泄漏与清理问题原样回来。

## 代价

- 每题一次子进程启动（秒级，相对每题分钟级的 agent 运行可忽略）。
- `local_shell` 使评测环境与生产沙箱语义有一处已知偏差，在 driver 注释与本
  记录明示；未来若评测需要覆盖沙箱内工具行为，需单独评估。
- CLI 子进程内不接 Langfuse 过程追踪（此前 in-process 经 `eval_langfuse_run`
  挂接）；评测结果与判卷记录仍完整落盘 raw.jsonl/manifest。

## 验证

- CLI 契约：`noesis chat -p ... --output-format stream-json/json` 实跑直连模型
  出正确答案、退出码语义正确；空收场守卫对 402 批次历史数据回放 8/8 命中。
- 记忆线：smoke 8/8 完成（行为级召回 100%，工具入参与 `/memory` 直读路径完整
  采集）；LongMemEval 单题正例 judge accepted、recall@k=1.0、配对负例零误召回；
  driver 超时路径实测杀进程组且保留部分结果。
- DeepResearch：`--limit 1` 经 CLI 子进程出报告（见 tag `cli-dr-smoke`）。
- `uv run pytest tests/test_noesis_cli_streamjson.py tests/test_eval_agent_memory.py
  tests/test_eval_agent_runtime.py tests/test_config_distributed_runs.py -q`：36 passed；
  改动文件 ruff 干净；`verify-md-links.py` 通过。
