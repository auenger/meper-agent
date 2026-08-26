"""快捷指令展示文案（display_text）——用户消息「实际发送 vs 对外展示」分离。

content 持续记录实际发送给 AI 的内容（审计/legacy 迁移依赖），
display_text 仅承担对外展示（气泡/历史/会话标题），避免后台指令泄漏。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.schemas.execution import ExecutionRequest
from app.schemas.ext_api import ExtInvokeRequest
from app.services.agent_execution_service import _resolve_session
from app.services.session_service import MessageService


def _session_doc(title: str = "", message_count: int = 0) -> dict:
    return {
        "_id": "session_TEST",
        "user_id": "user_01HTEST",
        "agent_id": "agent_TEST",
        "title": title,
        "status": "active",
        "message_count": message_count,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }


@pytest.fixture
def mock_db():
    db = MagicMock()
    db["messages"].insert_one = AsyncMock(return_value=MagicMock())
    with patch("app.services.session_service.get_database", return_value=db):
        yield db


# ── schemas ──────────────────────────────────────────────────────────────────


class TestRequestSchemas:
    """display_text 在两个请求 schema 上均为可选。"""

    def test_execution_request_display_text_defaults_to_none(self):
        req = ExecutionRequest(input="hi")
        assert req.display_text is None

    def test_execution_request_accepts_display_text(self):
        req = ExecutionRequest(input="询问天气应该去使用什么api", display_text="查看今天天气")
        assert req.display_text == "查看今天天气"
        assert req.input == "询问天气应该去使用什么api"

    def test_ext_invoke_request_display_text_defaults_to_none(self):
        req = ExtInvokeRequest(message="hi")
        assert req.display_text is None

    def test_display_text_over_500_chars_rejected(self):
        with pytest.raises(ValueError):
            ExecutionRequest(input="hi", display_text="x" * 501)


# ── MessageService.add_message ───────────────────────────────────────────────


class TestAddMessageDisplayText:
    """add_message：display_text 落库与标题优先级。"""

    @pytest.mark.asyncio
    async def test_user_message_persists_display_text(self, mock_db):
        with (
            patch(
                "app.services.session_service.SessionService.get_session",
                new_callable=AsyncMock,
                return_value=_session_doc(),
            ),
            patch(
                "app.services.session_service.SessionService.update_session",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            doc = await MessageService.add_message(
                session_id="session_TEST",
                role="user",
                content="询问天气应该去使用什么api",
                display_text="查看今天天气",
            )

        # content 记录实际发送内容，display_text 只承担展示
        assert doc["content"] == "询问天气应该去使用什么api"
        assert doc["display_text"] == "查看今天天气"
        # 首条消息标题优先展示文案，避免指令泄漏到会话侧边栏
        update_fields = mock_update.call_args.args[1]
        assert update_fields["title"] == "查看今天天气"

    @pytest.mark.asyncio
    async def test_without_display_text_keeps_legacy_shape(self, mock_db):
        with (
            patch(
                "app.services.session_service.SessionService.get_session",
                new_callable=AsyncMock,
                return_value=_session_doc(),
            ),
            patch(
                "app.services.session_service.SessionService.update_session",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            doc = await MessageService.add_message(
                session_id="session_TEST",
                role="user",
                content="普通用户输入",
            )

        assert doc["content"] == "普通用户输入"
        assert "display_text" not in doc
        update_fields = mock_update.call_args.args[1]
        assert update_fields["title"] == "普通用户输入"

    @pytest.mark.asyncio
    async def test_long_display_text_truncated_for_title(self, mock_db):
        long_label = "查看" * 20  # 40 chars > 30
        with (
            patch(
                "app.services.session_service.SessionService.get_session",
                new_callable=AsyncMock,
                return_value=_session_doc(),
            ),
            patch(
                "app.services.session_service.SessionService.update_session",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            doc = await MessageService.add_message(
                session_id="session_TEST",
                role="user",
                content="很长的实际指令" * 10,
                display_text=long_label,
            )

        assert doc["display_text"] == long_label  # 全量落库不截断
        update_fields = mock_update.call_args.args[1]
        assert update_fields["title"] == long_label[:30] + "…"

    @pytest.mark.asyncio
    async def test_agent_message_never_persists_display_text(self, mock_db):
        with (
            patch(
                "app.services.session_service.SessionService.get_session",
                new_callable=AsyncMock,
                return_value=_session_doc(message_count=1),
            ),
            patch(
                "app.services.session_service.SessionService.update_session",
                new_callable=AsyncMock,
            ),
        ):
            doc = await MessageService.add_message(
                session_id="session_TEST",
                role="agent",
                timeline_entries=[{"type": "text", "content": "回复"}],
                display_text="不应落库",
            )

        assert "display_text" not in doc

    @pytest.mark.asyncio
    async def test_title_not_overwritten_once_set(self, mock_db):
        with (
            patch(
                "app.services.session_service.SessionService.get_session",
                new_callable=AsyncMock,
                return_value=_session_doc(title="已有标题"),
            ),
            patch(
                "app.services.session_service.SessionService.update_session",
                new_callable=AsyncMock,
            ) as mock_update,
        ):
            await MessageService.add_message(
                session_id="session_TEST",
                role="user",
                content="第二条消息",
                display_text="第二条展示",
            )

        update_fields = mock_update.call_args.args[1]
        assert "title" not in update_fields


# ── _resolve_session ─────────────────────────────────────────────────────────


class TestResolveSessionDisplayText:
    """_resolve_session：display_text 透传到消息与新会话标题。"""

    @pytest.mark.asyncio
    async def test_display_text_preferred_for_title_and_message(self):
        body = ExecutionRequest(input="询问天气应该去使用什么api", display_text="查看今天天气")
        with (
            patch(
                "app.services.agent_execution_service.SessionService.create_session",
                new_callable=AsyncMock,
                return_value={"_id": "session_TEST"},
            ) as mock_create,
            patch(
                "app.services.agent_execution_service.MessageService.add_message",
                new_callable=AsyncMock,
            ) as mock_add,
        ):
            session_id = await _resolve_session("agent_TEST", body, "user_01HTEST")

        assert session_id == "session_TEST"
        assert mock_create.call_args.kwargs["title"] == "查看今天天气"
        # AI 收到的仍是 input；展示文案只落 display_text
        assert mock_add.call_args.kwargs["content"] == "询问天气应该去使用什么api"
        assert mock_add.call_args.kwargs["display_text"] == "查看今天天气"

    @pytest.mark.asyncio
    async def test_without_display_text_falls_back_to_input(self):
        body = ExecutionRequest(input="普通输入", session_id="session_EXIST")
        with (
            patch(
                "app.services.agent_execution_service.SessionService.create_session",
                new_callable=AsyncMock,
            ) as mock_create,
            patch(
                "app.services.agent_execution_service.MessageService.add_message",
                new_callable=AsyncMock,
            ) as mock_add,
        ):
            session_id = await _resolve_session("agent_TEST", body, "user_01HTEST")

        assert session_id == "session_EXIST"
        mock_create.assert_not_called()
        assert mock_add.call_args.kwargs["display_text"] == ""
