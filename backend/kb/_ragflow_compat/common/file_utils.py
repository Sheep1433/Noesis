import os
from pathlib import Path


def get_project_base_directory(*args):
    from kb.document_parse.deepdoc_config import resolve_rag_project_base

    project_base = resolve_rag_project_base()
    if args:
        return os.path.join(project_base, *args)
    return project_base
