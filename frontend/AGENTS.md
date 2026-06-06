# Noesis Frontend - Agent 项目指南

## 项目概述

Noesis 前端是基于 Vue 3 + Vite 5 + TypeScript + Naive UI 的全栈 AI 对话应用，支持多种大模型（Spark、SiliconFlow、Ollama）的流式输出响应。

## 常用命令

```bash
# 安装依赖
pnpm i

# 本地开发（http://localhost:2048）
pnpm dev

# 构建生产版本
pnpm build

# GitHub Pages 部署（使用 hash 路由）
pnpm build:gh-pages

# 代码检查
pnpm lint

# 代码检查并自动修复
pnpm lint:fix

# Stylelint 检查
pnpm stylelint
```

## 核心架构

### 应用入口
- `src/main.ts` - 应用入口，注册插件、路由、状态管理
- `src/App.vue` - 根组件
- `src/NaiveProvider.vue` - Naive UI Provider

### 路由系统
- `src/router/index.ts` - 路由配置，路由模式根据 `isMockDevelopment` 切换（hash/history）
- `src/router/routes.ts` - 根路由定义
- `src/router/child-routes.ts` - 子路由（ChatRoot、McpChat 等）
- `src/router/permission.ts` - 路由守卫，处理认证

### 状态管理（Pinia）
- `src/store/index.ts` - Store 初始化
- `src/store/business/userStore.ts` - 用户认证状态（token、登录/登出）
- `src/store/business/index.ts` - 业务状态（qa_type、file_list、task_id）和 AI 对话流式处理
- `src/store/business/initChatHistory.ts` - 聊天历史初始化
- `src/store/hooks/useAppStore.ts` - 应用状态 Hook

### SSE 流式处理
- `src/views/chat/useSSEStream.ts` - SSE 流式响应处理 Hook
- `src/views/chat/messageParts.ts` - 消息部件处理

### API 层
- `src/api/index.ts` - API 入口
- `src/api/client.ts` - API 客户端配置
- `src/api/chat.ts` - 聊天相关 API
- `src/api/knowledgeBase.ts` - 知识库 API
- `src/api/skills.ts` - Skills 文件目录 API（磁盘）
- 使用原生 Fetch API，通过 `userStore.getUserToken()` 获取认证 token

### Hooks
- `src/hooks/useClipText.ts` - 剪贴文本
- `src/hooks/useCopyCode.ts` - 代码复制
- `src/hooks/useCurrentInstance.ts` - 当前实例
- `src/hooks/useTheme.ts` - 主题切换

### 视图层
- `src/views/chat.vue` - **核心对话页面**（包含聊天逻辑）
- `src/views/chat/` - 聊天子模块
  - `index.vue` - Markdown 渲染组件
  - `useSSEStream.ts` - SSE 流式处理 Hook
  - `messageParts.ts` - 消息部件
- `src/views/Login.vue` - 登录页面
- `src/views/mcp/MCPClient.vue` - MCP 客户端页面
- `src/views/knowledge-base/` - 知识库相关页面
  - `KnowledgeBase.vue` - 知识库主页
  - `CollectionDetail.vue` - 集合详情
- `src/views/skills/` - Skills 文件目录管理
  - `SkillsManagement.vue` - 目录树与预览
- `src/views/TestAssistant.vue` - 测试助手
- `src/views/SuggestedPage.vue` - 建议页面
- `src/views/PdfViewer.vue` - PDF 查看器
- `src/views/FileUploadManager.vue` - 文件上传管理
- `src/views/TableModal.vue` - 表格弹窗
- `src/views/DefaultPage.vue` - 默认页面

### 组件库
- `src/components/MarkdownPreview/` - Markdown 渲染核心
  - `index.vue` - 主组件，处理流式响应
  - `plugins/` - 插件（highlight, markdown, preWrapper）
- `src/components/Layout/` - 布局组件
  - `default.vue` - 默认布局
  - `SidearPage.vue` - 侧边栏页面
  - `SlotArea.vue` / `SlotFrame.vue` / `SlotCenterPanel.vue` - 插槽布局
- `src/components/Navigation/` - 导航组件
  - `NavBar.vue` - 导航栏
  - `NavSideBar.vue` - 侧边导航
  - `NavFooter.vue` - 页脚
  - `NavOctocat.vue` - Octocat 导航
  - `SideBar.vue` - 侧边栏
- `src/components/KnowledgeBase/` - 知识库组件
  - `DocumentDrawer.vue` - 文档抽屉
  - `ShardDetail.vue` - 分片详情
- `src/components/TodoList/` - Todo 列表组件
- `src/components/FileUploadManager/` - 文件上传管理
- `src/components/TableList/` - 表格列表
- `src/components/Pagination/` - 分页组件
- `src/components/IconFont/` - 图标字体
- `src/components/IconifyIcon/` - Iconify 图标
- `src/components/ClipBoard/` - 剪贴板
- `src/components/AssistantReplyToolbar/` - Assistant 回复工具栏
- `src/components/ReasoningBlock/` - 推理过程展示块
- `src/components/ToolCallCollapse/` - 工具调用折叠组件
- `src/components/404.vue` - 404 页面

### 配置
- `src/config/env.ts` - **关键配置**：`isMockDevelopment` 控制模拟/真实 API 模式
  - 开发环境默认 `true`（使用模拟数据）
  - 设置为 `false` 时调用真实大模型接口

## 关键技术点

### 前端流式响应处理
`MarkdownPreview` 组件通过 `reader` prop 接收 `ReadableStreamDefaultReader`，配合 `model` 属性（指定大模型类型）进行响应解析和 Markdown 渲染。

### 问答类型（qa_type）
- `COMMON_QA` - 智能问答
- `DEEP_RESEARCH_QA` - 深度研究

### 用户认证
- Token 存储在 `sessionStorage`
- 路由守卫检查 `meta.requiresAuth`
- 401 响应自动跳转登录页

## 重要事项
- 遇上问题时永远不要想着加容错，而是先解决问题，然后再考虑是否需要加容错
