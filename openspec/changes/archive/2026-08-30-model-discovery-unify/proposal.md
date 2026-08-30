# 模型默认端点切换 kilo 免费网关 + 发现-采纳交互统一

## Why

OpenCode Zen 免费模型集持续轮换（north-mini-code-free 已下线、新增多个），静态 yaml 目录必然过期且需部署侧反复手工维护。经对比（2026-08-27 实测）：OpenCode Zen 免费模型 7 个，kilo 免费网关 19 个（hy3、laguna、nemotron-3-ultra 550b 1M、minimax-m3、step、longcat 等，且含 opencode 已下线的 north-mini-code），kilo 阵容更全。用户决策：默认对话模型切 kilo 免费网关，opencode 退场；更多模型由用户在设置页自行发现添加。

同时统一发现列表交互：此前平台组用勾选批量添加、自定义表单用逐行「添加」按钮，同一功能两种交互。

## What Changes

- **默认对话模型切 kilo**：`model.type=openai`、`name=kilo-auto/free`、`base_url=https://api.kilo.ai/api/gateway`；Key 用 `public` 占位（kilo 免费模型当前不校验 Key——未文档化行为，配置注释标注失效影响与稳妥替代：注册免费 Key 放 `.env MODEL_API_KEY`）。服务端内部 LLM 任务（压缩摘要、记忆抽取/选条、VLM fallback）随默认模型切到 kilo。
- **目录收缩为默认单条**：yaml catalog 只保留部署默认模型；免费模型不进静态目录，用户在设置页「获取可用模型」按当下真实列表勾选添加。
- **预设收缩**：`provider_presets` 只剩 deepseek / alibaba / minimax（用户自建付费渠道）；opencode、kilo 不在预设下拉出现——默认端点即 kilo，其余端点用户自建。
- **发现面板统一**：抽共享组件 `ModelDiscoveryPanel`（勾选多选 + 批量「添加所选」），平台组与自定义表单同一交互；内置通用「只看免费」chip——仅当发现结果含免费模型时展示并默认激活，无免费模型的 Provider（如 deepseek）平铺全部：免费 = model_id 含 `-free` / `:free` 片段，或发现行原始字段标记免费（kilo `isFree`）——通用约定，代码无平台特判。
- **发现行透传原始布尔/数值字段**（`flags`，如 kilo 的 `isFree`），供免费判定与后续展示。
- **平台组标签**：默认端点无对应预设时回退端点域名（如 `api.kilo.ai`），避免裸协议名误导。
- **探测健壮性**：`/models` 探测网络层异常重试一次（出网经本地代理常见间歇性失败），最终失败记 warning 日志（此前零日志不可诊断），错误消息带异常类名。

## Impact

- 部署侧：五份配置（deploy/config.docker.yaml、config.example.yaml、config.prod.example.yaml 及两份本地未跟踪配置）默认模型切 kilo；此前 yaml 目录中的免费条目不再默认出现。
- 既有会话若指向旧目录 id（如 hy3-free），运行时按既有回退逻辑落到默认模型。
- kilo 无 Key 访问为未文档化行为：若 kilo 开始校验，默认链路（含记忆抽取、压缩）受影响，恢复方式为配置免费注册的 Key。
