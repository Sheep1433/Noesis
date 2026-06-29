"""生成测试点评测集：20 篇 PRD + promptfooconfig tests 段。

用法（在 backend 目录）：
  uv run python evals/case/testpoints/generate_eval_dataset.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from evals.case.testpoints.golden_loader import load_all_golden

FIXED_PROMPT = "请根据需求文档生成测试场景与测试点"

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "documents"
CONFIG_PATH = ROOT / "promptfooconfig.yaml"

CONFIG_HEADER = dedent(
    f"""\
    # yaml-language-server: $schema=https://promptfoo.dev/config-schema.json
    description: Noesis 测试用例 Agent 测试点 评测（L0 + point_coverage recall/precision）
    prompts:
    - '{FIXED_PROMPT}'
    providers:
    - id: file://provider.py
      label: test-case-testpoints
      config:
        pythonExecutable: ../shared/run-python.sh
    evaluateOptions:
      maxConcurrency: 1
    defaultTest:
      options:
        provider: test-case-testpoints
      assert:
      - type: python
        value: file://../shared/assertions.py:assert_l0
        metric: l0
      - type: llm-rubric
        value: |
          你是测试设计评审员。根据 Agent 输出与金标准测试点，计算 **point_coverage_recall**（测试点覆盖召回率）。

          ## Agent 输出（JSON）
          {{output}}

          ## 金标准测试点
          {{golden_test_points_json}}

          ## 评分规则
          1. 从 Agent 输出的 JSON 中读取 `state.scenes_testpoints` 作为「生成测试点」；若为空或解析失败，score=0.0。
          2. 对每条金标准测试点，判断是否存在至少一条「生成测试点」在 **point_name 语义上覆盖** 该金标准所表达的 PRD 需求（scene_name 不一致可接受，但生成点须能反映同一需求点）。
          3. **无部分计分**：每条金标准要么计为已覆盖（1），要么未覆盖（0），不允许 0.5 等部分覆盖。
          4. **score** = 已覆盖金标准条数 / 金标准总条数，取值 0.0～1.0。
          5. **pass** 始终为 true（本指标仅汇报分数，不设通过阈值门禁）。
          6. 在 reason 中简要说明覆盖情况与 recall 计算依据。

          仅输出 JSON（不要其它文字）：
          ```json
          {{
            "reason": "<覆盖分析>",
            "score": 0.0,
            "pass": true
          }}
          ```
        metric: point_coverage_recall
        provider:
          id: file://../shared/judge.py
          config:
            pythonExecutable: ../shared/run-python.sh
      - type: llm-rubric
        value: |
          你是测试设计评审员。根据 Agent 输出与金标准测试点，计算 **point_coverage_precision**（测试点覆盖精确率）。

          ## Agent 输出（JSON）
          {{output}}

          ## 金标准测试点
          {{golden_test_points_json}}

          ## 评分规则
          1. 从 Agent 输出的 JSON 中读取 `state.scenes_testpoints`，展平各 scene 的 `test_points` 作为「生成测试点」；若为空或解析失败，score=0.0。
          2. 判断每条「生成测试点」是否**有效**（须满足其一）：
             - 语义上能对应至少一条金标准测试点；或
             - 被 PRD 文档内容**明确支持**（非泛泛而谈、非仅重复同一意图的变体）
          3. **无效情形**：仅与金标准属同一业务域但无法对应具体需求；与其它生成点明显重复；凭空编造 PRD 未提及的规则。
          4. **score** = 有效生成测试点条数 / 生成测试点总条数，取值 0.0～1.0。
          5. **pass** 始终为 true（本指标仅汇报分数，不设通过阈值门禁）。
          6. 在 reason 中简要说明有效/无效判定与 precision 计算依据。

          仅输出 JSON（不要其它文字）：
          ```json
          {{
            "reason": "<精确率分析>",
            "score": 0.0,
            "pass": true
          }}
          ```
        metric: point_coverage_precision
        provider:
          id: file://../shared/judge.py
          config:
            pythonExecutable: ../shared/run-python.sh
    tests:
    """
)


def _golden_json(points: list[dict]) -> str:
    return json.dumps(points, ensure_ascii=False, indent=2) + "\n"


def _yaml_str(value: str) -> str:
    """promptfoo vars 内多行 JSON 字符串。"""
    return json.dumps(value, ensure_ascii=False)


# fmt: off
DATASET: list[dict] = [
    {
        "item_id": "prd_001",
        "description": "账号密码登录与验证码",
        "document": dedent("""\
            # 用户登录模块需求规格说明

            | 版本 | 日期 | 作者 | 状态 |
            |------|------|------|------|
            | V2.0 | 2026-03-15 | 身份认证组 | 已评审 |

            ## 1. 背景与目标

            统一身份认证网关（UAM）面向 ToB 企业客户 SaaS 控制台。本模块实现 Web 端账号密码登录，配合图形验证码降低撞库风险。不包含自助注册、找回密码（见 UAM-REG）、OAuth 第三方登录（见 UAM-OAUTH）。

            ## 2. 术语

            | 术语 | 说明 |
            |------|------|
            | captcha_id | 验证码会话标识，5 分钟有效 |
            | 记住我 | 勾选后会话 Cookie 延长至 7 天（策略由 UAM-SESSION 执行） |

            ## 3. 功能需求

            ### 3.1 登录页 `/login`

            - 必填：用户名（手机号或工号）、密码、4 位字母数字混合图形验证码
            - 可选：「记住我」勾选框（默认不勾选，透传至会话服务）
            - 页面须展示《隐私政策》与《用户协议》链接

            ### 3.2 正常流程

            1. 打开登录页 → 后端下发 captcha_id 与验证码图片
            2. 用户提交表单 → 校验验证码未过期 → 校验用户名密码 → 签发 JWT
            3. 跳转 `/dashboard`；写入审计日志（时间、IP、设备指纹脱敏、登录方式=password）

            ### 3.3 异常与文案（须严格一致）

            | 场景 | 提示文案 |
            |------|----------|
            | 用户名或密码错误 | 「用户名或密码错误」（禁止区分用户名不存在/密码错误） |
            | 验证码错误 | 「验证码错误，请重新输入」 |
            | 验证码过期 | 「验证码已过期，请刷新」；须提供刷新按钮 |
            | 账号锁定 | 「账号已锁定，请 15 分钟后重试或联系管理员」 |

            ### 3.4 安全策略

            - 同一用户名连续失败 **5 次**（密码错误、验证码错误均计入）锁定 **15 分钟**
            - 锁定期间密码正确亦拒绝登录
            - 传输强制 HTTPS；日志禁止明文密码；密码后端 bcrypt 存储

            ## 4. 非功能需求

            - 登录接口 P99 < 800ms（不含短信）
            - 验证码图片 alt 文本为「验证码图片」；刷新按钮 aria-label「刷新验证码」
            - 支持 Chrome 90+、Edge 90+、Safari 15+

            ## 5. 不在范围

            支付、订单、企业微信扫码、短信验证码登录。
            """),
    },
    {
        "item_id": "prd_002",
        "description": "企业微信扫码登录",
        "document": dedent("""\
            # 企业微信 OAuth 扫码登录需求规格

            | 版本 | 日期 | 模块 |
            |------|------|------|
            | V1.3 | 2026-02-28 | UAM-OAUTH-WEWORK |

            ## 1. 概述

            在保留账号密码登录前提下，新增企业微信扫码登录。用户扫码授权后，系统完成 OAuth2 code 换票、本地用户绑定与 JWT 签发。不涉及支付与订单。

            ## 2. 角色

            - **企业管理员**：配置 CorpID、AgentID、Secret，启用「企业微信登录」
            - **终端用户**：已安装企业微信且在企业通讯录；首次须绑定本地账号

            ## 3. 主流程

            1. 登录页展示二维码（有效期 **2 分钟**，超时自动刷新）
            2. 企业微信扫码并确认授权
            3. 回调 `/api/auth/wework/callback?code=xxx`
            4. 后端 code 换 userid；已绑定则直接登录；未绑定跳转绑定页

            ### 3.1 首次绑定

            - 输入工号 + 本地密码完成 **首次绑定本地账号**
            - 绑定须 **二次验证本地密码**
            - 绑定成功后自动登录并写审计日志

            ## 4. 异常流程

            | 编号 | 场景 | 期望 |
            |------|------|------|
            | E1 | 用户取消扫码 | Toast「已取消扫码」，停留登录页 |
            | E2 | 二维码过期 | 「二维码已过期，请刷新」 |
            | E3 | 工号不存在或密码错误 | 明确提示，不泄露他人信息 |
            | E4 | 企业未开通应用 | 「企业未授权，请联系管理员」 |

            ## 5. 安全

            - code 一次性，5 分钟内有效
            - 回调 URL 白名单：`https://{domain}/api/auth/wework/callback`
            - 审计：扫码时间、userid、绑定结果、IP

            ## 6. 验收

            - 已绑定用户 3 步内进入首页
            - 未绑定用户未完成绑定无法访问业务 API（401）
            """),
    },
    {
        "item_id": "prd_003",
        "description": "会话管理与登出",
        "document": dedent("""\
            # 会话管理与登出需求（UAM-SESSION）

            | 版本 | 日期 |
            |------|------|
            | V1.1 | 2026-01-20 |

            ## 1. 目标

            定义登录后会话生命周期、记住我策略、主动登出与多端一致性。适用于 Web 控制台与移动端 H5 共用同一账号体系。

            ## 2. 会话策略

            | 模式 | Access Token 有效期 | Refresh Token | Cookie |
            |------|---------------------|---------------|--------|
            | 默认 | 2 小时 | 7 天 | HttpOnly + Secure + SameSite=Lax |
            | 记住我 | 2 小时 | **30 天** | 同上，Refresh 延长至 30 天 |

            - 「记住我 7 天免登录」指 Refresh Token 有效窗口为 7×24h（产品对外文案）
            - Access Token 过期前 5 分钟客户端可静默刷新

            ## 3. 登出

            ### 3.1 主动登出

            - 用户点击「退出登录」→ 服务端吊销当前 Refresh Token
            - **登出后其它标签页失效**：同一浏览器其它 Tab 在下次 API 请求时收到 401，跳转登录页
            - 移动端同步清除本地 Token

            ### 3.2 被动失效

            - 管理员强制下线：5 分钟内全端失效
            - 密码修改：吊销该用户全部 Refresh Token

            ## 4. 并发会话

            - 同一账号最多 **5 个** 活跃 Refresh Token（设备维度）
            - 第 6 次登录踢掉最早未活跃设备，并邮件通知用户

            ## 5. 非功能

            - Token 刷新接口 P99 < 200ms
            - 登出须幂等，重复调用返回 204

            ## 6. 不在范围

            单点登录联邦、LDAP 同步。
            """),
    },
    {
        "item_id": "prd_004",
        "description": "文件上传格式与大小",
        "document": dedent("""\
            # 通用文件上传组件需求

            | 版本 | 模块 | 状态 |
            |------|------|------|
            | V3.0 | DOC-UPLOAD | 已定稿 |

            ## 1. 背景

            知识库、工单、审批流等多业务复用统一上传组件。本需求定义客户端校验、服务端二次校验与错误提示，不包含病毒扫描（见 DOC-SCAN）。

            ## 2. 支持格式

            白名单扩展名（小写）：`pdf, doc, docx, xls, xlsx, ppt, pptx, png, jpg, jpeg, gif, zip`

            - **不支持格式提示**：「不支持的文件格式，请上传 pdf/doc/xls/png 等」
            - 双重扩展名 `report.pdf.exe` 按最后一段判定，拒绝并提示不支持

            ## 3. 大小限制

            | 场景 | 单文件上限 | 批量 |
            |------|-----------|------|
            | 普通上传 | **20MB** | 最多 10 个，合计 100MB |
            | 头像 | 2MB | 1 个 |

            - **超过20MB拦截**：前端阻止选择并 Toast「单文件不能超过 20MB」
            - 服务端须二次校验 Content-Length，超限返回 413

            ## 4. 边界与异常

            - 空文件（0 字节）：「文件内容为空，请重新选择」
            - 文件名超过 200 字符：截断或拒绝（配置项，默认拒绝）
            - 上传中断：展示失败态，支持重试同一文件

            ## 5. 存储

            - 对象存储路径：`/{tenant_id}/{yyyy}/{mm}/{uuid}_{sanitized_name}`
            - 返回 file_id、mime、size、sha256

            ## 6. 非功能

            - 20MB 文件上传 P99 < 30s（内网）
            - 进度条精度 1%，支持取消
            """),
    },
    {
        "item_id": "prd_005",
        "description": "大文件分片上传",
        "document": dedent("""\
            # 大文件分片与断点续传需求

            | 版本 | 依赖 |
            |------|------|
            | V2.1 | DOC-UPLOAD V3.0 |

            ## 1. 概述

            当单文件超过 **5MB** 时自动启用分片上传；小文件仍走直传。适用于内网大附件、视频课件等场景。

            ## 2. 分片规则

            - 分片大小：5MB（最后一片可小于 5MB）
            - 并发上传分片数：默认 3，可配置 1～5
            - 客户端计算 whole-file MD5，init 接口上报

            ### 2.1 超过5MB启用分片

            - 选择文件后若 size > 5MB，UI 切换为「分片上传模式」
            - 展示分片进度：已传片数/总片数

            ## 3. 断点续传

            - init 返回 upload_id；服务端记录已收分片 bitmap
            - 网络中断后重连：query 已传分片，仅补传缺失片
            - upload_id 24 小时过期，过期须重新 init

            ## 4. 合并

            - 全部分片成功后调用 merge
            - **合并失败可重试**：最多 3 次指数退避；仍失败提示「文件合并失败，请重新上传」
            - merge 成功后校验 MD5，不一致删除对象并报错

            ## 5. 取消与清理

            - 用户取消：abort upload_id，异步清理已传分片
            - 定时任务清理超过 48h 未完成 upload_id

            ## 6. 指标

            - 100MB 文件内网 P99 完成 < 3min
            - 单片失败自动重试 2 次
            """),
    },
    {
        "item_id": "prd_006",
        "description": "文件安全扫描与隔离",
        "document": dedent("""\
            # 文件安全扫描与隔离需求（DOC-SCAN）

            | 版本 | 集成 |
            |------|------|
            | V1.0 | ClamAV + 自建 YARA 规则 |

            ## 1. 流程

            上传 merge 完成后文件进入 **扫描队列**（异步）。扫描完成前状态为 `scanning`，禁止下载。

            ## 2. 扫描结果

            | 结果 | 状态 | 用户可见 |
            |------|------|----------|
            | 清洁 | active | 正常下载/预览 |
            | 可疑 | quarantine | 「文件待复核，暂不可用」 |
            | 高危 | isolated | 「文件存在安全风险，已隔离」 |

            ### 2.1 高危文件隔离

            - 命中木马、宏病毒、双后缀 executable → **高危文件隔离**
            - 隔离文件移入隔离桶，原 file_id 保留，状态 `isolated`

            ### 2.2 隔离文件不可下载

            - 下载/预览 API 对 `isolated` 返回 403 + 统一文案
            - 分享链接同样失效

            ## 3. 管理员

            - 安全管理员可「放行」或「永久删除」
            - 放行须填写原因并二次审批（两人规则）

            ## 4. SLA

            - 50MB 以内文件 5 分钟内出结果
            - 扫描失败重试 3 次，仍失败标记 `scan_failed` 人工介入

            ## 5. 审计

            - 记录：file_id、规则名、扫描引擎版本、操作人
            """),
    },
    {
        "item_id": "prd_007",
        "description": "请假审批流程",
        "document": dedent("""\
            # 员工请假审批流程需求

            | 版本 | 系统 | 状态 |
            |------|------|------|
            | V2.0 | OA-LEAVE | 已上线迭代 |

            ## 1. 请假类型

            年假、事假、病假、调休。各类型余额由 HR 系统同步，每日 02:00 增量。

            ## 2. 提交规则

            ### 2.1 年假

            - **年假提前1天提交**：开始日期早于当前自然日则拒绝，「年假须至少提前 1 个工作日申请」
            - 单次最多 15 天；跨月须拆分（可选，默认允许连续）

            ### 2.2 附件

            - 病假 > 3 天须上传病假单 PDF/JPG

            ## 3. 审批链

            | 天数 | 审批人 |
            |------|--------|
            | ≤3 天 | 直属主管 |
            | 4～10 天 | 主管 + 部门经理 |
            | >10 天 | 主管 + 部门经理 + HRBP |

            - 任一级 **驳回须填原因**（≥5 字），否则无法提交驳回
            - 驳回后申请人可修改重新提交，生成新版本 v2

            ## 4. 冲突检测

            - 与已有 approved/pending 请假日期重叠：「所选日期与已有请假冲突」
            - 与部门关键岗位值守冲突：警告但可强制提交（须备注）

            ## 5. 通知

            - 提交、审批、驳回、撤销均发站内信 + 邮件（可配置关闭邮件）

            ## 6. 不在范围

            薪酬扣减计算、考勤打卡硬件对接。
            """),
    },
    {
        "item_id": "prd_008",
        "description": "采购审批分级",
        "document": dedent("""\
            # 采购申请与分级审批需求

            | 版本 | 模块 |
            |------|------|
            | V1.5 | PROC-REQ |

            ## 1. 概述

            员工发起采购申请，按金额走不同审批链，并与预算系统占用/释放联动。

            ## 2. 表单字段

            - 物品名称、规格、数量、预估单价、币种（默认 CNY）
            - 用途说明（≥20 字）
            - 期望交付日期
            - 供应商（可选）

            ## 3. 金额分级审批

            | 含税金额 | 审批链 |
            |----------|--------|
            | ≤1 万 | **1万以下部门经理审批** |
            | 1 万～5 万 | 部门经理 + 财务专员 |
            | 5 万～20 万 | 部门经理 + 财务经理 + 分管副总 |
            | >20 万 | 上述 + CEO |

            ## 4. 附件要求

            - 金额 ≥ 5000：**必须上传报价单PDF**（或多家比价单 zip）
            - 单附件 ≤10MB，最多 5 个

            ## 5. 预算

            - 提交时预占用预算，驳回/撤回释放
            - 超预算拒绝：「部门 Q2 预算不足，剩余 xxx 元」

            ## 6. 加签与转办

            - 审批人可加签一人，加签不改变原超时 SLA
            - 转办须在同一审批级别内

            ## 7. 验收

            - 审批全程留痕不可删
            - 导出 PDF 含水印与审批时间轴
            """),
    },
    {
        "item_id": "prd_009",
        "description": "稀疏需求 ToolHub",
        "document": dedent("""\
            # 【草稿】内部工具模块需求（信息不完整）

            > 状态：需求调研中，大量细节 **TBD**。测试设计须做 **合理假设** 并标注。

            ## 已知信息

            - 模块代号：ToolHub（内部效率工具集合）
            - 用户：研发与运维，首期 200 人
            - 能力方向：快捷入口聚合、常用脚本一键执行（脚本清单 **TBD**）
            - 明确 **不做** 支付、收银、对公付款

            ## 会议纪要摘录（2026-02-20）

            1. 首页可配置卡片，拖拽排序「可能有，待确认」
            2. 脚本执行是否审批 — **未定论**；开发倾向只读免审批、写操作要审批
            3. 权限：可能复用 RBAC 或单独角色表 — **待架构评审**
            4. 日志保留 90 天 — 未签字
            5. SSO 依赖统一网关，超时处理 **未写入**

            ## 非功能（未评审）

            - 可用性 99.5%？
            - 页面加载「尽量快」— 无指标

            ## 测试设计指引

            - 信息不足处列 **假设**（如默认只读脚本、沿用 RBAC）
            - 勿编造未定义业务规则；可标「待需求确认」
            - 至少覆盖：空状态、无权限、未知入口

            ## 示例假设（可引用）

            - **假设 A**：首期仅只读脚本，写操作置灰
            - **假设 B**：无配置时空状态引导联系管理员
            - **假设 C**：403 不暴露脚本列表

            ## 待补充 Backlog

            - [ ] 用户故事与线框图
            - [ ] 脚本白名单 API
            - [ ] 审批是否接 OA
            - [ ] 错误码表

            *本文档故意不完整，用于评测稀疏 PRD；字数已扩展但仍含 TBD。*
            """),
    },
    {
        "item_id": "prd_010",
        "description": "电商商品下单",
        "document": dedent("""\
            # 电商 C 端商品下单需求

            | 版本 | 渠道 |
            |------|------|
            | V4.2 | App + 小程序 + H5 |

            ## 1. 下单流程

            商品详情 → 加入购物车 / 立即购买 → 确认订单 → 支付（见 PAY-001）。

            ## 2. 购物车

            - 同一 SKU 合并数量，上限 99
            - 失效商品（下架/无货）灰色展示，不可勾选结算
            - 全选/反选；结算前校验勾选项库存

            ## 3. 库存

            - 提交订单 **预占库存** 15 分钟，超时释放
            - 库存不足：「商品库存不足，已为您调整数量」并回写可买上限
            - 秒杀 SKU 走独立库存池，不与普通池混用

            ## 4. 优惠券

            - 每单最多 1 张店铺券 + 1 张平台券
            - 不可叠加：两种「满减」类互斥
            - 过期券不可选，列表自动过滤

            ## 5. 收货地址

            - 须含省市区、详细地址、手机号（11 位校验）
            - 港澳台地址暂不支持，提示「当前地区不支持配送」

            ## 6. 订单快照

            - 保存商品标题、单价、主图 URL、规格快照，不随后台改价变化

            ## 7. 指标

            - 创建订单接口 P99 < 500ms
            - 幂等键 Idempotency-Key 防重复下单
            """),
    },
    {
        "item_id": "prd_011",
        "description": "订单支付与退款",
        "document": dedent("""\
            # 订单支付与退款需求（PAY-001）

            | 版本 | 合规 |
            |------|------|
            | V3.1 | PCI 外包 |

            ## 1. 支付方式

            - 微信支付（App/JSAPI/Native）
            - 支付宝（App/WAP）
            - 企业对公转账（B 端，人工确认）

            ## 2. 支付流程

            1. 创建支付单，金额与订单应付一致（单位：分）
            2. 调起收银台，等待回调
            3. 回调验签 → 更新订单 paid → 通知履约

            ### 2.1 支付超时

            - 待支付订单 **30 分钟** 未付自动关单
            - 关单释放库存与优惠券

            ## 3. 退款

            | 类型 | 规则 |
            |------|------|
            | 整单退款 | 发货前用户/客服可发起 |
            | 部分退款 | 已发货仅退差价/退部分商品，须财务审核 |
            | 原路退回 | 7 个工作日内到账（文案提示） |

            - 重复退款请求幂等，同一 refund_no 只处理一次

            ## 4. 异常

            - 支付成功回调延迟：主动查单补偿，最多查 24h
            - 金额不一致：拒绝入账并告警

            ## 5. 对账

            - 每日 02:00 拉渠道账单与本地流水核对
            - 差异单进入 reconciliation 工单

            ## 6. 安全

            - 回调 IP 白名单 + 签名
            - 禁止客户端传入支付金额
            """),
    },
    {
        "item_id": "prd_012",
        "description": "会员积分体系",
        "document": dedent("""\
            # 会员积分体系需求

            | 版本 | 会员等级 |
            |------|----------|
            | V2.0 | 普通/银卡/金卡/黑卡 |

            ## 1. 积分获取

            - 消费 1 元 = 1 基础积分（完成支付后 T+1 到账）
            - 等级倍率：银 1.2、金 1.5、黑 2.0
            - 签到、活动赠送走 campaign 子账户，可设有效期

            ## 2. 积分过期

            - 自然年清零：每年 12-31 23:59 清除 **上一年度获得且未使用** 积分
            - 提前 30 天站内信提醒

            ## 3. 抵扣规则

            - 下单可抵现金，**抵扣不超过应付 50%**
            - 最低使用 100 积分起
            - 积分与某些券互斥（配置表维护）

            ## 4. 回滚

            - 订单全额退款：退回已用积分；撤销已发积分（不足则扣至 0）
            - 部分退款：按比例回滚

            ## 5. 账户

            - 积分流水不可删，仅可冲正
            - 黑卡用户人工调账须双人复核

            ## 6. 查询

            - 用户端展示：可用、即将过期（30 天内）、明细筛选
            """),
    },
    {
        "item_id": "prd_013",
        "description": "消息通知中心",
        "document": dedent("""\
            # 统一消息通知中心需求

            | 版本 | 渠道 |
            |------|------|
            | V1.4 | 站内信/邮件/短信/App Push |

            ## 1. 消息类型

            交易、审批、营销、系统。营销类默认 **关闭 Push**，需用户 opt-in。

            ## 2. 发送流程

            业务方调用 `/notify/send` → 路由渠道 → 模板渲染 → 队列异步发送。

            ### 2.1 模板

            - 变量 `{{userName}}` `{{orderId}}` 缺失时 fail-fast，不发送
            - 短信模板须工信部备案号

            ## 3. 免打扰

            - 用户可设 22:00～08:00 **免打扰**；时段内非交易类延迟到 08:00
            - 交易类（支付成功）不受限

            ## 4. 重试

            - 短信/Push 失败指数退避重试 3 次
            - 邮件失败进 dead-letter，人工补发

            ## 5. 收件箱

            - 站内信 90 天归档，支持批量已读/删除
            - 未读角标上限 99+

            ## 6. 合规

            - 营销短信须含退订指令「回T退订」
            - 用户注销后 30 天内停止一切触达
            """),
    },
    {
        "item_id": "prd_014",
        "description": "数据权限与RBAC",
        "document": dedent("""\
            # 数据权限与 RBAC 需求

            | 版本 | 模型 |
            |------|------|
            | V2.3 | RBAC + 行级 DATA_SCOPE |

            ## 1. 角色

            - 系统预置：超级管理员、租户管理员、普通成员、只读审计
            - 租户可自定义角色，权限从预置集合勾选
            - **角色继承**：子角色权限 ⊆ 父角色，禁止权限提升绕过

            ## 2. 数据范围（行级）

            | 级别 | 可见数据 |
            |------|----------|
            | ALL | 租户全部 |
            | DEPT | 本部门及下级 |
            | SELF | 仅本人创建 |

            - 列表 API 自动注入 scope SQL，禁止客户端传 `scope` 覆盖

            ## 3. 越权

            - 水平越权：访问他人 resource_id 返回 404（非 403，防枚举）
            - 垂直越权：无 permission 返回 403 + 统一文案

            ## 4. 缓存

            - 用户权限 Redis 缓存 5 分钟
            - 角色变更 **立即失效** 该用户及同角色在线会话权限缓存

            ## 5. 审计

            - 拒绝访问记录：user_id、uri、required_permission
            - 导出敏感数据须 `data:export` 权限 + 二次验证

            ## 6. 迁移

            - 旧版 ACL 一次性迁移脚本，须可回滚
            """),
    },
    {
        "item_id": "prd_015",
        "description": "报表导出",
        "document": dedent("""\
            # 报表查询与异步导出需求

            | 版本 | 引擎 |
            |------|------|
            | V1.2 | ClickHouse + 对象存储 |

            ## 1. 查询

            - 列表分页，单页最大 500 行
            - 复杂报表走 OLAP，超时 60s 返回「请缩小范围或使用导出」

            ## 2. 异步导出

            - 预估 >5000 行 **强制异步**；用户收到站内信下载链接
            - 任务队列 FIFO，单用户并发导出 **最多 2 个**
            - 文件格式：xlsx、csv；大文件自动 zip

            ## 3. 有效期

            - 下载链接 **72 小时** 有效，过期需重新申请
            - 文件 OSS 7 天后物理删除

            ## 4. 脱敏

            - 手机号中间 4 位 *；身份证仅后 4 位
            - 导出须带 **水印**（工号+时间）；PDF 额外禁止复制（可选）

            ## 5. 权限

            - 导出字段受列级权限控制，无权限列不出现在文件
            - 导出动作记 audit_log

            ## 6. 限流

            - 同一报表 5 分钟内重复导出返回 429
            """),
    },
    {
        "item_id": "prd_016",
        "description": "库存管理与预警",
        "document": dedent("""\
            # WMS 库存管理与预警需求

            | 版本 | 仓库 |
            |------|------|
            | V3.0 | 中心仓 + 区域仓 |

            ## 1. 库存模型

            - SKU × 仓库维度；字段：可用、预占、在途、冻结
            - **可用 = 实物 - 预占 - 冻结**

            ## 2. 安全库存

            - 每个 SKU 设安全库存阈值（可批量导入）
            - 可用 < 安全库存 → **低库存预警** 通知采购（邮件+站内）
            - 支持按销量自动建议阈值（Beta，可关闭）

            ## 3. 调拨

            - 中心仓 → 区域仓调拨单，在途期间区域仓不可售
            - 收货确认后可用增加；拒收回滚

            ## 4. 盘点

            - 盘点冻结仓库 SKU 出入库
            - 盘盈盘亏须审批；差异 >0.5% 自动升级财务

            ## 5. 并发

            - 扣减乐观锁 version；冲突重试 3 次
            - 禁止负库存（可配置超卖开关，默认关）

            ## 6. 对接

            - 订单、采购、退货单统一走 inventory-service API
            """),
    },
    {
        "item_id": "prd_017",
        "description": "客服工单系统",
        "document": dedent("""\
            # 客服工单系统需求

            | 版本 | 渠道接入 |
            |------|----------|
            | V2.1 | 在线/chat/邮件/电话 |

            ## 1. 工单生命周期

            新建 → 待分派 → 处理中 → 待用户确认 → 已关闭 / 已取消

            ## 2. 优先级与 SLA

            | 优先级 | 首次响应 | 解决 |
            |--------|----------|------|
            | P0 | 15min | 4h |
            | P1 | 1h | 24h |
            | P2 | 4h | 72h |

            - SLA 超时标红并升级至值班长
            - 非工作时间 P0 仍计时（轮班）

            ## 3. 分派

            - 自动分派：按技能组、负载均衡
            - **转派** 须填原因；同一工单转派不超过 5 次

            ## 4. 用户侧

            - 关闭前须用户确认（48h 无回复自动关）
            - 关闭后推送 **满意度评价** 1～5 星 + 可选文字

            ## 5. 关联

            - 可关联订单号、用户 ID；从订单页一键建单带上下文

            ## 6. 质检

            - 10% 随机抽检；敏感词检测辱骂升级
            """),
    },
    {
        "item_id": "prd_018",
        "description": "合同电子签章",
        "document": dedent("""\
            # 合同电子签章需求

            | 版本 | CA |
            |------|-----|
            | V1.0 | 第三方 CA  SaaS |

            ## 1. 流程

            上传 PDF → 指定签署方与顺序 → 发起 → 各方签署 → 归档存证

            ## 2. 签署顺序

            - 支持 **顺序签** 与 **会签**（并行）
            - 顺序签：前一方未完成，后一方不可见待签文件

            ## 3. 意愿认证

            - 企业：对公打款 0.01 元验证 或 法人刷脸
            - 个人：短信 OTP + 刷脸二选一
            - 认证失败 5 次锁定 24h

            ## 4. 签章

            - 企业公章、合同章、法人章位置拖拽
            - 骑缝章自动分割 PDF 页数

            ## 5. 作废

            - 仅发起方可在 **全员签署前** 作废
            - 已完成合同作废须全员同意 + 留存作废协议

            ## 6. 存证

            - 哈希上链（联盟链）；提供出证报告 PDF
            - 文件 AES 加密存储，密钥 KMS 托管

            ## 7. 合规

            - 符合《电子签名法》可靠电子签名要求
            """),
    },
    {
        "item_id": "prd_019",
        "description": "API限流与熔断",
        "document": dedent("""\
            # API 网关限流与熔断需求

            | 版本 | 网关 |
            |------|------|
            | V2.0 | Kong + Redis |

            ## 1. 限流维度

            - 全局限流：集群 10w QPS
            - 租户限流：套餐配额（免费 100/min，Pro 2000/min）
            - 用户限流：单用户 60/min（防刷）
            - IP 限流：未登录 30/min

            ## 2. 算法

            - **滑动窗口** 计数，Redis Lua 原子
            - 超限返回 429，`Retry-After` 头（秒）

            ## 3. 熔断

            - 下游错误率 >50% 且 QPS>100，10s 窗口 → 打开熔断 30s
            - 半开探测：允许 10% 流量
            - 熔断响应 503 + 统一 JSON

            ## 4. 降级

            - 非核心接口（推荐、统计）可配置降级 stub
            - 核心写接口禁止降级

            ## 5. 白名单

            - 内部服务 mTLS 绕过租户限流
            - 运维临时提额须 ticket 审批，最长 24h

            ## 6. 观测

            - Prometheus 指标：rate_limit_drop_total、circuit_open
            - 租户连续 429 超 100 次/小时告警
            """),
    },
    {
        "item_id": "prd_020",
        "description": "多租户隔离",
        "document": dedent("""\
            # SaaS 多租户隔离需求

            | 版本 | 隔离模型 |
            |------|----------|
            | V3.2 | 共享库 tenant_id 行级 |

            ## 1. 租户上下文

            - 所有 API 从 JWT `tenant_id` 注入上下文
            - 禁止请求体传 tenant_id 切换租户
            - 后台 job 须显式 setTenant

            ## 2. 数据隔离

            - ORM 全局 Filter：`tenant_id = :current`
            - **跨租户访问** 返回 404
            - 缓存 key 前缀 `t:{tenant_id}:`

            ## 3. 资源配额

            | 资源 | 免费 | 企业 |
            |------|------|------|
            | 用户数 | 10 | 不限 |
            | 存储 | 5GB | 合同约定 |
            | API | 见 LIMIT-001 |

            - 超配额创建拒绝并引导升级

            ## 4. 定制

            - 租户级 branding：Logo、主题色、域名 CNAME
            - 配置隔离，互不可见

            ## 5.  offboarding

            - 注销租户：软删 30 天 → 硬删
            - **数据导出**：提供 JSON+文件打包，72h 内生成

            ## 6. 合规

            - 租户数据驻留区域可选（cn/eu），不可跨区复制
            - 审计日志含 tenant_id，保留 180 天
            """),
    },
]
# fmt: on


def _render_test(entry: dict, *, golden: dict[str, list[dict[str, str]]]) -> str:
    item_id = entry["item_id"]
    points = golden[item_id]
    gjson = _golden_json(points)
    lines = [
        f"- description: {item_id}",
        "  metadata:",
        f"    item_id: {item_id}",
        "  vars:",
        f"    item_id: {item_id}",
        f"    document_path: documents/{item_id}.md",
        f"    golden_test_points_json: {_yaml_str(gjson)}",
    ]
    return "\n".join(lines)


def _validate_golden(golden: dict[str, list[dict[str, str]]]) -> None:
    dataset_ids = {e["item_id"] for e in DATASET}
    golden_ids = set(golden)
    if dataset_ids != golden_ids:
        missing = dataset_ids - golden_ids
        extra = golden_ids - dataset_ids
        raise SystemExit(f"golden/*.yaml 与 DATASET item_id 不一致: missing={missing} extra={extra}")


def main() -> None:
    if len(DATASET) != 20:
        raise SystemExit(f"期望 20 条评测样本，当前 {len(DATASET)}")

    golden = load_all_golden()
    _validate_golden(golden)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # 清理旧文档
    for old in DOCS_DIR.glob("*.md"):
        old.unlink()

    for entry in DATASET:
        doc_path = DOCS_DIR / f"{entry['item_id']}.md"
        doc_path.write_text(entry["document"].strip() + "\n", encoding="utf-8")

    tests_yaml = "\n".join(_render_test(e, golden=golden) for e in DATASET)
    CONFIG_PATH.write_text(CONFIG_HEADER + tests_yaml + "\n", encoding="utf-8")

    total_points = sum(len(golden[e["item_id"]]) for e in DATASET)
    print(f"已生成 {len(DATASET)} 篇 PRD，金标准测试点合计 {total_points} 条")
    for entry in DATASET:
        item_id = entry["item_id"]
        pts = golden[item_id]
        scenes = {p["scene_name"] for p in pts}
        print(f"  {item_id}: {len(pts)} points, {len(scenes)} scenes")
    print(f"金标准源: {ROOT / 'golden'}")
    print(f"配置: {CONFIG_PATH}")


if __name__ == "__main__":
    main()
