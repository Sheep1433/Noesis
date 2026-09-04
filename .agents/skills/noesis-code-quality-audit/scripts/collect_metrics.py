#!/usr/bin/env python3
"""Collect deterministic, dependency-free code-quality metrics for Noesis.

This script reports signals only. It does not edit files and does not decide
whether a small helper or compatibility layer is actually a defect.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


EXCLUDED_PARTS = {
    ".git",
    ".noesis",
    ".tmp",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    "_ragflow_compat",
}
SOURCE_ROOTS = (
    "backend/packages/noesis-core/src",
    "backend/server",
    "frontend/src",
)
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".vue", ".js", ".jsx", ".scss", ".css"}


@dataclass(frozen=True)
class PythonFileMetrics:
    path: Path
    functions: int
    small_functions: int
    long_functions: int
    max_function_span: int


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.parts)


def source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in SOURCE_ROOTS:
        directory = root / relative_root
        if not directory.is_dir():
            continue
        files.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix in SOURCE_SUFFIXES
            and not is_excluded(path.relative_to(root))
        )
    return sorted(set(files))


def python_metrics(path: Path, root: Path) -> PythonFileMetrics | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None

    spans: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            spans.append(end - node.lineno + 1)
    if not spans:
        return None
    return PythonFileMetrics(
        path=path.relative_to(root),
        functions=len(spans),
        small_functions=sum(span <= 8 for span in spans),
        long_functions=sum(span >= 50 for span in spans),
        max_function_span=max(spans),
    )


def print_file_metrics(files: list[Path], root: Path) -> None:
    suffix_counts = Counter(path.suffix.lstrip(".") for path in files)
    print("[files]")
    print("counts=" + ", ".join(f"{key}:{suffix_counts[key]}" for key in sorted(suffix_counts)))
    print("largest=")
    for path in sorted(files, key=lambda item: item.stat().st_size, reverse=True)[:20]:
        line_count = path.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
        print(f"  {line_count:5} {path.relative_to(root)}")


def print_python_metrics(files: list[Path], root: Path) -> None:
    metrics = [python_metrics(path, root) for path in files if path.suffix == ".py"]
    metrics = [item for item in metrics if item is not None]
    print("[python-functions]")
    print("ratio is a signal only; abstract methods and accessors need manual review")
    for item in sorted(
        metrics,
        key=lambda value: (
            value.small_functions / value.functions,
            value.long_functions,
            value.functions,
        ),
        reverse=True,
    )[:40]:
        ratio = item.small_functions / item.functions
        if item.functions < 8 and item.long_functions == 0:
            continue
        print(
            f"  {ratio:5.0%} funcs={item.functions:3} "
            f"small<=8={item.small_functions:3} long>=50={item.long_functions:2} "
            f"max_span={item.max_function_span:3} {item.path}"
        )


def print_text_signals(files: list[Path], root: Path) -> None:
    marker_pattern = re.compile(r"\b(TODO|FIXME|HACK|XXX|legacy|compat|temporary|deprecated)\b", re.I)
    broad_exception_pattern = re.compile(r"except\s+(?:Exception|BaseException)(?:\s+as\s+\w+)?\s*:")
    any_pattern = re.compile(r"\bAny\b|Dict\[[^\n]*Any|dict\[[^\n]*Any")
    markers = Counter()
    broad_exceptions = 0
    dynamic_types = 0
    for path in files:
        if path.suffix not in {".py", ".ts", ".tsx", ".vue", ".js", ".jsx"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        markers.update(match.group(1).lower() for match in marker_pattern.finditer(text))
        if path.suffix == ".py":
            broad_exceptions += len(broad_exception_pattern.findall(text))
            dynamic_types += len(any_pattern.findall(text))
        else:
            dynamic_types += len(re.findall(r"\bany\b|as unknown as", text))
    print("[text-signals]")
    print("markers=" + ", ".join(f"{key}:{markers[key]}" for key in sorted(markers)))
    print(f"broad_python_exceptions={broad_exceptions}")
    print(f"dynamic_type_tokens={dynamic_types}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    files = source_files(root)
    print(f"root={root}")
    print(f"source_files={len(files)}")
    print_file_metrics(files, root)
    print_python_metrics(files, root)
    print_text_signals(files, root)


if __name__ == "__main__":
    main()
