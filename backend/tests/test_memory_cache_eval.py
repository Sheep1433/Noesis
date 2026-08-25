from evals.memory_cortex.cache_eval import _bulletin


def test_cache_eval_uses_canonical_bulletin_hashes() -> None:
    first = _bulletin("Use one switch.")
    same = _bulletin("Use one switch.")
    changed = _bulletin("Use the revised switch.")
    assert first.text == same.text
    assert first.bulletin_hash == same.bulletin_hash
    assert first.bulletin_hash != changed.bulletin_hash
