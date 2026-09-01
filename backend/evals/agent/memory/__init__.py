"""Noesis 记忆召回行为评测（Agentic recall）。

入口：``uv run python -m evals.agent.memory``。runner 在 ``__main__``
延迟导入——包级导入 noesis 会在 runpy 设置 sys.argv[0] 前触发全局
CLI 参数解析（见 noesis.config.env 的 evals 豁免判定）。
"""
