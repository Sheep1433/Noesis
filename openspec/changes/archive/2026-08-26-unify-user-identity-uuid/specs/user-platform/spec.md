## ADDED Requirements

### Requirement: 用户标识 SHALL 使用字符串 UUID

系统 SHALL 以 UUID 字符串作为用户唯一标识，在数据库、会话、Service 与 API 层保持单一表示；SHALL NOT 在自增整数与字符串之间维护双表示。

#### Scenario: 新用户注册
- **WHEN** 新用户创建账户
- **THEN** 系统 SHALL 生成 UUID 作为用户标识并在所有关联表中以字符串引用

#### Scenario: 存量数据迁移
- **WHEN** 已有数据库升级到本版本
- **THEN** Alembic SHALL 将既有整型 user id 转换为 UUID 并保持全部关联关系
