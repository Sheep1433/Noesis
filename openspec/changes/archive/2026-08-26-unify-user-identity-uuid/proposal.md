## Why

用户标识在数据库中以自增整数存储、在会话与运行时中以字符串透传，两套表示在记忆、子 Agent 与多进程链路上反复转换，且自增 id 会泄漏用户规模并跨环境不稳定。经验记忆按 `user_id` 隔离数据后，需要一个稳定、可加密存储、可跨表一致引用的用户标识。

## What Changes

- 新增 `noesis.ids` 作为唯一标识生成入口，用户 id 统一为 UUID 字符串。
- 所有以 `user_id` 关联的表（约 20 张，含 chat、settings、user_llm、scheduled_task、bg_task、memory 等）外键与索引迁移为字符串 UUID；Alembic 迁移 `202608240002_uuid_user_ids.py` 完成存量数据转换。
- 认证实体、会话、repository 与 API 层的 `user_id` 类型统一为 `str`，删除 int/str 双表示。
- 用户删除时的记忆数据清理保持原有级联语义。

## Impact

- 影响 API：`user_id` 字段类型对客户端不变（原本即字符串序列化）；数据库 schema 变更经 Alembic 自动迁移。
- 影响规范：`user-platform` 的用户标识表述。
- 迁移建议在低峰执行；空库部署直接建表，无需手工脚本。
