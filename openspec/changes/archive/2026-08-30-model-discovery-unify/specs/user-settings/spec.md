# user-settings Delta：模型默认端点与发现-采纳交互

## MODIFIED Requirements

### Requirement: 用户 SHALL 管理对话模型目录（默认端点 + 发现-采纳）

设置页 `models` section SHALL 展示部署默认模型与用户自定义 Provider 模型：内置目录条目与用户在同名 Provider（slug=默认端点标识）下采纳的模型渲染为同一组，同名 Provider SHALL NOT 再单列为独立自定义组。

默认端点组 SHALL 提供发现入口：以部署侧端点与 Key 探测 `GET /models`，返回当下真实可用列表；用户勾选后批量采纳为该用户的模型行（立即落库，Key 为部署侧配置的公开占位值）。自定义 Provider 表单内的发现 SHALL 使用同一勾选批量交互，采纳结果进入表单草稿、随表单保存生效。

发现结果含免费模型时，面板 SHALL 提供「只看免费」筛选项（默认激活、可关闭）；无免费模型的 Provider SHALL 平铺全部结果。免费判定为 model_id 含 `-free` 或 `:free` 片段，或发现行原始字段标记免费（如 kilo 的 `isFree`）。系统 SHALL NOT 在代码内固化任何特定平台特判。

探测 SHALL 在网络层异常时重试一次；最终失败 SHALL 记录含异常类型与服务端日志，错误消息 SHALL 携带异常类名。

API Key 加密存储与凭据禁令（不得展示明文 Key）沿用 `user-platform` 既有要求。

#### Scenario: 默认模型开箱可用

- **WHEN** 部署以默认配置启动且用户未做任何模型配置
- **THEN** 默认对话模型 SHALL 可用（kilo 免费网关 + 公开占位 Key）

#### Scenario: 发现并采纳免费模型

- **WHEN** 用户在默认端点组点击「获取可用模型」并勾选若干模型后批量添加
- **THEN** 所选模型 SHALL 立即落库为该用户在同名 Provider 下的模型行
- **AND** 分组展示 SHALL 将其与内置目录条目合并为同一组

#### Scenario: 只看免费筛选

- **WHEN** 用户打开发现结果（如 kilo 的 368 个模型）
- **THEN** 面板 SHALL 默认仅展示免费模型（19 个）且提供关闭筛选的入口

#### Scenario: 探测网络抖动重试

- **WHEN** 出网探测首次因网络层异常失败
- **THEN** 系统 SHALL 自动重试一次，成功即正常返回

#### Scenario: 查看模型设置

- **WHEN** 用户打开模型设置
- **THEN** 页面 SHALL 展示 `/api/models` 返回的目录且不出现明文凭据
