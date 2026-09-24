# Tasks: 后台 shell 任务输出流式可读

## 1. kernel 流式捕获

- [x] 1.1 `agents/background/shell/kernel.py`：命令执行改为流式读取（asyncio 子进程逐块读 stdout/stderr 合并流），追加进任务条目环形缓冲（`collections.deque` 上限 64KB）；终态 `result_tail` 语义不变（从全量输出取尾，兼容旧断言）
- [x] 1.2 flush 循环：running 期间每 2s 将环形缓冲尾部（≤4KB）UPDATE 到 `bg_shell_job.output_tail`；终态 flush 一次后停止；flush 失败仅告警（输出可读性降级，不中断命令）
- [x] 1.3 `repositories/bg_shell_job_repository.py`：新增 `update_output_tail(task_id, tail)`；`get_task` / `list_for_session` 返回体带 `output_tail`
- [x] 1.4 Alembic migration：`bg_shell_job` 加 `output_tail TEXT NULL`

## 2. 模型与 UI 消费

- [x] 2.1 `agents/background/subagent/tools.py`：`check_async_task` 对 running shell 任务在 hint 后附输出尾部快照（≤4KB，注明「运行中最新输出」）；工具描述补「运行中可查输出尾部」
- [x] 2.2 任务清单端点投影（`executor.list_with_fallback` 的 shell 分支 + SessionTaskService）返回体带 `output_tail`
- [x] 2.3 前端 `TaskListPanel` shell 详情：运行中渲染 `output_tail`（pre 等宽 + 自动滚动到底），终态切换为 result 展示
- [x] 2.4 单测：环形缓冲截断边界、flush 周期与终态 flush、running 快照返回、投影带 output_tail

## 3. 验收

- [x] 3.1 行为验证：派长输出命令（如 `for i in $(seq 1 60); do echo line-$i; sleep 1; done` 后台执行），运行中 check_async_task 能看到 line-N 推进、任务面板实时滚动、终态 result_tail 正常
- [x] 3.2 web face（follower）验证：任务面板在无 executor 的进程上同样能看到 output_tail（经 DB）
