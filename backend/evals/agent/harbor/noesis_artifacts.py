"""Harbor Agent 产物读写（无 LangChain 依赖，供 Harbor 进程 import）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_run_summary(logs_dir: Path) -> dict[str, Any]:
    path = logs_dir / "noesis.txt"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
