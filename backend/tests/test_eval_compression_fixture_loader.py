"""压缩评测 fixture / probe 加载测试。"""

import pytest

from evals.compression.fixture_loader import list_fixture_ids, load_fixture, load_probes

PROBE_TYPES = {"recall", "artifact", "continuation", "decision"}


def test_list_and_load_fixtures():
    """列出的每个 fixture 必须能加载、id 自洽、且带合规题库。

    fixtures/real/ 为真实导出（gitignored，数量随库存变化）——旧的合成
    小 fixture（消息数低于压缩保留尾）已删除，不再有固定 id 集合。
    """
    ids = set(list_fixture_ids())
    if not ids:
        # 真实导出只存在于导出过的机器上；CI / 全新克隆环境必然为空，跳过而非红
        pytest.skip("无真实 fixture（fixtures/real/ 需本地导出），跳过加载验证")
    for fid in ids:
        fixture = load_fixture(fid)
        assert fixture["id"] == fid
        assert isinstance(fixture.get("messages"), list) and fixture["messages"]
        probes = load_probes(fid)
        assert probes["fixture_id"] == fid
        assert len(probes["probes"]) >= 8
        types = {p["type"] for p in probes["probes"]}
        assert len(types & PROBE_TYPES) >= 1


def test_load_fixture_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.compression.fixture_loader.FIXTURES_DIR", tmp_path)
    monkeypatch.setattr("evals.compression.fixture_loader.REAL_FIXTURES_DIR", tmp_path / "real")
    import pytest

    with pytest.raises(FileNotFoundError):
        load_fixture("nope")
