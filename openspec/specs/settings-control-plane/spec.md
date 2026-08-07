# settings-control-plane Specification

## Purpose
TBD - created by archiving change expand-settings-control-plane. Update Purpose after archive.
## Requirements
### Requirement: 设置壳 SHALL 使用可搜索的统一 section 注册表
系统 SHALL 以单一注册表声明设置 section 的 id、标题、关键词、深链参数和可用状态；`/settings?s=<id>` SHALL 可直接打开合法 section，搜索 SHALL 匹配标题与关键词，未知 id SHALL 安全回退到概览。

#### Scenario: 搜索并深链设置项
- **WHEN** 用户搜索“模型”并选择模型设置
- **THEN** 系统 SHALL 打开对应 section 且 URL 包含稳定 section id

#### Scenario: 非法 section
- **WHEN** 用户访问不存在的 `/settings?s=unknown`
- **THEN** 系统 SHALL 回退到概览并保持设置壳可用

### Requirement: 设置交互 SHALL 提供一致状态与危险操作保护
所有设置 section SHALL 使用一致的加载、空态、保存中、保存成功、字段错误和请求失败语义；删除、清空、恢复默认和覆盖导入 SHALL 在执行前展示具体影响并要求确认。存在未保存修改时离开 section SHALL 提示用户。

#### Scenario: 未保存修改离开
- **WHEN** 用户修改表单但尚未保存并切换 section
- **THEN** 系统 SHALL 提示保存、放弃或留在当前 section

### Requirement: 设置导入导出 SHALL 版本化且默认排除敏感数据

系统 SHALL 提供当前用户设置的版本化导出，以及 preview/apply 两阶段导入。导出 SHALL 排除 secret、Provider、消息、附件、checkpoint 和运行日志；preview SHALL 展示新增、修改、忽略、冲突和校验错误，未经确认 SHALL NOT 写入。导入 SHALL NOT 接受 providers 设置域。

#### Scenario: 导出不含 Provider
- **WHEN** 用户导出当前设置
- **THEN** 导出文件 SHALL 不包含 Provider、API Key、Base URL 或模型用途绑定

#### Scenario: 导入预览
- **WHEN** 用户上传包含 providers 域的设置文件
- **THEN** 系统 SHALL 在预览中拒绝该域且不得写入 Provider 数据

### Requirement: 敏感设置变更 SHALL 形成脱敏审计

MCP secret、通道 Token、自动化定义、通知策略和设置导入等变更 SHALL 记录当前用户、动作、设置域、目标标识、时间和脱敏摘要；审计 SHALL NOT 保存旧值或新值的明文 secret。系统 SHALL NOT 再产生用户 Provider 变更审计。

#### Scenario: 替换通道 Token
- **WHEN** 用户替换通道 Token
- **THEN** 系统 SHALL 记录 replace 动作但 SHALL NOT 在审计响应或日志中返回 Token 内容

