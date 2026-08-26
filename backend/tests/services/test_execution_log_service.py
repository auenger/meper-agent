"""Tests for ExecutionLogService — channel classification, write, stats."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.services.execution_log_service import (
    CHANNEL_API_KEY,
    CHANNEL_IM,
    CHANNEL_INTERNAL,
    ExecutionLogService,
    classify_channel,
)

# ---------------------------------------------------------------------------
# classify_channel — pure function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_id", "expected"),
    [
        ("user_01KTNVBYQSKQQNW1BAXC436ZJ4", CHANNEL_INTERNAL),
        ("user_abc", CHANNEL_INTERNAL),
        ("user_01KTNVBYQSKQQNW1BAXC436ZJ4:1", CHANNEL_API_KEY),
        ("user_01KTNVBYQSKQQNW1BAXC436ZJ4:visitor-uuid", CHANNEL_API_KEY),
        ("channel:ch_01KXYY5SXB9882A7Q7PZFWFJT6:oc_682f108", CHANNEL_IM),
        ("", CHANNEL_INTERNAL),
        ("weird", CHANNEL_INTERNAL),
    ],
)
def test_classify_channel(user_id: str, expected: str) -> None:
    assert classify_channel(user_id) == expected


# ---------------------------------------------------------------------------
# write_log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_log_inserts_with_correct_source() -> None:
    """write_log derives source from user_id and inserts one document."""
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="xlog_1"))

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        # internal
        await ExecutionLogService.write_log(user_id="user_01", agent_id="agent_1", total_tokens=100)
        inserted_internal = mock_col.insert_one.call_args_list[0].args[0]
        assert inserted_internal["source"] == CHANNEL_INTERNAL
        assert inserted_internal["agent_id"] == "agent_1"
        assert inserted_internal["total_tokens"] == 100

        # api_key — v4 形态回归：user_id 是纯 platform user_ id（与内部用户
        # 同形态），api_key_id 显式存在时必须判为 api_key 渠道。
        await ExecutionLogService.write_log(
            user_id="user_01KTNVBYQSKQQNW1BAXC436ZJ4", api_key_id="apikey_1",
        )
        inserted_v4 = mock_col.insert_one.call_args_list[1].args[0]
        assert inserted_v4["source"] == CHANNEL_API_KEY
        assert inserted_v4["api_key_id"] == "apikey_1"

        # api_key — 旧形态（带冒号）
        await ExecutionLogService.write_log(user_id="user_01:1", api_key_id="apikey_1")
        inserted_ext = mock_col.insert_one.call_args_list[2].args[0]
        assert inserted_ext["source"] == CHANNEL_API_KEY

        # im
        await ExecutionLogService.write_log(user_id="channel:ch_1:oc_1")
        inserted_im = mock_col.insert_one.call_args_list[3].args[0]
        assert inserted_im["source"] == CHANNEL_IM
        assert inserted_im["channel_id"] == "ch_1"


# ---------------------------------------------------------------------------
# backfill_source_channel — one-time migration (marker-guarded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_fixes_misclassified_and_is_idempotent() -> None:
    """First run fixes api_key_id-bearing records + writes marker; second run no-ops."""
    logs_col = MagicMock()
    logs_col.update_many = AsyncMock(
        return_value=MagicMock(modified_count=23)
    )
    markers_col = MagicMock()
    # First call: no marker; second call: marker present.
    markers_col.find_one = AsyncMock(side_effect=[None, {"_id": "backfill_execution_log_source_v1"}])
    markers_col.update_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = (
        lambda key: logs_col if key == "execution_logs" else markers_col
    )

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        await ExecutionLogService.backfill_source_channel()
        # 修正查询只针对带 api_key_id 但 source 不是 api_key 的记录
        query = logs_col.update_many.call_args.args[0]
        assert query["api_key_id"] == {"$ne": ""}
        assert query["source"] == {"$ne": CHANNEL_API_KEY}
        markers_col.update_one.assert_awaited_once()

        logs_col.update_many.reset_mock()
        markers_col.update_one.reset_mock()
        await ExecutionLogService.backfill_source_channel()

    logs_col.update_many.assert_not_awaited()
    markers_col.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_log_failure_does_not_raise() -> None:
    """A DB error during write_log must be swallowed (logged), not raised."""
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(side_effect=Exception("DB down"))

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        result = await ExecutionLogService.write_log(user_id="user_01")
    assert result is None  # failure returns None, no exception


@pytest.mark.asyncio
async def test_write_log_derives_other_duration() -> None:
    """write_log persists duration split and derives other = latency - llm - tool."""
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="xlog_1"))

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        await ExecutionLogService.write_log(
            user_id="user_01",
            latency_ms=10_000,
            llm_duration_ms=7_000,
            tool_duration_ms=2_000,
            ttft_ms=1_200,
        )
        doc = mock_col.insert_one.call_args.args[0]
        assert doc["latency_ms"] == 10_000
        assert doc["llm_duration_ms"] == 7_000
        assert doc["tool_duration_ms"] == 2_000
        assert doc["ttft_ms"] == 1_200
        # other = 10000 - 7000 - 2000
        assert doc["other_duration_ms"] == 1_000


@pytest.mark.asyncio
async def test_write_log_clamps_negative_other_to_zero() -> None:
    """Clock skew (monotonic vs wall) may make llm+tool exceed latency — clamp ≥ 0."""
    mock_col = MagicMock()
    mock_col.insert_one = AsyncMock(return_value=MagicMock(inserted_id="xlog_1"))

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        await ExecutionLogService.write_log(
            user_id="user_01",
            latency_ms=5_000,
            llm_duration_ms=4_900,
            tool_duration_ms=200,  # 4900 + 200 > 5000
        )
        doc = mock_col.insert_one.call_args.args[0]
        assert doc["other_duration_ms"] == 0

        # 不传 duration 时默认全 0（兼容 ext 兜底等无 usage 路径）
        await ExecutionLogService.write_log(user_id="user_01", latency_ms=1_000)
        doc2 = mock_col.insert_one.call_args_list[1].args[0]
        assert doc2["llm_duration_ms"] == 0
        assert doc2["tool_duration_ms"] == 0
        assert doc2["other_duration_ms"] == 1_000
        assert doc2["ttft_ms"] == 0


# ---------------------------------------------------------------------------
# get_stats — aggregation (mocked)
# ---------------------------------------------------------------------------


class _MockAggCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for row in self._rows:
            yield row

    async def to_list(self, length=None):
        return list(self._rows)


@pytest.mark.asyncio
async def test_get_stats_groups_by_source() -> None:
    """get_stats aggregates execution_logs by source channel."""
    agg_rows = [
        {"_id": CHANNEL_INTERNAL, "calls": 10, "tokens": 5000, "input_tokens": 4000,
         "output_tokens": 1000, "llm_calls": 20, "avg_latency_ms": 1500.0,
         "success": 9, "failed": 1},
        {"_id": CHANNEL_API_KEY, "calls": 5, "tokens": 2000, "input_tokens": 1800,
         "output_tokens": 200, "llm_calls": 8, "avg_latency_ms": 800.0,
         "success": 5, "failed": 0},
    ]
    mock_col = MagicMock()
    mock_col.aggregate = MagicMock(return_value=_MockAggCursor(agg_rows))
    mock_col.count_documents = AsyncMock(return_value=15)

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        result = await ExecutionLogService.get_stats()

    channels = result["channels"]
    assert channels[CHANNEL_INTERNAL]["calls"] == 10
    assert channels[CHANNEL_INTERNAL]["tokens"] == 5000
    assert channels[CHANNEL_INTERNAL]["success"] == 9
    assert channels[CHANNEL_API_KEY]["calls"] == 5
    assert channels[CHANNEL_IM]["calls"] == 0  # no data → zeros
    # totals
    totals = result["totals"]
    assert totals["calls"] == 15  # 10 + 5
    assert totals["tokens"] == 7000
    assert totals["success_rate"] == round(14 / 15 * 100, 1)


@pytest.mark.asyncio
async def test_get_stats_aggregates_duration_split() -> None:
    """get_stats returns avg duration split (llm / tool / other / ttft) per channel."""
    agg_rows = [
        {"_id": CHANNEL_INTERNAL, "calls": 2, "tokens": 100, "input_tokens": 80,
         "output_tokens": 20, "llm_calls": 2, "avg_latency_ms": 10_000.0,
         "avg_llm_duration_ms": 7_000.0, "avg_tool_duration_ms": 2_000.0,
         "avg_other_duration_ms": 1_000.0, "avg_ttft_ms": 1_200.0,
         "success": 2, "failed": 0},
    ]
    mock_col = MagicMock()
    mock_col.aggregate = MagicMock(return_value=_MockAggCursor(agg_rows))
    mock_col.count_documents = AsyncMock(return_value=2)

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        result = await ExecutionLogService.get_stats()

    ch = result["channels"][CHANNEL_INTERNAL]
    assert ch["avg_latency_ms"] == 10_000
    assert ch["avg_llm_duration_ms"] == 7_000
    assert ch["avg_tool_duration_ms"] == 2_000
    assert ch["avg_other_duration_ms"] == 1_000
    assert ch["avg_ttft_ms"] == 1_200
    # 无数据的渠道拆分字段也为 0
    assert result["channels"][CHANNEL_IM]["avg_llm_duration_ms"] == 0

    # 聚合 pipeline 用 $ifNull 兜底旧文档（无 duration 字段的历史数据）
    pipeline = mock_col.aggregate.call_args.args[0]
    group = pipeline[1]["$group"]
    assert group["avg_llm_duration_ms"]["$avg"]["$ifNull"][0] == "$llm_duration_ms"
    assert group["avg_llm_duration_ms"]["$avg"]["$ifNull"][1] == 0


@pytest.mark.asyncio
async def test_get_stats_totals_are_weighted_average() -> None:
    """totals 的 avg_* 是按调用次数加权平均，而非跨渠道简单相加。"""
    agg_rows = [
        {"_id": CHANNEL_INTERNAL, "calls": 100, "tokens": 1000, "input_tokens": 800,
         "output_tokens": 200, "llm_calls": 100, "avg_latency_ms": 10_000.0,
         "avg_llm_duration_ms": 7_000.0, "avg_tool_duration_ms": 2_000.0,
         "avg_other_duration_ms": 1_000.0, "avg_ttft_ms": 1_200.0,
         "success": 100, "failed": 0},
        {"_id": CHANNEL_API_KEY, "calls": 1, "tokens": 10, "input_tokens": 5,
         "output_tokens": 5, "llm_calls": 1, "avg_latency_ms": 60_000.0,
         "avg_llm_duration_ms": 40_000.0, "avg_tool_duration_ms": 15_000.0,
         "avg_other_duration_ms": 5_000.0, "avg_ttft_ms": 800.0,
         "success": 1, "failed": 0},
    ]
    mock_col = MagicMock()
    mock_col.aggregate = MagicMock(return_value=_MockAggCursor(agg_rows))
    mock_col.count_documents = AsyncMock(return_value=101)

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = (
        lambda key: mock_col if key == "execution_logs" else MagicMock()
    )

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        result = await ExecutionLogService.get_stats()

    totals = result["totals"]
    total_calls = totals["calls"]
    assert total_calls == 101
    # 加权平均 Σ(calls_i×avg_i)/Σ(calls_i)；简单相加（错误实现）会是 70_000
    assert totals["avg_latency_ms"] == round(
        (100 * 10_000 + 1 * 60_000) / total_calls, 0,
    )
    assert totals["avg_llm_duration_ms"] == round(
        (100 * 7_000 + 1 * 40_000) / total_calls, 0,
    )
    assert totals["avg_tool_duration_ms"] == round(
        (100 * 2_000 + 1 * 15_000) / total_calls, 0,
    )
    assert totals["avg_ttft_ms"] == round(
        (100 * 1_200 + 1 * 800) / total_calls, 0,
    )
    assert totals["success_rate"] == 100.0


# ---------------------------------------------------------------------------
# list_logs — paginated query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_logs_paginates_and_filters() -> None:
    """list_logs applies filters and returns (items, total)."""
    mock_col = MagicMock()
    mock_col.count_documents = AsyncMock(return_value=3)
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.skip = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[{"source": "internal", "agent_id": "a1"}])
    mock_col.find = MagicMock(return_value=cursor)

    mock_db = MagicMock()
    mock_db.__getitem__.side_effect = lambda key: mock_col if key == "execution_logs" else MagicMock()

    with patch("app.services.execution_log_service.get_database", return_value=mock_db):
        items, total = await ExecutionLogService.list_logs(
            source="internal", page=1, page_size=10,
        )

    assert total == 3
    assert len(items) == 1
    assert items[0]["source"] == "internal"
