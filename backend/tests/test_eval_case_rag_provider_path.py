from unittest.mock import patch


def test_case_rag_eval_patch_target_uses_current_harness_namespace():
    marker = object()
    with patch("noesis.agents.case_generate.rag.QdrantConfig", marker):
        from noesis.agents.case_generate import rag

        assert rag.QdrantConfig is marker
