"""压测查询集加载单测。"""

from evals.loadtest.queries import DATASET, load_dataset_queries, pick_query


def test_load_dataset_queries() -> None:
    queries = load_dataset_queries()
    assert len(queries) == 5
    assert all(isinstance(q, str) and q for q in queries)


def test_pick_query_from_dataset() -> None:
    pool = load_dataset_queries()
    assert pick_query(pool) in pool


def test_dataset_file_exists() -> None:
    assert DATASET.is_file()
