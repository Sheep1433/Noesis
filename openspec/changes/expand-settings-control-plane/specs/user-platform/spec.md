## ADDED Requirements

### Requirement: 用户作用域设置 API SHALL 统一鉴权、隔离与 secret 语义
新增设置 API SHALL 使用有效 Cookie Session，非安全方法 SHALL 校验 CSRF，所有读写 SHALL 限于当前用户。secret read model SHALL 只返回 configured、可选后缀和更新时间；写命令 SHALL 区分 keep、replace、clear。系统 SHALL NOT 使用 Bearer JWT 作为这些 API 的身份凭据。

#### Scenario: 跨用户设置访问
- **WHEN** 用户 A 使用用户 B 的设置资源 id 发起读取或修改
- **THEN** 系统 SHALL 返回 404 且不披露资源是否存在

### Requirement: 用户设置导入 SHALL 按域校验和事务化
导入 apply SHALL 重新校验 preview 使用的版本和当前状态，并按设置域事务化写入；某域失败 SHALL 回滚该域且不留下部分 secret 变更。所有成功或失败的 apply SHALL 形成脱敏审计。

#### Scenario: 导入时状态已变化
- **WHEN** preview 后目标设置已被其它请求修改
- **THEN** apply SHALL 拒绝冲突域并要求重新预览，而不是静默覆盖
