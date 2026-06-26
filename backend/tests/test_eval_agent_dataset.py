"""Agent 评测数据集加载测试。"""

from pathlib import Path

from evals.agent.perf.dataset import DEFAULT_DATASET, filter_items, load_dataset, resolve_workspace_seed

CATEGORIES = {
    "search_retrieval",
    "code_intelligence",
    "creative_synthesis",
    "safety_alignment",
}


def test_load_agent_dataset_count_and_ids():
    items = load_dataset(DEFAULT_DATASET)
    assert len(items) >= 8
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    for item in items:
        assert item.get("provenance")
        assert item.get("category") in CATEGORIES
        assert item.get("query")


def test_dataset_category_coverage():
    items = load_dataset(DEFAULT_DATASET)
    by_cat = {i["category"] for i in items}
    assert "search_retrieval" in by_cat
    assert "code_intelligence" in by_cat
    assert "creative_synthesis" in by_cat
    assert "safety_alignment" in by_cat


def test_resolve_workspace_seed():
    items = load_dataset(DEFAULT_DATASET)
    item = next(i for i in items if i["id"] == "dr_code_small_api_fix")
    seed = resolve_workspace_seed(item, DEFAULT_DATASET.parent)
    assert seed is not None
    assert (seed / "app.py").is_file()


def test_filter_items_by_id():
    items = load_dataset(DEFAULT_DATASET)
    one = filter_items(items, item_id="dr_search_wiki_bio")
    assert len(one) == 1
    assert one[0]["id"] == "dr_search_wiki_bio"
