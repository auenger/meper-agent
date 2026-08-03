"""Tests for AgentService — CRUD operations and lifecycle management."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.errors import ConflictError
from app.services.agent_service import AgentService


@pytest.fixture(autouse=True)
def mock_database():
    """Mock the MongoDB database."""
    mock_db = MagicMock()
    with patch("app.services.agent_service.get_database", return_value=mock_db):
        yield mock_db


def _fake_doc(agent_id: str = "agent_01HTEST", status: str = "draft") -> dict:
    return {
        "_id": agent_id,
        "name": "Test Agent",
        "description": "A test agent",
        "prompt_slots": {},
        "skill_ids": [],
        "mcp_connection_ids": [],
        "builtin_config": [],
        "workflow_ids": [],
        "knowledge_base_ids": [],
        "default_model": "gpt-4",
        "max_retry": 3,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


# ── update_agent ──────────────────────────────────────────────────────────────


class TestUpdateAgent:
    """update_agent: basic update and guard rails."""

    @pytest.mark.asyncio
    async def test_update_draft_agent(self, mock_database):
        """Draft agent can be updated normally."""
        updated_doc = _fake_doc(status="draft")
        updated_doc["name"] = "Updated Name"
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(status="draft"),  # existing agent
                None,  # no name conflict
                updated_doc,  # after update
            ]
        )
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        doc = await AgentService.update_agent(
            agent_id="agent_01HTEST",
            name="Updated Name",
        )

        assert doc is not None
        assert doc["name"] == "Updated Name"
        # No $inc — no version bump
        args, _kwargs = mock_col.update_one.call_args
        assert "$inc" not in args[1]
        assert "$set" in args[1]

    @pytest.mark.asyncio
    async def test_update_published_agent_raises_conflict(self, mock_database):
        """Published agents are immutable."""
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(return_value=_fake_doc(status="published"))
        mock_database.__getitem__.return_value = mock_col

        with pytest.raises(ConflictError) as exc_info:
            await AgentService.update_agent(
                agent_id="agent_01HTEST",
                name="Should Fail",
            )
        assert exc_info.value.code == "AGENT_PUBLISHED_IMMUTABLE"

    @pytest.mark.asyncio
    async def test_update_archived_agent(self, mock_database):
        """Archived agent can be updated."""
        updated_doc = _fake_doc(status="archived")
        updated_doc["name"] = "Revived from archive"
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(status="archived"),  # existing agent
                None,  # no name conflict
                updated_doc,  # after update
            ]
        )
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        doc = await AgentService.update_agent(
            agent_id="agent_01HTEST",
            name="Revived from archive",
        )

        assert doc is not None
        assert doc["name"] == "Revived from archive"


class TestUpdateAgentNameConflict:
    """Name conflict should raise before any update."""

    @pytest.mark.asyncio
    async def test_name_conflict_raises(self, mock_database):
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(),
                {"_id": "agent_other", "name": "Test Agent"},
            ]
        )
        mock_database.__getitem__.return_value = mock_col

        with pytest.raises(ConflictError) as exc_info:
            await AgentService.update_agent(
                agent_id="agent_01HTEST",
                name="Test Agent",
            )
        assert exc_info.value.code == "AGENT_NAME_CONFLICT"

    @pytest.mark.asyncio
    async def test_update_custom_tools_encrypts_sensitive(self, mock_database):
        """custom_tools 的敏感 user_args 字段在保存时被加密。"""
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(side_effect=[_fake_doc(), None, _fake_doc()])
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        fake_tool_doc = {
            "_id": "tool_001",
            "name": "my-api",
            "user_args_schema": {
                "properties": {
                    "token": {"type": "string", "sensitive": True},
                    "base_url": {"type": "string"},
                }
            },
        }
        with patch(
            "app.services.tool_service.ToolService.get_tools_by_ids",
            new_callable=AsyncMock,
            return_value=[fake_tool_doc],
        ):
            await AgentService.update_agent(
                agent_id="agent_01HTEST",
                name="Test Agent",
                custom_tools=[{"tool_id": "tool_001", "user_args": {"token": "sk-secret", "base_url": "https://x"}}],
            )

        args, _kwargs = mock_col.update_one.call_args
        saved = args[1]["$set"]["custom_tools"]
        assert len(saved) == 1
        assert saved[0]["user_args"]["token"].startswith("enc:")
        assert saved[0]["user_args"]["base_url"] == "https://x"

    @pytest.mark.asyncio
    async def test_update_custom_tools_preserves_encrypted(self, mock_database):
        """已加密(enc: 前缀)的值不重复加密。"""
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(side_effect=[_fake_doc(), None, _fake_doc()])
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        fake_tool_doc = {
            "_id": "tool_001",
            "user_args_schema": {"properties": {"token": {"type": "string", "sensitive": True}}},
        }
        with patch(
            "app.services.tool_service.ToolService.get_tools_by_ids",
            new_callable=AsyncMock,
            return_value=[fake_tool_doc],
        ):
            await AgentService.update_agent(
                agent_id="agent_01HTEST",
                name="Test Agent",
                custom_tools=[{"tool_id": "tool_001", "user_args": {"token": "enc:already-encrypted"}}],
            )

        args, _kwargs = mock_col.update_one.call_args
        saved = args[1]["$set"]["custom_tools"]
        assert saved[0]["user_args"]["token"] == "enc:already-encrypted"

    @pytest.mark.asyncio
    async def test_update_custom_tool_ids_legacy_fallback(self, mock_database):
        """旧客户端只传 custom_tool_ids 时,转为 user_args={} 的绑定。"""
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(side_effect=[_fake_doc(), None, _fake_doc()])
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        with patch(
            "app.services.tool_service.ToolService.get_tools_by_ids",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await AgentService.update_agent(
                agent_id="agent_01HTEST",
                name="Test Agent",
                custom_tool_ids=["tool_legacy_1", "tool_legacy_2"],
            )

        args, _kwargs = mock_col.update_one.call_args
        saved = args[1]["$set"]["custom_tools"]
        assert [b["tool_id"] for b in saved] == ["tool_legacy_1", "tool_legacy_2"]
        assert all(b["user_args"] == {} for b in saved)


# ── publish_agent ─────────────────────────────────────────────────────────────


class TestPublishAgent:
    """Publishing transitions status to published."""

    @pytest.mark.asyncio
    async def test_publish_draft_agent(self, mock_database):
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(status="draft"),  # existing agent
                _fake_doc(status="published"),  # updated agent
            ]
        )
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        doc = await AgentService.publish_agent("agent_01HTEST")

        assert doc is not None
        assert doc["status"] == "published"
        args, _kwargs = mock_col.update_one.call_args
        assert args[1]["$set"]["status"] == "published"


# ── archive_agent ─────────────────────────────────────────────────────────────


class TestArchiveAgent:
    """Archiving transitions status to archived."""

    @pytest.mark.asyncio
    async def test_archive_published_agent(self, mock_database):
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(status="published"),
                _fake_doc(status="archived"),
            ]
        )
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        doc = await AgentService.archive_agent("agent_01HTEST")

        assert doc is not None
        assert doc["status"] == "archived"
        args, _kwargs = mock_col.update_one.call_args
        assert args[1]["$set"]["status"] == "archived"

    @pytest.mark.asyncio
    async def test_archive_draft_agent(self, mock_database):
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            side_effect=[
                _fake_doc(status="draft"),
                _fake_doc(status="archived"),
            ]
        )
        mock_col.update_one = AsyncMock()
        mock_database.__getitem__.return_value = mock_col

        doc = await AgentService.archive_agent("agent_01HTEST")

        assert doc is not None
        assert doc["status"] == "archived"


# ── delete_agent ──────────────────────────────────────────────────────────────


class TestDeleteAgent:
    """delete_agent removes the agent document."""

    @pytest.mark.asyncio
    async def test_delete_agent(self, mock_database):
        mock_col = MagicMock()
        mock_col.find_one = AsyncMock(
            return_value={"_id": "agent_01HTEST", "name": "Test Agent"}
        )
        # delete_agent 做引用检查 (find().to_list()) + 级联清理 sessions (async for)，
        # 两者都走 find()。mock 为空结果，确保不触碰真实 DB。
        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_cursor.__aiter__ = MagicMock(return_value=iter([]))
        mock_col.find = MagicMock(return_value=mock_cursor)
        mock_result = MagicMock()
        mock_result.deleted_count = 1
        mock_col.delete_one = AsyncMock(return_value=mock_result)
        mock_database.__getitem__.return_value = mock_col

        result = await AgentService.delete_agent("agent_01HTEST")

        assert result is True
