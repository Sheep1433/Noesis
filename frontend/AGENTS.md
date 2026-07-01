# Noesis 前端开发指南

Vue 3 + Vite + TypeScript + Naive UI。仓库级约定见 [../AGENTS.md](../AGENTS.md)（协作与 Bug 流转以该文件为准）。

## 常用命令

```bash
pnpm i              # 安装依赖
pnpm dev            # 本地开发 http://localhost:2048
pnpm build          # 生产构建
pnpm build:gh-pages # GitHub Pages（hash 路由）
pnpm lint           # ESLint
pnpm lint:fix       # ESLint 自动修复
pnpm stylelint      # 样式检查
```

## 核心架构

### 应用入口

- `src/main.ts` — 插件、路由、状态管理
- `src/App.vue` — 根组件
- `src/NaiveProvider.vue` — Naive UI Provider

### 路由

- `src/router/index.ts` — 路由模式由 `VITE_ROUTER_MODE=hash` 切换 hash/history
- `src/router/routes.ts`、`child-routes.ts`、`permission.ts`

### 状态管理（Pinia）

- `src/store/business/userStore.ts` — 认证（token、登录/登出）
- `src/store/business/index.ts` — `qa_type`、`file_list`、流式对话
- `src/store/business/initChatHistory.ts` — 历史初始化

### SSE 流式

- `src/views/chat/useSSEStream.ts` — SSE 消费
- `src/views/chat/messageParts.ts` — 消息部件组装
- `src/components/MarkdownPreview/` — 流式 Markdown 渲染

**持久化边界**：assistant 消息由后端 `QaService` 落库；前端仅 reduce SSE 到内存中的 `content.parts` 做实时渲染。`[DONE]` / `finish` 用于结束 loading 与 UI 收尾，**不是**入库条件。刷新后以 `GET .../messages` 读库为准。

### API 层

- `src/api/user.ts`（登录、历史记录）、`chat.ts`、`knowledgeBase.ts`、`skills.ts`
- 统一经 `src/utils/authHttp.ts`（`authFetch` / `getAuthHeaders` / `parseAuthJson`）鉴权

### 主要页面

| 路径 | 说明 |
|------|------|
| `views/chat.vue` | 核心对话页 |
| `views/Login.vue` | 登录 |
| `views/knowledge-base/` | 知识库 |
| `views/skills/` | Skills 管理 |
| `views/mcp/MCPClient.vue` | MCP 客户端 |
| `views/TestAssistant.vue` | 测试助手 |

### 关键组件

`ReasoningBlock`（推理）、`ToolCallCollapse`（工具调用）、`TodoList`、`KnowledgeBase/*`、`Layout/*`、`Navigation/*`

### 配置

- `src/config/env.ts` — `langfuseUiOrigin` 等运行时配置
- `src/config/theme.ts` — 设计 token（TS 侧常量，与 CSS 变量对齐）
- `src/styles/tokens/_semantic.scss` — `--noesis-*` CSS 变量唯一来源
- `.env.template` — 本地环境变量模板（复制为 `.env.local`）

### 样式约定

- **颜色/圆角/阴影**：组件内使用 `var(--noesis-*)`，禁止新增散落 hex
- **Naive UI**：`useTheme` + `themeOverrides` 使用 hex 色值（`:color` / `primaryColor` 禁止传 CSS var，seemly 无法解析）；页面样式仍用 CSS 变量
- **UnoCSS**：`uno.config.ts` 的 `theme.colors` 引用同一套 CSS 变量；`presetIcons.collections` 须用 `() => require('@iconify-json/…')` 函数形式，否则 prod 不生成 Iconify 图标 CSS
- **主题预设**：`newsprint`（纸墨，默认）/ `light`（Flat Design）/ `deep`（深色）；侧栏调色盘切换，定义见 `styles/tokens/_semantic.scss`（纸墨 `:root`）与 `_presets.scss`（浅/深色）

## 技术要点

- 流式：`MarkdownPreview` 通过 `reader`（`ReadableStreamDefaultReader`）+ `model` 解析 SSE
- `qa_type`：`COMMON_QA`、`FAULT_OPERATION_QA`、`TEST_CASE_QA`、`DEEP_RESEARCH_QA`（与后端一致）
- 认证：Token 在 `sessionStorage`；`meta.requiresAuth`；401 跳登录

## 验证

改动后按影响范围执行 `pnpm lint` / `pnpm build`。
