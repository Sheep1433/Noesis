"""评测数据集加载测试。"""

from evals.case.dataset import DEFAULT_DATASET, load_dataset, resolve_document_context


def test_load_dataset_count_and_unique_ids():
    items = load_dataset(DEFAULT_DATASET)
    assert len(items) >= 8
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))


def test_resolve_document_context():
    items = load_dataset(DEFAULT_DATASET)
    item = next(i for i in items if i["id"] == "tc_login_001")
    text = resolve_document_context(item, DEFAULT_DATASET.parent)
    assert "验证码" in text
