# Agent Memory

## 分层

| 层 | 文件 | 用途 | 默认加入 Agent 上下文 |
|---|---|---|---|
| L0 | `USER.md` | 稳定画像与背景 | SuperAgent 是 |
| L1 | `AGENTS.md` | 长期规则与偏好 | SuperAgent 是 |
| L2 | `memory/YYYY-MM-DD.md` | 从跨会话消息整理出的按日记忆 | 否 |

USER.md 与 AGENTS.md 由用户通过 Markdown 原文维护。每日整理不会自动修改这两个文件，避免把一次性信息变成长期规则。

## 数据流

```mermaid
flowchart LR
  A["已完成的聊天消息"] --> B["MemoryDreamService"]
  B --> C["过滤 reasoning、工具输出、错误和删除内容"]
  C --> D["memory/YYYY-MM-DD.md"]
  D --> E["search_memory"]
  E --> F["精简摘要与来源 ID"]
  F --> G["get_memory_source"]
  G --> H["有限原始消息上下文"]
```

整理器按用户和自然日读取消息，把 user/assistant 问答组合成稳定条目。条目标识由来源消息和内容计算；同一天重复运行会重建同一文件，不会重复追加。写入使用临时文件原子替换，失败时旧文件仍可用。

## 检索

设置页和 SuperAgent 使用同一个条目检索 Service。搜索结果只包含日期、分类、摘要、关键词、分数与来源 ID，不返回整篇每日文件。Agent 需要核对细节时，再调用来源读取工具获取有限相邻消息。

首版采用关键词相关度。文件格式保留稳定的结构化元数据，后续可增加 FTS 或向量索引，而不改变每日文件和 API 的外部行为。

## 权限与隐私

- 整理查询同时校验 message 与 session 的 user_id。
- 来源读取再次校验 session、message 均属于当前用户。
- Agent tool 在创建时绑定 user_id，模型参数不包含 user_id。
- reasoning、工具原始输出、未完成、错误和已删除消息不会写入 L2。
- L2 不参加默认 MemoryMiddleware 注入，只在用户问题需要历史信息时按需检索。

## 自动运行与失败行为

应用内 scheduler 每小时检查一次上一自然日。目标文件带成功标记时跳过；失败只记录日志，下一周期继续尝试，不影响聊天服务。用户也能在设置页选择日期手动补跑。
