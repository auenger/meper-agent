"""忽略待答澄清卡片（dismiss）——用户不想回答 agent 追问时的关闭路径。

dismiss 与 resume 持久化答案同构：给最后一条 agent 消息里未答的
interrupt tool_call 追加一条合成 tool_result（前端按 tool_call_id 配对
合并，卡片进入已答态）。覆盖：命中填写 / 已答幂等 / 非_interrupt 不误伤 /
多个未答取最后一个 / confirm_workflow 同样可忽略 / service 封装。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.errors import NotFoundError
from app.schemas.execution import DismissRequest
from app.services.agent_execution_service import AgentExecutionService
from app.services.session_service import MessageService


def _agent_msg(entries: list[dict]) -> dict:
    return {
        "_id": "msg_AGENT1",
        "session_id": "session_TEST",
        "role": "agent",
        "timeline_entries": entries,
        "created_at": "2026-01-01T00:00:00",
    }


def _tool_call(name: str, call_id: str) -> dict:
    return {"type": "tool_call", "tool_name": name, "id": call_id, "args": {}}


def _tool_result(name: str, call_id: str, content: str = "ok") -> dict:
    return {
        "type": "tool_result",
        "tool_name": name,
        "content": content,
        "status": "success",
        "tool_call_id": call_id,
    }


@pytest.fixture
def messages_col():
    col = MagicMock()
    col.find_one = AsyncMock(return_value=_agent_msg([
        {"type": "text", "content": "需要补充信息"},
        _tool_call("ask_clarification", "call_1"),
    ]))
    col.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    db = MagicMock()
    db.__getitem__.return_value = col
    with patch("app.services.session_service.get_database", return_value=db):
        yield col


# ── MessageService.dismiss_pending_clarification ─────────────────────────────


class TestDismissPendingClarification:
    async def test_pending_card_gets_synthetic_tool_result(self, messages_col):
        dismissed = await MessageService.dismiss_pending_clarification("session_TEST")

        assert dismissed is True
        messages_col.update_one.assert_awaited_once()
        _, update_doc = messages_col.update_one.await_args.args
        pushed = update_doc["$push"]["timeline_entries"]
        assert pushed["type"] == "tool_result"
        assert pushed["tool_name"] == "ask_clarification"
        assert pushed["tool_call_id"] == "call_1"
        assert pushed["content"] == MessageService.DISMISSED_RESULT_TEXT
        assert pushed["status"] == "success"

    async def test_already_answered_card_is_idempotent(self, messages_col):
        messages_col.find_one.return_value = _agent_msg([
            _tool_call("ask_clarification", "call_1"),
            _tool_result("ask_clarification", "call_1", "管理层"),
        ])

        dismissed = await MessageService.dismiss_pending_clarification("session_TEST")

        assert dismissed is False
        messages_col.update_one.assert_not_awaited()

    async def test_no_agent_message_is_idempotent(self, messages_col):
        messages_col.find_one.return_value = None

        assert await MessageService.dismiss_pending_clarification("session_TEST") is False
        messages_col.update_one.assert_not_awaited()

    async def test_non_interrupt_tool_call_untouched(self, messages_col):
        # 未完成的普通工具调用（如中断在 kb_search 上）不是澄清卡片，不忽略。
        messages_col.find_one.return_value = _agent_msg([
            _tool_call("kb_search", "call_9"),
        ])

        assert await MessageService.dismiss_pending_clarification("session_TEST") is False
        messages_col.update_one.assert_not_awaited()

    async def test_multiple_pending_picks_the_last(self, messages_col):
        messages_col.find_one.return_value = _agent_msg([
            _tool_call("ask_clarification", "call_1"),
            _tool_result("ask_clarification", "call_1", "第一轮回答"),
            _tool_call("ask_clarification", "call_2"),
        ])

        dismissed = await MessageService.dismiss_pending_clarification("session_TEST")

        assert dismissed is True
        _, update_doc = messages_col.update_one.await_args.args
        assert update_doc["$push"]["timeline_entries"]["tool_call_id"] == "call_2"

    async def test_confirm_workflow_card_dismissable(self, messages_col):
        messages_col.find_one.return_value = _agent_msg([
            _tool_call("confirm_workflow", "call_3"),
        ])

        dismissed = await MessageService.dismiss_pending_clarification("session_TEST")

        assert dismissed is True
        _, update_doc = messages_col.update_one.await_args.args
        pushed = update_doc["$push"]["timeline_entries"]
        assert pushed["tool_name"] == "confirm_workflow"
        assert pushed["tool_call_id"] == "call_3"

    async def test_legacy_records_without_ids_pair_by_name(self, messages_col):
        # 旧数据双方都无 call id → 回退 tool_name 配对（与前端合并逻辑一致）。
        messages_col.find_one.return_value = _agent_msg([
            _tool_call("ask_clarification", ""),
            {"type": "tool_result", "tool_name": "ask_clarification", "content": "旧回答"},
        ])

        assert await MessageService.dismiss_pending_clarification("session_TEST") is False
        messages_col.update_one.assert_not_awaited()


# ── AgentExecutionService.dismiss_interrupt ──────────────────────────────────


class TestDismissInterruptService:
    async def test_agent_not_found_raises(self):
        with (
            patch(
                "app.services.agent_execution_service.AgentService.get_agent",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(NotFoundError),
        ):
            await AgentExecutionService.dismiss_interrupt(
                "agent_X", DismissRequest(session_id="session_TEST"), "user_1",
            )

    async def test_delegates_to_message_service(self):
        with (
            patch(
                "app.services.agent_execution_service.AgentService.get_agent",
                new_callable=AsyncMock,
                return_value={"_id": "agent_1", "name": "a"},
            ),
            patch.object(
                MessageService, "dismiss_pending_clarification",
                new_callable=AsyncMock, return_value=True,
            ) as mock_dismiss,
        ):
            dismissed = await AgentExecutionService.dismiss_interrupt(
                "agent_1", DismissRequest(session_id="session_TEST"), "user_1",
            )

        assert dismissed is True
        mock_dismiss.assert_awaited_once_with("session_TEST")
