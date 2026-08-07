# model-provider-settings Specification

## Purpose
TBD - created by archiving change expand-settings-control-plane. Update Purpose after archive.
## Requirements
### Requirement: 用户 SHALL 只读查看平台模型目录

设置页 SHALL 展示平台已配置的可用模型、默认模型及用户可理解的能力信息，不得提供 Provider、Base URL 或 API Key 输入。

#### Scenario: 查看模型设置
- **WHEN** 用户打开模型设置
- **THEN** 页面 SHALL 展示 `/api/models` 返回的目录且不出现凭据管理操作

