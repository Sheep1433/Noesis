## MODIFIED Requirements

### Requirement: 容器化运行环境与配置注入

Docker（或 Docker Compose）部署 SHALL 通过环境变量或挂载的 env 文件为后端提供 PostgreSQL 业务库、PostgreSQL LangGraph checkpoint、Qdrant、模型 API 等连接信息，其键名与 `backend/config/env.py` 中 pydantic-settings 字段对应；Compose SHALL 为 PostgreSQL 服务配置持久化存储、健康检查，并使后端在其就绪后启动。镜像与版本控制文件 SHALL NOT 包含真实生产密钥或仅适用于个人机器的硬编码口令。

#### Scenario: 运维使用 PostgreSQL 启动

- **WHEN** 运维在 compose 或运行时环境中设置与后端配置类一致的 PostgreSQL 业务库、LangGraph checkpoint 与 Qdrant 主机、端口及凭证
- **THEN** 后端容器 SHALL 在 PostgreSQL 健康检查就绪后启动并成功连接所配置的外部依赖，且仓库内无可提交的明文密钥变更
