# Tasks

## 1. 默认端点切换 kilo

- [x] 1.1 五份配置默认模型切 kilo（type=openai、kilo-auto/free、api_key=public 占位，注释标注无 Key 行为风险与 .env 真 Key 替代）：`deploy/config.docker.yaml`、`config.example.yaml`、`config.prod.example.yaml` 及两份本地未跟踪配置
- [x] 1.2 catalog 收缩为默认单条；预设收缩为 deepseek/alibaba/minimax（无 opencode、无 kilo）
- [x] 1.3 平台 Provider 标签无预设时回退端点域名（`model_api.py`）

## 2. 发现-采纳交互统一

- [x] 2.1 共享组件 `ModelDiscoveryPanel`：勾选多选 + 批量「添加所选」+ 内置「只看免费」chip（`-free`/`:free` 片段或 `isFree` 字段，通用规则）
- [x] 2.2 平台组与自定义表单同一面板；已添加条目置灰
- [x] 2.3 平台组合并展示采纳模型（同名 Provider 不再单列）+「管理」入口
- [x] 2.4 发现行透传布尔/数值原始字段（`flags`，kilo `isFree`）

## 3. 探测健壮性

- [x] 3.1 `_probe_models_endpoint` 网络层异常重试一次；最终失败 warning 日志 + 消息带异常类名
- [x] 3.2 回归测试：抖动重试、彻底失败消息、flags 断言

## 4. 测试与验证

- [x] 4.1 后端 30 项相关测试通过（model_catalog / user_llm_service / embedding_config）
- [x] 4.2 活体验证：kilo 探测 368 模型 / 19 免费（panel 规则）/ 默认模型在列；配置链路（ModelConfig → catalog → provider info）加载正确
- [x] 4.3 `pnpm lint` / `pnpm build` 通过
- [ ] 4.4 本地 `./scripts/run.sh prod` 设置页手工回归（用户验证）

## 5. 文档

- [x] 5.1 openspec 变更 `model-discovery-unify`（proposal + user-settings delta + tasks）
