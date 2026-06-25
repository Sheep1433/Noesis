"""promptfoo 评分 Provider：复用 Noesis get_llm() 供 llm-rubric 断言调用。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

_BACKEND = Path(__file__).resolve().parents[3]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from llm import get_llm


def call_api(
    prompt: str,
    options: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None,
):
    llm = get_llm()
    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return {"output": content}
