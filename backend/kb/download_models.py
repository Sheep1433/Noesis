"""下载 RAGFlow DeepDoc ONNX 权重到配置的 model_dir。

用法（在 backend 目录）::

    uv run python -m kb.download_models
    uv run python -m kb.download_models /custom/model/dir
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from kb.document_parse.deepdoc_config import ensure_model_dir, list_required_model_files

DEEPDOC_REPO = "InfiniFlow/deepdoc"
XGB_REPO = "InfiniFlow/text_concat_xgb_v1.0"
DEEPDOC_FILES = ("layout.onnx", "det.onnx", "rec.onnx", "tsr.onnx", "ocr.res")
XGB_FILE = "updown_concat_xgb.model"


def _download(repo_id: str, filename: str, target_dir: Path) -> None:
    from huggingface_hub import hf_hub_download

    target = target_dir / filename
    if target.is_file():
        print(f"  SKIP {filename}")
        return
    print(f"  DOWNLOAD {filename} ...")
    hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir=str(target_dir),
        endpoint=os.environ.get("HF_ENDPOINT", "https://huggingface.co"),
    )
    print(f"  OK {filename}")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    target_dir = ensure_model_dir()
    if args:
        target_dir = Path(args[0]).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)

    print(f"Target: {target_dir}")
    for name in DEEPDOC_FILES:
        _download(DEEPDOC_REPO, name, target_dir)
    _download(XGB_REPO, XGB_FILE, target_dir)

    missing = [name for name in list_required_model_files() if not (target_dir / name).is_file()]
    if missing:
        print(f"ERROR: 仍缺少: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("All DeepDoc models ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
