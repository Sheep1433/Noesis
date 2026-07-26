## Context

当前 `/settings` 由 `frontend/src/views/settings/SettingsShell.vue` 与多个 section 组成，画像和记忆走用户文件 API，自动化与通道走 `/api/user/*`，Skills/MCP/知识库仍是独立管理页。后端相关实现位于 `backend/noesis_server/api/`、`services/` 与 `domain/`，Agent 装配和模型工厂位于 `backend/packages/harness/noesis/`。现状已经具备各功能纵向链路，但缺少统一设置组件、用户级 Provider/default model 存储、运行记录、诊断聚合和安全的配置迁移机制。

本 change 跨前端、API、Service、数据模型、Scheduler、Delivery 与 Agent runtime。设计必须维持 API → Service → Domain/Harness 依赖方向，维持 Cookie Session + CSRF，且不得让 Agent 工具读取或修改 secret。当前 `/api/chat` SSE 和 assistant 持久化状态机不因本 change 改动。

## Goals / Non-Goals

**Goals:**

- 建立可扩展、可搜索、可深链、交互一致的设置控制面。
- 为 Provider、模型用途、MCP、自动化、通道、记忆/规则和平台健康提供可验证的管理闭环。
- 把运行结果、错误和恢复动作带回对应设置页，而不是要求用户读取服务端日志。
- 为敏感配置提供统一脱敏、审计、导入导出与用户隔离。
- 通过先行的共享契约和集中集成审查减少跨能力边界冲突。

**Non-Goals:**

- 不新增多成员团队编排、语音、插件市场或多项目配置同步。
- 不替换现有 Skills、知识库整页管理界面；设置页只提供摘要、状态和深链。
- 不新增聊天 SSE 事件，不修改流式 assistant 单行落库状态机。
- 不允许用户设置覆盖平台安全禁止项，也不把 secret 放入用户记忆、导出文件或 Agent 上下文。
- 不在一个阶段同时上线全部能力；任务按依赖分波交付。

## Decisions

### D1：先建立共享设置契约，再在单功能分支集中集成能力 section

第一阶段只定义 `SettingsSectionDefinition`、导航注册表、共享 primitives、`SecretField` 交互契约和统一状态响应类型。section 通过静态注册表声明 `id`、标题、关键词、路由查询参数、权限和健康摘要提供者；不引入动态插件系统。

采用静态注册表而不是继续在 `SettingsShell.vue` 中硬编码分支，是为了让搜索、深链、概览和测试共享同一真相源。暂不采用服务端下发页面 schema，避免把 Vue 组件行为压缩成不成熟的通用表单协议。

本 change 的能力跨度较大且共享 API、schema、注册表与运行时边界较多，因此实际采用单一 `feat/expand-settings-control-plane` 功能分支完成实现和集成。共享地基先在该分支建立，后续能力按 A–F 工作流顺序落地；每个工作流保持独立目录和测试边界，共享注册表、primitives 与公共响应类型由同一集成分支集中修改和审查。

```
dev
 └── feat/expand-settings-control-plane
      ├── settings-foundation
      ├── provider-model-settings
      ├── automation-operations
      ├── channel-operations
      ├── context-settings
      └── settings-observability
```

功能分支完成全量自测后按 `feat → dev` 单向合并，不直接进入 `main`。后续类似 change 若能力边界足够独立，仍可在共享地基稳定后使用 worktree 并行；本次不事后重写 Git 历史。

### D2：用户设置采用分域存储，不建立单一巨型 JSON

- Provider/default model、通知偏好使用用户作用域关系表或独立配置实体。
- 自动化定义沿用现有 scheduled task 表，新增独立 run record 表。
- 通道定义沿用现有通道存储，运行健康从 Delivery read model 聚合。
- USER/AGENTS/L2 记忆继续以用户数据目录为权威，不复制进数据库。
- 审计使用 append-only 设置审计表，仅记录字段名、动作、主体、时间与脱敏摘要。

拒绝把所有设置放进一个 JSON 列：它虽然开发快，但难以做并发更新、约束、审计、局部迁移与用户隔离测试。

### D3：secret 统一使用写入命令与脱敏 read model

API read model 只返回 `configured`、可选后缀和最近更新时间；写入支持 `replace`、`keep`、`clear` 三态，前端不得回传服务端掩码作为新 secret。Provider API Key、通道 Token、MCP headers 中的敏感值都复用该语义。

secret 的静态存储沿用项目既有安全配置设施；若当前部署尚无可用加密设施，第一阶段只能引用部署侧 secret，不得以明文数据库字段作为临时方案。连接测试由应用服务在宿主进程中执行，错误返回用户可行动分类，不返回请求头或原始异常中的凭据。

### D4：Provider 配置与模型目录分层

`ProviderConnection` 表达 endpoint、鉴权引用、类型和启用状态；模型目录是对 Provider 发现结果与手工覆盖的 read model；`ModelPurposeBinding` 仅保存用途到 `(provider_id, model_id)` 的选择。模型工厂解析顺序为用户用途绑定 → 平台用途默认 → 现有环境配置。

默认用途至少包含 `chat`、`vision`、`embedding`、`rerank`。切换默认只影响后续新 run；正在执行的 run 保留启动时快照，保证一次 run 内模型稳定。

### D5：自动化定义与运行记录分离

scheduled task 继续保存定义和最新摘要；每次调度或手动触发创建 immutable run record，状态采用 `queued → running → succeeded|failed|cancelled`。重试创建新 run 并引用 `retry_of`，不覆盖旧记录。任务删除采用定义软删除或保留最小墓碑，确保历史记录可解释。

执行仍走现有统一 Run/Delivery 管线，不另建一条 Agent 调用路径。设置页通过普通 JSON API 轮询运行状态，不复用聊天 SSE。

### D6：通道诊断只读取 Delivery 权威状态

通道配置仍归用户设置 Service，adapter 健康、最近入站/出站、测试投递结果由 Delivery 提供 read model。测试连接不产生聊天消息；测试投递发送固定的产品测试内容并写审计。默认路由只允许选择当前用户可用的 `qa_type`/会话策略，不允许绑定其它用户会话。

### D7：上下文预览是只读编译结果

`agent-context-settings` 复用 Agent 装配的实际记忆和提示词解析逻辑，生成分段清单：来源、优先级、字符/Token 估算、是否注入及最终只读预览。预览不得调用模型、不得创建 checkpoint、不得写记忆。L2 搜索只返回当前用户日记文件的命中片段和元数据。

### D8：诊断 API 返回稳定能力状态，不暴露内部路径

诊断聚合返回 `healthy|degraded|unavailable|unknown`、检查时间、用户可理解摘要和可选行动码。具体主机名、绝对路径、连接串、堆栈和 secret 只进入服务端日志。每个依赖检查独立超时；单项失败不得让整个诊断端点 500。

### D9：配置导出采用版本化清单与两阶段导入

导出格式包含 `schema_version`、生成时间和各非敏感设置域；默认不包含 secret、会话消息、附件、checkpoint 或运行日志。导入先调用 preview，返回新增/修改/忽略/冲突与校验错误；用户确认后才 apply。apply 按设置域事务化，任一域失败时该域回滚并产生审计记录。

## Risks / Trade-offs

- [范围过大导致长期分支] → 在单功能分支内按 foundation、A–F 工作流分段实现并逐段验证，最终执行全量回归后一次合入 `dev`。
- [多个能力修改共享设置壳造成冲突] → 壳、primitives 和注册清单由集成分支集中修改；各能力保持独立 section、Service 与测试边界。
- [用户 Provider 配置改变现有部署行为] → 保留平台默认回退；用户绑定只影响新 run，并提供连接测试后才能设为默认。
- [健康检查拖慢页面] → 聚合服务并发执行、单项超时、短 TTL 缓存；页面先显示缓存再刷新。
- [secret 通过错误、导出或审计泄漏] → 统一 secret 类型和 redact 函数；为 API、日志、导出、审计增加负向回归测试。
- [运行记录无限增长] → 配置保留周期和分页，清理只删明细、不破坏任务最新摘要。
- [上下文预览与真实运行漂移] → 预览调用同一 resolver/compiler，不在 API 层复制拼装规则。

## Migration Plan

1. 在功能分支建立 settings foundation：注册表、primitives、兼容现有七个 section，不改变后端数据。
2. 增加数据库 migration 与 read/write Service，先部署后端兼容 API；旧配置仍为回退真相源。
3. 在同一功能分支依次集成 Provider、自动化、通道、上下文、诊断 section；每个 section 受独立 capability flag 控制，便于灰度和回滚。
4. 完成导入导出和审计后，再把概览健康摘要设为默认首页。
5. 全量验证通过后按 `feat/expand-settings-control-plane → dev` 合并；回滚前端时隐藏新注册项即可，回滚后端时保留新增表，旧 API 与现有 env/yaml、MCP JSON、任务和通道配置继续工作。

## Open Questions

1. Provider secret 首期使用部署侧 secret 引用，还是项目已有设施已足以提供用户级静态加密？实施前必须确认，不能以明文过渡。
2. 自动化运行记录默认保留 30 天还是按条数保留？建议首期 30 天且每用户设置硬上限。
3. 系统诊断是否只对普通用户展示其可行动子集，并为管理员增加更深信息？首期建议只实现普通用户安全视图。
4. L2 记忆搜索首期使用文件扫描还是复用后续记忆索引？建议先文件元数据 + 限量全文匹配，接口保持可替换。
