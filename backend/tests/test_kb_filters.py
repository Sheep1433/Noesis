"""kb.filters 单元测试"""
from langchain_core.documents import Document

from kb.retrieval import document_matches_post_filter, split_search_filters


def test_split_search_filters_empty():
    q, p = split_search_filters(None)
    assert q is None
    assert p == {}


def test_split_header_path_prefix():
    q, p = split_search_filters(
        {"file_name": "a.md", "header_path_prefix": "a.md > Ch1"}
    )
    assert q == {"file_name": "a.md"}
    assert p == {"header_path_prefix": "a.md > Ch1"}


def test_document_matches_post_filter_prefix():
    doc = Document(
        page_content="x",
        metadata={"header_path": "a.md > Ch1 > Sec"},
    )
    assert document_matches_post_filter(
        doc.metadata, {"header_path_prefix": "a.md > Ch1"}
    )
    assert not document_matches_post_filter(
        doc.metadata, {"header_path_prefix": "other"}
    )


def test_document_matches_post_filter_file_name_in():
    doc = Document(page_content="x", metadata={"file_name": "a.md"})
    assert document_matches_post_filter(doc.metadata, {"file_name_in": ["a.md", "b.md"]})
    assert not document_matches_post_filter(doc.metadata, {"file_name_in": ["b.md"]})


def test_document_matches_post_filter_exclude_file_names():
    doc = Document(page_content="x", metadata={"file_name": "a.md"})
    assert document_matches_post_filter(doc.metadata, {"exclude_file_names": ["b.md"]})
    assert not document_matches_post_filter(doc.metadata, {"exclude_file_names": ["a.md"]})
