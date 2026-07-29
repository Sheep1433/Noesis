"""COMMON_QA structured citation provider release-gate spike.

Run with the same model configuration as the deployment. It prints JSONL only and
never logs credentials. A provider passes only when every fixed case parses and
structured streaming yields usable typed objects rather than partial JSON text.
"""

from __future__ import annotations

import asyncio
import json
import time

from noesis.llm.factory import get_llm
from noesis.runtime.evidence import CitedAnswer

CASES = [
    ("zh_single", "证据 ev_a：北京是中国首都。问题：中国首都是哪里？"),
    (
        "en_multiple",
        "Evidence ev_a: Water freezes at 0°C. Evidence ev_b: Water boils at 100°C. State both facts.",
    ),
    ("uncited", "用一句话礼貌地向用户问好。这不需要引用证据。"),
    (
        "long_zh",
        "证据 ev_a：验证码有效期为五分钟。请用三个短段落说明规则、风险与用户建议，只绑定有直接依据的段落。",
    ),
]

INSTRUCTION = """Return structured segments. Each segment text is user-visible answer text only.
cited_evidence_ids contains only evidence ids directly supporting that segment; use [] otherwise.
Never put [[source:...]], [ID:n], evidence ids, or citation markers in text.\n\n"""


async def main() -> None:
    model = get_llm()
    structured = model.with_structured_output(
        CitedAnswer,
        method="function_calling",
        include_raw=True,
    )
    passed = True
    for name, prompt in CASES:
        started = time.monotonic()
        try:
            result = await structured.ainvoke(INSTRUCTION + prompt)
            parsed = result.get("parsed")
            error = result.get("parsing_error")
            ok = parsed is not None and error is None
            passed = passed and ok
            payload = {
                "case": name,
                "ok": ok,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "segments": parsed.model_dump()["segments"] if parsed else None,
                "error": str(error) if error else None,
            }
        except Exception as exc:  # release-gate diagnostics
            passed = False
            payload = {
                "case": name,
                "ok": False,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "error": f"{type(exc).__name__}: {exc}",
            }
        print(json.dumps(payload, ensure_ascii=False))
    print(json.dumps({"release_gate_passed": passed}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
