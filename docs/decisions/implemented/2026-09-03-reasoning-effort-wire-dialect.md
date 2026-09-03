# 决策：推理档位的 wire 形态按端点方言分流（Kilo 系网关只收嵌套 reasoning.effort）

状态：implemented
日期：2026-09-03

## 问题

推理档位统一以顶层 `reasoning_effort` 透传（OpenAI chat completions 官方参数）。实测发现 Kilo 网关（`api.kilo.ai`）对该参数直接拒绝：**单独**携带顶层 `reasoning_effort` 即返回 400 `"reasoning_effort" and "reasoning.effort" are both provided with conflicting values`——Kilo 把顶层参数归一成自己的 `reasoning.effort`，与网关侧注入的上游默认档位构成冲突对后整体拒绝（实测 4/4 确定性复现）。子 Agent 继承 turn 档位后首次 LLM 调用即崩（SUBAGENT_FAILED），且同一请求换嵌套 `reasoning.effort` 形态即可通过网关校验。

## 决策

`build_chat_model` 的档位注入收敛到 `_reasoning_wire_kwargs` 单点，按 base_url 主机名分流 wire 形态：

- 默认：顶层 `reasoning_effort`（OpenAI 官方 chat completions 只认这个形态）；
- `_NESTED_REASONING_HOSTS`（api.kilo.ai）：嵌套 `reasoning.effort`，经 `extra_body` 透传——不走 langchain-openai 的 `reasoning` 字段，该字段会把整条请求切到 Responses API（`_use_responses_api` 见 payload 含 `reasoning` 键即返回 True），兼容网关只提供 `/chat/completions`。

## 备选方案

- **全局改嵌套形态**：实现最简，但 OpenAI 官方 chat completions 端点不接受嵌套 `reasoning` 对象，会把官方端点打挂。否。
- **按域名全局配置开关 / 用户可配**：方言是端点事实不是用户偏好，配置面扩 UI 只为修一个网关 bug，成本倒挂。否；方言表就在工厂单点，新增实证方言改一处常量即可。
- **捕获 400 后换形态重试**：掩盖协议错误、拖慢首 token，且 400 属不可重试类，加特例破坏重试分类。否。

## 后果与代价

- 方言表是实证清单不是机制推断：新网关若同病需实测后加域名；误加会让只认顶层的端点收到嵌套形态。tokenrhythm.studio 曾被误判为 Kilo 同后端（错误 user_id 一致实为子 Agent 目录回退后直打 kilo 所致），已从方言表移除——其对两种格式的实际行为未实测，维持顶层原形态。
- 诊断方法沉淀：网关方言类问题用「公共 key 直接 curl 对照 + 本地 echo server 抓 wire payload」两步即可实锤，无需解密用户凭据；归因前先确认请求真正打到的端点（本次 400 的 user_id 与 kilo 一致，最初误读为 tokenrhythm 转发证据）。

另有一个排障中发现的独立缺陷（未在本记录收口）：子 Agent worker 在隔离 loop 编译时，「用户自定义模型快照」ContextVar 不跨线程传播，`slug/model_id` 又不在内置目录，导致子 Agent 静默回退默认目录模型（本次事故即因此打到 kilo-auto/free）。主链路同轮的 model_calls 元数据显示主 Agent 也编译为 kilo-auto/free，即主 Agent 同样未使用所选模型；两者都是模型保真问题，修复需单独立项。
