from types import SimpleNamespace

import pytest

from noesis_server.services.citation_service import CitationService, find_citation_annotation


def test_find_citation_annotation_reads_only_typed_text_annotation() -> None:
    annotation = {
        "type": "kb_citation",
        "citation_id": "cit_1",
        "collection_name": "requirements",
        "document_id": "doc_1",
        "document_version_id": "docv_1",
        "segment_id": "seg_1",
    }
    content = {"parts": [
        {"type": "retrieval", "citation_id": "cit_1", "results": []},
        {"type": "text", "content": "答案", "annotations": [annotation]},
    ]}
    assert find_citation_annotation(content, "cit_1") == annotation


def test_find_citation_annotation_rejects_forged_or_wrong_type() -> None:
    content = {"parts": [{
        "type": "text",
        "content": "答案",
        "annotations": [{"type": "file_citation", "citation_id": "cit_1"}],
    }]}
    assert find_citation_annotation(content, "cit_1") is None
    assert find_citation_annotation(content, "cit_forged") is None


class _DbResult:
    def __init__(self, pair):
        self._pair = pair

    def first(self):
        return self._pair


class _Db:
    def __init__(self, pair):
        self._pair = pair

    async def execute(self, _query):
        return _DbResult(self._pair)


def _message_and_session(*, collections=None):
    annotation = {
        "type": "kb_citation",
        "citation_id": "cit_1",
        "collection_name": "requirements",
        "document_id": "doc_1",
        "document_version_id": "docv_1",
        "segment_id": "seg_1",
        "title": "登录.md",
        "excerpt": "旧快照",
        "verification": "structural",
    }
    message = SimpleNamespace(content={"parts": [{
        "type": "text", "content": "五分钟", "annotations": [annotation],
    }]})
    session = SimpleNamespace(extra={"kb_collections": collections or ["requirements"]})
    return message, session


def _web_message_and_session(url="https://example.com/docs"):
    annotation = {
        "type": "url_citation",
        "citation_id": "cit_web",
        "url": url,
        "title": "Example Docs",
        "excerpt": "生成时网页快照",
        "verification": "structural",
    }
    message = SimpleNamespace(content={"parts": [{
        "type": "text", "content": "网页答案", "annotations": [annotation],
    }]})
    return message, SimpleNamespace(extra={})


@pytest.mark.asyncio
async def test_resolve_web_citation_returns_snapshot_without_refetch(monkeypatch) -> None:
    called = False

    def _resolve(**_kwargs):
        nonlocal called
        called = True
        return "resolved", {}

    monkeypatch.setattr(
        "noesis_server.services.citation_service.KbRetrievalService.resolve_evidence",
        _resolve,
    )
    result = await CitationService.resolve(
        message_id="m1", citation_id="cit_web", user_id="u1", db=_Db(_web_message_and_session())
    )
    assert result.status == "resolved"
    assert result.data["url"] == "https://example.com/docs"
    assert result.data["excerpt"] == "生成时网页快照"
    assert called is False


@pytest.mark.asyncio
async def test_resolve_web_citation_rejects_url_credentials() -> None:
    result = await CitationService.resolve(
        message_id="m1",
        citation_id="cit_web",
        user_id="u1",
        db=_Db(_web_message_and_session("https://user:secret@example.com/docs")),
    )
    assert result.status == "missing"


@pytest.mark.asyncio
async def test_resolve_rechecks_live_segment_and_keeps_snapshot_distinct(monkeypatch) -> None:
    monkeypatch.setattr(
        "noesis_server.services.citation_service.KbRetrievalService.resolve_evidence",
        lambda **_kwargs: (
            "resolved",
            {"content": "当前版本正文", "title": "登录.md", "locator": {"type": "page"}},
        ),
    )
    result = await CitationService.resolve(
        message_id="m1", citation_id="cit_1", user_id="u1", db=_Db(_message_and_session())
    )
    assert result.status == "resolved"
    assert result.data["excerpt"] == "当前版本正文"
    assert result.data["snapshot_excerpt"] == "旧快照"


@pytest.mark.asyncio
async def test_resolve_denies_revoked_collection_before_live_read(monkeypatch) -> None:
    called = False

    def _resolve(**_kwargs):
        nonlocal called
        called = True
        return "resolved", {}

    monkeypatch.setattr(
        "noesis_server.services.citation_service.KbRetrievalService.resolve_evidence",
        _resolve,
    )
    result = await CitationService.resolve(
        message_id="m1",
        citation_id="cit_1",
        user_id="u1",
        db=_Db(_message_and_session(collections=["other"])),
    )
    assert result.status == "forbidden"
    assert called is False


@pytest.mark.asyncio
async def test_resolve_forged_or_foreign_message_is_missing() -> None:
    foreign = await CitationService.resolve(
        message_id="m1", citation_id="cit_1", user_id="u1", db=_Db(None)
    )
    forged = await CitationService.resolve(
        message_id="m1", citation_id="cit_forged", user_id="u1", db=_Db(_message_and_session())
    )
    assert foreign.status == "missing"
    assert forged.status == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("live_status", ["stale", "missing"])
async def test_resolve_propagates_deleted_version_status(monkeypatch, live_status) -> None:
    monkeypatch.setattr(
        "noesis_server.services.citation_service.KbRetrievalService.resolve_evidence",
        lambda **_kwargs: (live_status, None),
    )
    result = await CitationService.resolve(
        message_id="m1", citation_id="cit_1", user_id="u1", db=_Db(_message_and_session())
    )
    assert result.status == live_status
