"""从 documents/ + golden/ 生成 promptfooconfig.yaml。

源数据（人工维护）：
  - testpoints/documents/prd_*.md   PRD 需求正文
  - testpoints/golden/prd_*.yaml    金标准测试点

生成物（勿手改，跑脚本覆盖）：
  - testpoints/promptfooconfig.yaml

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
EXPECTED_CASE_COUNT = 20

CONFIG_HEADER = dedent(
    """\
    # yaml-language-server: $schema=https://promptfoo.dev/config-schema.json
    description: Noesis 测试用例 Agent 测试点评测（L0 + point_coverage recall/precision）
    prompts:
    - '请根据需求文档生成测试场景与测试点'
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
      - type: python
        value: file://../shared/assertions.py:assert_point_coverage_recall
        metric: point_coverage_recall
      - type: python
        value: file://../shared/assertions.py:assert_point_coverage_precision
        metric: point_coverage_precision
    tests:
    """
)


def _golden_json(points: list[dict]) -> str:
    return json.dumps(points, ensure_ascii=False, indent=2) + "\n"


def _yaml_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def discover_document_ids() -> list[str]:
    if not DOCS_DIR.is_dir():
        raise FileNotFoundError(f"PRD 目录不存在: {DOCS_DIR}")
    ids = sorted(p.stem for p in DOCS_DIR.glob("prd_*.md"))
    if not ids:
        raise FileNotFoundError(f"{DOCS_DIR} 下未找到 prd_*.md")
    return ids


def _validate_dataset(doc_ids: list[str], golden: dict[str, list[dict[str, str]]]) -> None:
    if len(doc_ids) != EXPECTED_CASE_COUNT:
        raise SystemExit(f"期望 {EXPECTED_CASE_COUNT} 篇 PRD，当前 documents/ 有 {len(doc_ids)} 篇")
    doc_set = set(doc_ids)
    golden_set = set(golden)
    if doc_set != golden_set:
        missing = doc_set - golden_set
        extra = golden_set - doc_set
        raise SystemExit(f"documents/ 与 golden/*.yaml item_id 不一致: missing={missing} extra={extra}")


def _render_test(item_id: str, *, golden: dict[str, list[dict[str, str]]]) -> str:
    points = golden[item_id]
    gjson = _golden_json(points)
    return "\n".join(
        [
            f"- description: {item_id}",
            "  metadata:",
            f"    item_id: {item_id}",
            "  vars:",
            f"    item_id: {item_id}",
            f"    document_path: documents/{item_id}.md",
            f"    golden_test_points_json: {_yaml_str(gjson)}",
        ]
    )


def main() -> None:
    golden = load_all_golden()
    doc_ids = discover_document_ids()
    _validate_dataset(doc_ids, golden)

    tests_yaml = "\n".join(_render_test(item_id, golden=golden) for item_id in doc_ids)
    CONFIG_PATH.write_text(CONFIG_HEADER + tests_yaml + "\n", encoding="utf-8")

    total_points = sum(len(golden[item_id]) for item_id in doc_ids)
    print(f"已生成 promptfoo 配置：{len(doc_ids)} 条用例，金标准测试点合计 {total_points} 条")
    for item_id in doc_ids:
        pts = golden[item_id]
        scenes = {p["scene_name"] for p in pts}
        print(f"  {item_id}: {len(pts)} points, {len(scenes)} scenes")
    print(f"PRD 源: {DOCS_DIR}")
    print(f"金标准源: {ROOT / 'golden'}")
    print(f"配置: {CONFIG_PATH}")


if __name__ == "__main__":
    main()
