---
name: product-facing-copy-audit
description: >-
  Audit user-facing product copy for leaked engineering/implementation details
  (paths, env flags, multi-tenant internals, debug notes). Use when editing UI
  toast/empty/error text, settings copy, API message/runtime_note fields shown
  in the client, or when the user asks to check 产品文案 / 用户可见文案 / 不要暴露实现细节.
---

# 产品端文案审计（禁止工程细节泄漏）

面向**真实产品用户**的文案：只说「做什么 / 怎么用 / 结果如何」，**禁止**把实现、部署、多租户隔离、内部开关写进 UI。

## 何时执行

- 改前端 toast、placeholder、hint、空态、错误提示、设置页说明
- 改后端会进入响应体且前端会展示的字段（如 `message`、`runtime_note`、`detail`）
- 用户提到：产品文案、用户可见、实现细节泄漏、别写给开发看的话

## 检查范围（默认）

| 区域 | 路径提示 |
|------|----------|
| 前端文案 | `frontend/src/**/*.{vue,ts,tsx}` 中的用户可见字符串 |
| 后端用户可见 | `backend/api/**`、`backend/services/**` 写入 Response / 返回 dict 且会被 UI 展示的文案 |
| **排除** | `docs/`、`openspec/`、`AGENTS.md`、测试断言字符串、logger、注释、仅管理员/运维 CLI |

## 禁止出现在产品端的内容

1. **存储与路径**：`.data/`、`users/{id}`、`channels.json`、仓库相对路径、绝对路径、「本机用户目录」
2. **配置与开关**：`config.yaml`、`messaging.*`、`*_enabled=true`、env 名、`APP_ENV`、「重启后端」
3. **多租户/安全实现自白**：「不会回传到其他用户」「仅你的 user_id」「其他租户看不到」「写在服务器磁盘」
4. **内部架构词**：PersistSink、SSOT、Fan-out、SPI、OpenSpec、Alembic、headless run（除非产品正式命名）
5. **开发者口吻**：TODO、临时、联调、hack、debug、给 Agent 看的说明
6. **过度暴露威胁模型**：在普通设置成功 toast 里解释密钥落盘与隔离策略（安全说明应放帮助中心/隐私政策，且用产品语言）

## 允许的产品文案

- 操作结果：已保存 / 已删除 / 配对已更新 / 请填写 Bot Token
- 任务引导：去 @BotFather 创建机器人、发送 /start、填写 Chat ID
- 用户可理解的状态：未配置、已启用、连接失败请稍后重试
- 脱敏展示：`****尾号`（不解释存储实现）

## 工作流程

1. 对改动或指定目录，搜索用户可见字符串（`message.success/error/warning`、`placeholder`、模板文本、API 返回的 note/message）。
2. 对照上方「禁止」清单；命中则标为 **P0（必须改）**。
3. 给出替换建议：删掉实现句，改成中性产品句（优先更短）。
4. **直接修复**当前任务相关的泄漏文案；不要只评论不改（除非用户只要报告）。
5. 输出简短报告：

```markdown
## 产品文案审计
- P0: `path:line` — 问题 → 建议
- OK: （无则写「未发现用户可见工程细节」）
```

## 反例 → 正例

| 反例（禁止） | 正例 |
|--------------|------|
| 已保存到本机用户目录（不会回传到其他用户） | 已保存 |
| 真收发需 messaging.telegram_runtime_enabled=true 并重启 | （删除；或「通道未就绪，请稍后重试」若必须提示状态） |
| Token 保存在 `.data/users/{id}/channels.json` | （删除；引导里只写怎么填 Token） |
| 约 30 秒内生效（supervisor reconcile） | 配对已更新 |

## 写新文案时

先问：这句话若印在正式 SaaS 界面上，用户是否需要知道？若只有开发/运维需要 → **不要进产品端**，写到 `docs/` 或运维 runbook。
