# Tasks: chat-composer-send-time-upload

## 1. 后端 — 会话 ensure API

- [x] 1.1 `schemas/chat_vo.py` 新增 `EnsureSessionRequest`（`title?`, `extra?`）
- [x] 1.2 `chat_api.py` 新增 `PUT /api/chat/sessions/{session_id}/ensure`，内部 `ChatService.get_or_create_session`
- [x] 1.3 确认 `POST .../attachments` 在无 session 时仍返回 404（移除若有 upload lazy create 临时代码）
- [x] 1.4 单测：ensure 创建 / 幂等 / 越权 404

## 2. 前端 — 本地队列

- [x] 2.1 `FileUploadManager` 或新模块：COMMON_QA 模式下 `enqueueFiles` 仅入队，不调 `uploadSessionAttachment`
- [x] 2.2 队列 UI：预览、移除、失败态（发送阶段）
- [x] 2.3 拖拽 / 粘贴 / 下拉统一走 enqueue
- [x] 2.4 `FAULT_OPERATION_QA` 拦截入队

## 3. 前端 — 发送编排

- [x] 3.1 `api/chat.ts` 新增 `ensureSession(sessionId, { qa_type })`
- [x] 3.2 `handleCreateStylized`：validate trim → ensure → 串行 upload → build file_dict → stream
- [x] 3.3 发送中 loading 文案（上传中 / 生成中）
- [x] 3.4 upload 失败中止 stream，保留输入

## 4. 前端 — 发送按钮灰化

- [x] 4.1 `sendDisabled` computed：`!trim(text)`、uploadingOnSend、faultOp+files、既有 loading 停止逻辑
- [x] 4.2 仅附件无文字时按钮禁用（手动或 E2E 验证）

## 5. 前端 — 生命周期丢弃

- [x] 5.1 `newChat` / 切换历史会话 / 页面 mount 时清空本地队列
- [x] 5.2 不引入 sessionStorage 草稿

## 6. 回归与文档

- [x] 6.1 纯文本首条发送、带附件首条发送、空格灰化、刷新丢弃
- [x] 6.2 更新 `general-qa-file-upload` 相关 AGENTS 说明（上传时机改为发送时）
- [x] 6.3 `pnpm lint` / 受影响 backend tests

## 依赖顺序

```
1.* → 3.* → 6.*
2.* 与 3.* 可并行
4.*、5.* 随 2./3. 一并落地
```
