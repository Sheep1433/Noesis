"""Agent 场景入口（SuperAgent / QA / 故障运维 / 调试 MCP）。

Lazy: importing ``noesis.agents`` does not eagerly load agent implementations
(they import ``noesis.factory`` which imports middlewares under
``noesis.agents.middlewares`` — eager loading would create a circular import).
Use ``from noesis.agents import GeneralQAAgent`` to load on demand.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "FaultOperationAgent",
    "GeneralQAAgent",
    "SimpleMCPAgent",
    "SuperAgent",
]


def __getattr__(name: str) -> Any:
    if name == "GeneralQAAgent":
        from noesis.agents.common_qa import GeneralQAAgent
        globals()["GeneralQAAgent"] = GeneralQAAgent
        return GeneralQAAgent
    if name == "FaultOperationAgent":
        from noesis.agents.fault_operation import FaultOperationAgent
        globals()["FaultOperationAgent"] = FaultOperationAgent
        return FaultOperationAgent
    if name == "SimpleMCPAgent":
        from noesis.agents.simple_mcp import SimpleMCPAgent
        globals()["SimpleMCPAgent"] = SimpleMCPAgent
        return SimpleMCPAgent
    if name == "SuperAgent":
        from noesis.agents.super_agent import SuperAgent
        globals()["SuperAgent"] = SuperAgent
        return SuperAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
