"""压缩评测 fixture / probe 加载测试。"""

from evals.compression.fixture_loader import list_fixture_ids, load_fixture, load_probes

PROBE_TYPES = {"recall", "artifact", "continuation", "decision"}


def test_list_and_load_fixtures():
    ids = list_fixture_ids()
    assert set(ids) == {"debug_session", "feature_impl", "config_build"}
    for fid in ids:
        fixture = load_fixture(fid)
        assert fixture["id"] == fid
        assert fixture.get("compress_options", {}).get("force") is True
        probes = load_probes(fid)
        assert probes["fixture_id"] == fid
        assert 8 <= len(probes["probes"]) <= 12
        types = {p["type"] for p in probes["probes"]}
        assert len(types & PROBE_TYPES) >= 2
