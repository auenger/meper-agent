"""Tests for vector KB stats — file_count/total_size maintenance.

Covers the bug where vector KBs always showed "0 文件 / 0 B" because
``recompute_stats`` only scans disk ``.md`` files (tree KBs), while vector
KB document metadata lives in the ``knowledge_documents`` collection.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.services.kb_service import KnowledgeBaseService
from app.services.knowledge_document_service import KnowledgeDocumentService


def _mock_collection(aggregate_result: list[dict]):
    """Build a mocked Mongo collection with an aggregate() AsyncMock."""
    mock_cursor = MagicMock()
    mock_cursor.to_list = AsyncMock(return_value=aggregate_result)
    mock_col = MagicMock()
    mock_col.aggregate = MagicMock(return_value=mock_cursor)
    return mock_col


# ---------------------------------------------------------------------------
# KnowledgeDocumentService.compute_kb_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_kb_stats_aggregates_count_and_size() -> None:
    """Aggregate pipeline returns summed count + size."""
    mock_col = _mock_collection([{"_id": None, "count": 3, "size": 12345}])
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    with patch(
        "app.services.knowledge_document_service.get_database", return_value=mock_db
    ):
        count, size = await KnowledgeDocumentService.compute_kb_stats("kb_123")

    assert count == 3
    assert size == 12345
    # Verify the pipeline matched the right kb_id
    mock_col.aggregate.assert_called_once()
    pipeline = mock_col.aggregate.call_args.args[0]
    assert pipeline[0]["$match"] == {"knowledge_base_id": "kb_123"}


@pytest.mark.asyncio
async def test_compute_kb_stats_empty_kb() -> None:
    """No documents → empty aggregate result → (0, 0)."""
    mock_col = _mock_collection([])  # no $group output
    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    with patch(
        "app.services.knowledge_document_service.get_database", return_value=mock_db
    ):
        count, size = await KnowledgeDocumentService.compute_kb_stats("kb_empty")

    assert (count, size) == (0, 0)


# ---------------------------------------------------------------------------
# KnowledgeBaseService.recompute_vector_stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_vector_stats_writes_aggregated_values() -> None:
    """recompute_vector_stats reads from doc service, writes to kb collection."""
    mock_kb_col = MagicMock()
    mock_kb_col.update_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_kb_col)

    with (
        patch("app.services.kb_service.get_database", return_value=mock_db),
        patch(
            "app.services.knowledge_document_service.KnowledgeDocumentService.compute_kb_stats",
            new=AsyncMock(return_value=(2, 5000)),
        ),
    ):
        await KnowledgeBaseService.recompute_vector_stats("kb_vec_1")

    mock_kb_col.update_one.assert_called_once()
    call_args = mock_kb_col.update_one.call_args
    assert call_args.args[0] == {"_id": "kb_vec_1"}
    set_doc = call_args.args[1]["$set"]
    assert set_doc["file_count"] == 2
    assert set_doc["total_size"] == 5000
    assert "updated_at" in set_doc
