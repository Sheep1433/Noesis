## MODIFIED Requirements

### Requirement: LLM 工厂

系统 SHALL 按部署端配置的模型目录与 `MODEL_TYPE`（或等价配置）选用厂商 LangChain 集成创建聊天模型；用户选择的 `model_id` SHALL 只能引用平台公开目录。系统 SHALL NOT 从用户设置加载 Provider 地址、API Key 或运行时模型快照，且 SHALL NOT 在业务代码硬编码密钥。

#### Scenario: 缺密钥失败可定位
- **WHEN** 平台模型所需 API Key 缺失
- **THEN** 创建模型 SHALL 失败并给出可定位错误，而非静默空响应

#### Scenario: 用户选择平台模型
- **WHEN** 用户在聊天页选择 `/api/models` 中的模型
- **THEN** 后续 run SHALL 使用该平台目录项且不读取用户 Provider 配置
