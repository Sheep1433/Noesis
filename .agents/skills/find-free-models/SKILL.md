---
name: find-free-models
description: >-
  发现并验证可用的免费 LLM 端点（OpenCode Zen 网关 / models.dev 目录）。
  从 https://models.dev/api.json 拉取全部 provider 的模型，筛出 cost.input==0 且
  status==active 的免费模型，逐个用公共 API Key `public` 实测 chat/completions，
  报告真正可用者（含实际路由模型、上下文长度、端点）。模型会过期，每次现拉现测，不缓存。
  当用户提到 免费模型 / 薅羊毛 / opencode zen / models.dev / 找个免费的 LLM / 哪些模型还能白嫖
  时使用。
---

# 查找免费模型（OpenCode Zen / models.dev）

利用 OpenCode 的 Zen 网关对免费模型放行公共 API Key `public`、无需注册的特性，
**现拉现测**当前可用免费模型。模型会过期（21 个原免费模型已有 16 个 deprecated），
**禁止**记忆/缓存旧列表，每次都重新发现。

## 原理速记

- 源码 `packages/core/src/plugin/provider/opencode.ts:20`：无 API Key 时自动填 `"public"`，且只放行 `cost.input === 0` 的模型。
- 免费模型特征：`cost.input == 0 && status == "active"`。
- `models.dev/api.json` 是 OpenCode 的公开模型目录，含所有 provider 的模型定义与 `cost`/`status`。
- 必带 Headers：`HTTP-Referer: https://opencode.ai/` 与 `X-Title: opencode`，用于来源识别，缺失可能被拒。
- **数据可能被收集**用于厂商改进模型，禁止发敏感数据。

## 何时启用

- 用户问「有没有免费模型」「薅羊毛」「哪些模型还能白嫖」
- 用户要给某工具配一个零成本 LLM 端点
- 已知端点（OpenCode Zen / Kilo Gateway）疑似失效，需重新发现

## 已知端点（参考，可用性以实测为准）

| 端点 | Base URL | API Key | Header 要求 |
|------|----------|---------|------------|
| OpenCode Zen | `https://opencode.ai/zen/v1` | `public` | `HTTP-Referer: https://opencode.ai/`、`X-Title: opencode` |
| Kilo Gateway | `https://api.kilo.ai/api/gateway` | 不需要 | `HTTP-Referer: https://opencode.ai/`、`X-Title: opencode` |

## 执行流程

### Step 1 — 拉取目录，筛出候选

跑 `scripts/find_free_models.sh discover`，输出每个 provider 下 `cost.input==0 && status==active` 的模型，含 context 长度。

### Step 2 — 逐个实测可用性

跑 `scripts/find_free_models.sh test`，对每个候选发一条 `1+1=?`（max_tokens=20），
返回 `choices` 记为 ✓（打印实际路由模型 + 输出前 80 字），返回 `error` 记为 ✗。
带 reasoning 的模型输出可能在 `reasoning` 字段而非 `content`，脚本已兼容（输出标 `(reasoning)`）。

`scripts/find_free_models.sh`（无参数 = 全流程 discover+test）一键完成。

### Step 3 — 汇报

按 provider 分组列出**当前实测可用**的模型：模型 ID、实际路由模型、上下文长度、端点 Base URL。
明确标注「截至本次实测」的日期，并提醒模型会过期、敏感数据勿发。

## 注意

- **不缓存列表**：模型 `status` 会从 active 变 deprecated，每次现拉现测。
- **`big-pickle` 是别名**：曾与 `deepseek-v4-flash-free` 指向同一实际模型。
- **部分模型带 reasoning**：如 `nemotron-3-ultra-free`，输出在 `reasoning` 字段。
- **Headers 必加**：`HTTP-Referer` + `X-Title`，否则可能被拒。
- 超时统一 15s；测试请求体最小化（`max_tokens: 20`）避免误判限速。
