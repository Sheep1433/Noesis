# Noesis（智枢）前端

Noesis（智枢）是一个基于 Vue 3 + Vite 5 + TypeScript + Naive UI 的全栈 AI 对话应用的前端部分。

## 技术栈

- **框架**: Vue 3 + Vite 5 + TypeScript
- **UI**: Naive UI
- **状态管理**: Pinia
- **样式**: UnoCSS + Iconify
- ** Markdown**: markdown-it + highlight.js

## 功能特性

- 流式输出响应（SSE，经 `/api` 与后端通信）
- Markdown 渲染（代码高亮）
- 问答类型：智能问答、故障运维、测试用例生成、深度研究
- 工具调用折叠展示
- 推理过程展示
- 聊天历史管理
- 知识库管理
- 技能管理
- MCP 客户端

## 前置条件

- Node.js >= 18.12.x
- pnpm 9.x

## 安装和运行

```bash
# 安装依赖
pnpm i

# 本地开发（http://localhost:2048）
pnpm dev

# 构建生产版本
pnpm build

# GitHub Pages 部署
pnpm build:gh-pages
```

## 配置

复制 `.env.template` 为 `.env.local`，按需设置环境变量：

| 变量 | 说明 |
|------|------|
| `VITE_BASE_API` | REST 前缀，默认 `/api` |
| `VITE_LANGFUSE_UI_ORIGIN` | Langfuse 控制台地址，设置后显示「观测」入口 |
| `VITE_TEST_CASE_UPLOAD_COLLECTION` | 测试助手需求文档上传集合名 |
| `VITE_ROUTER_MODE` | 设为 `hash` 用于 GitHub Pages 部署 |

运行时配置见 `src/config/env.ts`、`src/config/knowledge.ts`。

## 项目结构

```
frontend/src/
├── api/               # API 接口层
├── assets/           # 静态资源
├── components/       # 公共组件
│   ├── Layout/      # 布局组件
│   ├── MarkdownPreview/  # Markdown 渲染
│   ├── Navigation/ # 导航组件
│   └── ...
├── hooks/           # 组合式函数
├── router/          # 路由配置
├── store/           # Pinia 状态管理
├── types/           # TypeScript 类型
├── utils/           # 工具函数
├── views/           # 页面视图
│   ├── chat.vue    # 核心对话页面
│   └── ...
├── config/          # 配置文件
└── main.ts          # 应用入口
```

## 后端联动

本前端需要配合后端服务运行，后端基于 FastAPI + LangGraph 实现。

启动后端：
```bash
cd backend
uv run app.py
```

本地开发时 Vite 将 `/api` 代理到 `http://127.0.0.1:8089`。
