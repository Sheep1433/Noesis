# 手动验收脚本（super-agent-async-tasks）

> 按顺序执行；每步标注「预期」。后端 `cd backend && uv run app.py`，前端
> `pnpm build && pnpm preview`（或 dev）。qa_type 切到 SuperAgent 模式。

## 1. 后台委派 + 继续回复

1. 发送：「帮我调研 X，调研期间先告诉我你的计划」
2. 预期：主 Agent 调 `start_task` 后**不等子任务**，立即输出计划文本
3. 工具栏右侧网络图标（带角标）→ 抽屉出现 running 任务卡，显示步数
4. 任务卡显示「X 步」且随执行增长（SSE push，非轮询刷新）

## 2. 子会话查看

1. 运行中或完成后点任务卡整行 → 展开「子会话详情」
2. 预期：指令（灰字）→ 工具调用（ToolCallCollapse，与主 Agent 同形态，
   可展开看参数/结果）→ 模型每轮文本
3. 刷新页面再进入会话 → 重启前的历史任务卡仍可点开（持久层 + checkpoint fallback）

## 3. send_message 调整（followup-turn）

1. 任务 running 时，详情底部输入「聚焦中文源」，发送
2. 预期：提示「已发送，子任务将作为新一轮执行」；当前 turn 结束后
   子 Agent 以该消息开新 turn，详情出现新一轮工具调用
3. completed 任务发送追问 → 任务回到 running，结束后结果更新
4. failed / cancelled 任务发送 → 报「任务已结束」

## 4. 完成通知收果

1. 等子任务完成（页面开着）：主 Agent 无活跃 run 时自动续跑——
   对话流出现**系统通知条**（弱化居中行，不是用户气泡），随后主 Agent
   自动继续输出 check_task 收果与汇总
2. 主 Agent run 活跃时完成：下一次模型调用边界即时注入，主 Agent 在
   本轮继续时收果
3. 用户真实消息界面：**不出现**任何 `[系统通知]` 伪装的用户消息
4. 刷新页面：通知条仍渲染为通知条（`source_kind` 标记），非用户气泡

## 5. 审批路径（HITL）

1. 让主 Agent 委派一个会触发危险命令的子任务（如 `rm -rf` 某工作区路径）
2. 预期：任务转「待审批」，抽屉顶部出现审批卡（批准/拒绝）
3. 批准 → 任务续跑至完成；拒绝 → 按拒绝续跑
4. 审批悬置不打断前台等待：`run_in_background=false` 的任务在审批期
   间工具持续等待

## 6. 前台等待（依赖链）

1. 让主 Agent 做一个「先查 A 再基于 A 做 B」的任务，观察其用
   `start_task(..., run_in_background=false)`
2. 预期：工具等待期间界面仍可响应（不阻塞事件循环），返回后主 Agent
   直接使用子任务结果
3. 构造 >2 分钟的前台任务 → 自动转后台提示，主 Agent 继续其他工作

## 7. 后台命令（execute run_in_background）

1. 让主 Agent 跑一个长命令（如 `sleep 120 && echo done`），观察其用
   `execute(command, run_in_background=true)`
2. 预期：立即返回 task_id；抽屉出现「命令」标记的任务卡；短命令仍前台
   同步执行（行为与之前完全一致）
3. 完成后 `check_task` 收果：exit code + 输出尾部；对话流出现系统通知条
4. 危险后台命令：审批卡仍出现在启动前（工具名未变，interrupt_on 匹配）
5. 运行中命令任务 → 详情显示命令 + 输出；无「发送指令」输入框
6. 删除会话（docker 模式）：运行中任务转 failed（「会话沙箱已销毁」）

## 8. 回归基线

- 后端：`uv run pytest tests/ -q` → 8 个既有失败（与本变更无关）之外全绿
- 前端：`pnpm lint`、`pnpm vitest run __tests__/`、`pnpm build` 全绿
