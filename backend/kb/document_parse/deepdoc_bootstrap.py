"""注入 sys.path 与 RAGFlow 兼容模块（避免与 Noesis `common` 包冲突）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_bootstrapped = False


def _load_submodule(package: str, name: str, module_path: Path) -> ModuleType:
    full_name = f"{package}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {full_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _install_ragflow_compat(kb_dir: Path) -> None:
    compat = kb_dir / "_ragflow_compat"
    common_pkg = sys.modules.get("common")
    if common_pkg is None:
        raise RuntimeError("Noesis common 包尚未加载，无法注入 RAGFlow 兼容层")

    common_files = {
        "constants": compat / "common" / "constants.py",
        "file_utils": compat / "common" / "file_utils.py",
        "misc_utils": compat / "common" / "misc_utils.py",
        "settings": compat / "common" / "settings.py",
        "token_utils": compat / "common" / "token_utils.py",
    }
    for name, path in common_files.items():
        mod = _load_submodule("common", name, path)
        setattr(common_pkg, name, mod)

    rag_root = compat / "rag"
    if "rag" not in sys.modules:
        rag_pkg = ModuleType("rag")
        rag_pkg.__path__ = [str(rag_root)]  # type: ignore[attr-defined]
        sys.modules["rag"] = rag_pkg
    else:
        rag_pkg = sys.modules["rag"]

    rag_modules = {
        "nlp": rag_root / "nlp" / "__init__.py",
        "nlp.rag_tokenizer": rag_root / "nlp" / "rag_tokenizer.py",
        "utils.lazy_image": rag_root / "utils" / "lazy_image.py",
        "prompts.generator": rag_root / "prompts" / "generator.py",
    }
    for rel, path in rag_modules.items():
        full = f"rag.{rel}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[full] = module
        spec.loader.exec_module(module)
        parent_name, _, child = rel.partition(".")
        if child:
            parent = sys.modules.setdefault(f"rag.{parent_name}", ModuleType(f"rag.{parent_name}"))
            setattr(parent, child.split(".")[-1], module)
        else:
            setattr(rag_pkg, parent_name, module)


def ensure_deepdoc_bootstrap() -> None:
    global _bootstrapped
    if _bootstrapped:
        return

    kb_dir = Path(__file__).resolve().parents[1]
    compat_dir = kb_dir / "_ragflow_compat"
    for entry in (str(compat_dir), str(kb_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    import common  # noqa: F401 — 确保 Noesis common 已加载

    _install_ragflow_compat(kb_dir)

    from kb.document_parse.deepdoc_config import ensure_model_dir

    ensure_model_dir()
    _bootstrapped = True
