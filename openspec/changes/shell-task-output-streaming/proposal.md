# Proposal: 后台 shell 任务输出流式可读

## Why

后台 shell 任务（`execute` + `run_in_background`）的输出现状是「运行中零输出、终态只留尾部、完整输出丢弃」：

- shell kernel 在命令结束后一次性捕获输出（`shell/kernel.py:96`），运行期间模型与用户都拿不到任何进度信息（长命令只能看「仍在运行」盲等）；
- 终态仅保留 `result_tail`（末尾截断），头部信息（启动路径、前置日志）永久丢失。

对照调研（2026-09-23）：ZCode 后台 bash 输出流式写文件，运行中可读头部 30KB、`Read` 分块读，完成通知带文件路径；DSH 的 jobs 家族按输出契约分 stream（游标增量读）/final-output 两类，`job_output` 支持 wait 阻塞与增量读取。两家的共同点是**运行中输出渐进可读**——这是「后台任务」能力的基本盘，Noesis 缺失。

## What Changes

- **kernel 流式捕获**：`shell/kernel.py` 命令执行改为流式读取 stdout/stderr，追加进任务条目的环形缓冲（上限 64KB，丢头部保尾部）；终态行为不变（`result_tail` 照旧）。
- **运行中输出进 DB**：新增 `bg_shell_job.output_tail`（TEXT，nullable）+ Alembic migration；执行进程每 2s 将缓冲尾部 flush 到该列——web face（follower）经 DB 读取，不依赖执行进程内存。
- **模型侧**：`check_async_task` 对 running 状态的 shell 任务返回「仍在运行 + 输出尾部快照（4KB）」；终态行为不变。工具描述同步（运行中可查输出尾部）。
- **UI 侧**：任务面板 shell 详情在运行中展示 `output_tail` 实时日志（轮询既有 tasks 端点即可，无需新端点）。
- **不引入**：阻塞式取输出工具（通知优先原则）；DSH 式游标增量（尾部快照对模型足够，省去游标状态与重复读去重）；输出文件沙箱化（跨进程读沙箱文件需绕 runner，DB 列更直接）。

## Impact

- **数据库**：`bg_shell_job` 加 `output_tail` 列 + migration（纯加列，向前兼容）。
- **后端**：`agents/background/shell/kernel.py`（流式捕获 + 环形缓冲 + flush 循环）、`repositories/bg_shell_job_repository.py`（flush 写入）、`agents/background/subagent/tools.py`（running 快照返回）、任务清单端点投影（带 output_tail）。
- **前端**：`TaskListPanel` shell 详情运行中渲染 `output_tail`（等宽字体 + 自动滚动到底）。
- **风险**：2s flush 的写放大（单行 UPDATE，可忽略）；环形缓冲截断导致头部丢失仍存在（64KB 窗口内的 trade-off，与 ZCode 头读/尾读策略一致的取舍）。
