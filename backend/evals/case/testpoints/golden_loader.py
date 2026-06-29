"""从 testpoints/golden/*.yaml 加载金标准测试点。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

MIN_SCENES = 3
MAX_SCENES = 6
MIN_POINTS = 20
MAX_POINTS = 50


def _flatten_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    for scene in scenes:
        scene_name = str(scene.get("scene_name") or "").strip()
        if not scene_name:
            raise ValueError("scene_name 不能为空")
        raw_points = scene.get("test_points") or []
        if not raw_points:
            raise ValueError(f"场景 {scene_name!r} 缺少 test_points")
        for tp in raw_points:
            if isinstance(tp, str):
                point_name = tp.strip()
            elif isinstance(tp, dict):
                point_name = str(tp.get("point_name") or "").strip()
            else:
                raise ValueError(f"场景 {scene_name!r} 的 test_points 项格式非法: {tp!r}")
            if not point_name:
                raise ValueError(f"场景 {scene_name!r} 存在空 point_name")
            points.append({"scene_name": scene_name, "point_name": point_name})
    return points


def load_golden_file(path: Path) -> tuple[str, list[dict[str, str]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: 根节点须为 mapping")
    item_id = str(data.get("item_id") or path.stem).strip()
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError(f"{item_id}: 缺少 scenes 列表")
    points = _flatten_scenes(scenes)
    return item_id, points


def validate_golden_points(item_id: str, points: list[dict[str, str]]) -> None:
    scenes = {p["scene_name"] for p in points}
    n_scenes, n_points = len(scenes), len(points)
    if not (MIN_SCENES <= n_scenes <= MAX_SCENES):
        raise ValueError(f"{item_id}: 场景数 {n_scenes} 不在 {MIN_SCENES}～{MAX_SCENES} 范围")
    if not (MIN_POINTS <= n_points <= MAX_POINTS):
        raise ValueError(f"{item_id}: 测试点数 {n_points} 不在 {MIN_POINTS}～{MAX_POINTS} 范围")


def load_all_golden(*, golden_dir: Path | None = None) -> dict[str, list[dict[str, str]]]:
    root = golden_dir or GOLDEN_DIR
    if not root.is_dir():
        raise FileNotFoundError(f"金标准目录不存在: {root}")

    by_id: dict[str, list[dict[str, str]]] = {}
    for path in sorted(root.glob("*.yaml")):
        item_id, points = load_golden_file(path)
        validate_golden_points(item_id, points)
        if item_id in by_id:
            raise ValueError(f"重复 item_id: {item_id}")
        by_id[item_id] = points
    if not by_id:
        raise ValueError(f"{root} 下未找到 *.yaml")
    return by_id
