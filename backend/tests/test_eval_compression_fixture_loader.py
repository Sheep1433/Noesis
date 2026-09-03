"""压缩评测 fixture / probe 加载测试。"""

from evals.compression.fixture_loader import list_fixture_ids, load_fixture, load_probes

PROBE_TYPES = {"recall", "artifact", "continuation", "decision"}
# 合成 fixture（入库）；fixtures/real/ 下的真实导出 gitignored，数量不定
SYNTHETIC_IDS = {"debug_session", "feature_impl", "config_build"}


def test_list_and_load_fixtures():
    ids = set(list_fixture_ids())
    assert SYNTHETIC_IDS <= ids  # 真实导出（real/）存在时也会列出
    for fid in SYNTHETIC_IDS:
        fixture = load_fixture(fid)
        assert fixture["id"] == fid
        assert fixture.get("compress_options", {}).get("force") is True
        probes = load_probes(fid)
        assert probes["fixture_id"] == fid
        assert 8 <= len(probes["probes"]) <= 12
        types = {p["type"] for p in probes["probes"]}
        assert len(types & PROBE_TYPES) >= 2


def test_load_fixture_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.compression.fixture_loader.FIXTURES_DIR", tmp_path)
    monkeypatch.setattr("evals.compression.fixture_loader.REAL_FIXTURES_DIR", tmp_path / "real")
    import pytest

    with pytest.raises(FileNotFoundError):
        load_fixture("nope")
