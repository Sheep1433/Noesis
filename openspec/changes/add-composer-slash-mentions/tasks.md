## 1. 后端契约与校验

- [x] 1.1 在 `schemas/qa_vo.py`（及 stream 入参映射处）增加可选 `mentions` 结构（Pydantic：`type` / `id` / `path` / `source` / `virtual_path`）
- [x] 1.2 实现 `MentionResolveService`（或等价）：校验归属与路径穿越；`qa_type` 允许的 mention 类型；映射 workspace → Agent 虚拟路径
- [x] 1.3 `qa_service`：解析 mentions → prompt 附加块；skill mention 并入本轮 `enabled_skills`；user 消息 `extra.mentions` 落库
- [x] 1.4 FAULT 拒绝 `skill` mention（4xx）；SUPER 接受 skill/file/folder/subagent；未知 subagent id 返回 4xx

## 2. 后端测试

- [x] 2.1 单测：合法 file/skill/subagent 解析与注入片段
- [x] 2.2 单测：路径穿越、跨会话、FAULT+skill、省略 mentions 兼容
- [x] 2.3 相关用例：`uv run pytest` 覆盖新增模块（按仓库惯例命名）

## 3. 前端 catalog 与缓存

- [x] 3.1 新增 composable/store：预取 skills tree + session context；TTL；与上下文面板共享失效信号（finish / 手动刷新）
- [x] 3.2 按 `qa_type` 维护 subagent 静态表（SUPER: `task-worker`；FAULT: `general-purpose`）
- [x] 3.3 本地 fuzzy 过滤工具函数（路径/名称）

## 4. 前端 Picker UI

- [x] 4.1 实现 `MentionPicker` 组件（列表、键盘导航、Esc 关闭）
- [x] 4.2 在 `chat.vue` Composer 输入绑定：空白后 `/`、`@` 触发；插入 chip；维护 `mentions` 数组
- [x] 4.3 发送路径：`sendMessage` / SSE 请求附带 `mentions`；历史气泡只读展示 `extra.mentions` chip
- [x] 4.4 按 `qa_type` 开关：SUPER 全开；FAULT 无 `/` Skills；其它类型首期隐藏

## 5. 联调与回归

- [x] 5.1 手动：SUPER `/` 选 skill、`@` 文件与 subagent，确认 Agent 提示与工具行为（后端注入由单测覆盖；UI 建议本地点验）
- [x] 5.2 手动：FAULT `@` 文件；提交 skill mention 应 4xx（单测覆盖拒绝路径）
- [x] 5.3 确认 `+` 菜单 Models/Skills/MCP 不受影响；旧请求无 `mentions` 仍可用
- [x] 5.4 后端 `uv run app.py` 可启动；前端影响范围 `pnpm lint`
- [x] 5.5 架构要点追加到 `docs/NOTES.md`（只增不减）
