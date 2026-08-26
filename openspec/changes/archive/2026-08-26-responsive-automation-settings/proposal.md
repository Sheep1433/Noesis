## Why

设置页「定时与自动化」区域在窄屏下控件换行错乱、操作区溢出，且新增任务表单与列表耦合在同一个组件中，难以维护。

## What Changes

- 将 AutomationSection 的布局改为响应式：窄屏下单列堆叠、操作按钮自动换行。
- 抽出 AutomationTaskForm 组件承载新增任务表单，列表组件只保留展示与状态操作。

## Impact

- 仅影响前端设置页；API 与后端行为不变。
- 影响规范：`user-settings` 的自动化设置展示要求。
