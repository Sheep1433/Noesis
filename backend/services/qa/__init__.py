"""Qa 编排包。

对外：``from services.qa import QaService, common_agent, ...``。
兼容：``services.qa_service``（旧 import / patch 路径）。
"""

from services.qa.helpers import (
    case_coordinator,
    common_agent,
    fault_agent,
    super_agent,
)
from services.qa.service import QaService

__all__ = [
    "QaService",
    "case_coordinator",
    "common_agent",
    "fault_agent",
    "super_agent",
]
