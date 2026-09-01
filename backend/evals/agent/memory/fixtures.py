"""记忆召回评测素材：应召回场景 + 评测用户记忆种子。

场景设计对齐 spec「召回纪律」：涉及用户偏好、历史决策、既往经验、
当前目标四类记忆线索的提问，Agent 应先经 search_memory 检索再产出。
静默漏召回是 Agentic 召回的已知失效模式，本评测量化其概率。
"""

from __future__ import annotations

from typing import Any

# 评测专用用户（记忆种子写入其用户数据目录，幂等 upsert）
EVAL_USER_ID = "eval-memory-recall"

# 记忆种子：四类各一条（label 与场景一一对应）
SEEDED_ENTRIES: list[dict[str, Any]] = [
    {
        "memory_type": "preference",
        "label": "文档格式",
        "slug_hint": "document-format",
        "description": "偏好表格化、简体中文输出；涉及文档/报告/说明输出时调用",
        "body": "文档输出一律表格化、简体中文。",
        "applicability": "撰写或生成文档、报告、说明时",
    },
    {
        "memory_type": "decision",
        "label": "包管理",
        "slug_hint": "package-manager",
        "description": "前端包管理器统一 pnpm；讨论前端工具链选型时调用",
        "body": "前端包管理器统一使用 pnpm（workspace 用 pnpm -w）。",
        "why": "安装速度与硬链接磁盘占用优于 npm/yarn",
    },
    {
        "memory_type": "experience",
        "label": "超时重试",
        "slug_hint": "timeout-retry",
        "description": "服务超时用指数退避重试有效；排查超时类故障时调用",
        "body": "服务超时问题用指数退避重试解决过多次，配合熔断防止雪崩。",
        "applicability": "外部服务调用超时、不稳定时",
    },
    {
        "memory_type": "goal",
        "label": "学习计划",
        "slug_hint": "nodejs-learning",
        "description": "用户在学 Node.js；规划学习建议或涉及个人计划时调用",
        "body": "在学 Node.js（2026-08 起），当前进度到异步编程篇。",
    },
]

# 应召回场景：query 含记忆线索，断言 Agent 调用 search_memory
RECALL_SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "preference-doc-format",
        "query": "我之前对文档输出格式有什么要求？直接给结论。",
        "expect_label": "文档格式",
    },
    {
        "id": "decision-package-manager",
        "query": "我们之前定过前端包管理器用哪个？为什么选它？",
        "expect_label": "包管理",
    },
    {
        "id": "experience-timeout-retry",
        "query": "上次服务超时的故障，我们最后是用什么办法解决的？",
        "expect_label": "超时重试",
    },
    {
        "id": "goal-learning",
        "query": "我最近在学什么？基于它给我接下来的学习建议。",
        "expect_label": "学习计划",
    },
]
