"""Session and Message business logic — CRUD operations."""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from app.db.mongodb import get_database
from app.models.session import Message, Session


class SessionService:
    """Service layer for Session operations."""

    COLLECTION = "sessions"

    @staticmethod
    def _collection():
        return get_database()[SessionService.COLLECTION]

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    @staticmethod
    async def create_session(
        user_id: str,
        agent_id: str,
        title: str = "",
    ) -> dict:
        """Create a new session for a user-agent pair.

        Args:
            user_id: Owner user ID.
            agent_id: Associated agent ID.
            title: Optional title (usually first message preview).

        Returns:
            Created session MongoDB document.
        """
        session = Session(
            user_id=user_id,
            agent_id=agent_id,
            title=title[:200] if title else "",
        )
        doc = {
            "_id": session.id,
            "user_id": session.user_id,
            "agent_id": session.agent_id,
            "title": session.title,
            "status": session.status.value,
            "message_count": session.message_count,
            "total_tokens": session.total_tokens,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

        await SessionService._collection().insert_one(doc)

        # Create workspace directory tree for this session (AC: file isolation)
        try:
            from app.engine.tool.workspace import WorkspaceManager

            WorkspaceManager.create_workspace(user_id, session.id)
        except Exception as exc:
            logger.warning(
                "workspace_creation_failed",
                session_id=session.id,
                error=str(exc),
            )

        logger.info("session_created", session_id=session.id, user_id=user_id, agent_id=agent_id)
        return doc

    @staticmethod
    async def get_session(session_id: str) -> dict | None:
        """Get a session by ID."""
        return await SessionService._collection().find_one({"_id": session_id})

    @staticmethod
    async def list_sessions(
        user_id: str,
        agent_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict], int]:
        """List sessions for a user, optionally filtered by agent_id.

        Returns:
            Tuple of (items, total_count).
        """
        filter_query: dict = {"user_id": user_id}
        if agent_id:
            filter_query["agent_id"] = agent_id

        col = SessionService._collection()
        total = await col.count_documents(filter_query)
        cursor = col.find(filter_query).sort("updated_at", -1).skip((page - 1) * page_size).limit(page_size)
        items = await cursor.to_list(length=page_size)
        return items, total

    @staticmethod
    async def delete_session(session_id: str) -> bool:
        """Delete a session and all its associated messages.

        Returns:
            True if session was deleted, False if not found.
        """
        # Fetch session doc before deletion (needed for workspace cleanup)
        session_doc = await SessionService._collection().find_one({"_id": session_id})

        result = await SessionService._collection().delete_one({"_id": session_id})
        if result.deleted_count > 0:
            # Delete all messages in this session
            await MessageService._collection().delete_many({"session_id": session_id})

            # Clean up LangGraph checkpointer thread (thread_id == session_id).
            # The full agent execution history (state snapshots, tool_calls,
            # intermediate steps) lives in the checkpoints/checkpoint_writes
            # collections keyed by thread_id; without this it would leak as
            # orphan data and remain readable via get_thread_messages.
            # pymongo 是同步驱动,直接调用会阻塞 event loop,用 to_thread 卸到线程池。
            try:
                from app.engine.harness_integration import get_checkpointer

                checkpointer = get_checkpointer()
                cp_col = getattr(checkpointer, "checkpoint_collection", None)
                writes_col = getattr(checkpointer, "writes_collection", None)
                thread_filter = {"thread_id": session_id}  # exact match, not prefix

                def _delete() -> None:
                    if cp_col is not None:
                        cp_col.delete_many(thread_filter)
                    if writes_col is not None:
                        writes_col.delete_many(thread_filter)

                await asyncio.to_thread(_delete)
            except Exception as exc:
                logger.warning(
                    "session_checkpointer_cleanup_failed",
                    session_id=session_id,
                    error=str(exc),
                )

            # Clean up workspace files
            try:
                from app.engine.tool.workspace import WorkspaceManager

                if session_doc:
                    WorkspaceManager.delete_workspace(
                        session_doc["user_id"], session_id
                    )
            except Exception as exc:
                logger.warning(
                    "workspace_cleanup_failed",
                    session_id=session_id,
                    error=str(exc),
                )

            logger.info("session_deleted", session_id=session_id)
            return True
        return False

    @staticmethod
    async def update_session(session_id: str, update_fields: dict) -> dict | None:
        """Update specific fields on a session.

        Returns:
            Updated document or None if not found.
        """
        from app.models.base import utc_now

        update_fields["updated_at"] = utc_now().isoformat()
        await SessionService._collection().update_one(
            {"_id": session_id},
            {"$set": update_fields},
        )
        return await SessionService.get_session(session_id)

    @staticmethod
    async def add_tokens(session_id: str, tokens: int) -> None:
        """Atomically increment the session's cumulative token usage."""
        from app.models.base import utc_now

        await SessionService._collection().update_one(
            {"_id": session_id},
            {"$inc": {"total_tokens": tokens}, "$set": {"updated_at": utc_now().isoformat()}},
        )


class MessageService:
    """Service layer for Message operations."""

    COLLECTION = "messages"

    @staticmethod
    def _collection():
        return get_database()[MessageService.COLLECTION]

    @staticmethod
    async def add_message(
        session_id: str,
        role: str,
        content: str = "",
        timeline_entries: list[dict] | None = None,
        file_ids: list[str] | None = None,
        token_usage: dict | None = None,
        request_id: str = "",
        display_text: str = "",
    ) -> dict:
        """Add a message to a session.

        Args:
            session_id: Parent session ID.
            role: 'user' or 'agent'.
            content: Message text (user messages). Agent messages omit this
                and store text in ``timeline_entries`` instead.
            timeline_entries: Structured timeline events (for agent messages).
            file_ids: Associated FileRef IDs for uploaded attachments.
            request_id: 本轮执行请求 id——消息级反馈（§8.2）按它关联本轮
                load 的技能（与 skill_logs.request_id 同键）。
            display_text: 展示文案（快捷指令 label）。content 记录实际发送
                给 AI 的内容；气泡/历史/标题展示优先本字段。

        Returns:
            Created message document.
        """
        msg = Message(
            session_id=session_id,
            role=role,
            content=content,
            display_text=display_text,
            timeline_entries=timeline_entries or [],
            file_ids=file_ids or [],
        )
        # Agent messages do not store a top-level ``content`` field — their
        # text lives inside ``timeline_entries`` (type="text" entries). Omit
        # the key entirely so agent docs have no content field at all.
        doc: dict[str, Any] = {
            "_id": msg.id,
            "session_id": msg.session_id,
            "role": msg.role,
            "timeline_entries": msg.timeline_entries,
            "file_ids": msg.file_ids,
            "created_at": msg.created_at,
        }
        if request_id:
            doc["request_id"] = request_id
        if token_usage:
            doc["token_usage"] = token_usage
        if role == "user":
            doc["content"] = msg.content
            if msg.display_text:
                doc["display_text"] = msg.display_text

        await MessageService._collection().insert_one(doc)

        # Update session metadata
        update_fields: dict = {
            "message_count": (await SessionService.get_session(session_id) or {}).get("message_count", 0) + 1,
        }
        # Only set title from user message if session title is still empty.
        # Truncate to 30 chars + ellipsis (matches the chat sidebar's width).
        # 优先展示文案（快捷指令 label），避免后台指令泄漏到会话标题。
        if role == "user":
            session_doc = await SessionService.get_session(session_id)
            if session_doc and not session_doc.get("title"):
                title_source = display_text or content
                update_fields["title"] = title_source[:30] + ("…" if len(title_source) > 30 else "")
        await SessionService.update_session(session_id, update_fields)

        return doc

    @staticmethod
    async def append_to_last_agent_message(
        session_id: str,
        timeline_entries: list[dict],
        *,
        token_usage: dict | None = None,
        request_id: str = "",
    ) -> None:
        """Append timeline entries to the last agent message in this session.

        Used by resume so that tool_result (user's answer) ends up in the
        same message as the original tool_call, keeping the conversation
        history consistent for frontend rendering.

        request_id: 更新为本轮（resume）的请求 id——消息级反馈（§8.2）
        以最后 request 为轮次键，否则 resume 段 load 的技能与反馈断链。
        """
        col = MessageService._collection()
        # Find the last agent message
        last_msg = await col.find_one(
            {"session_id": session_id, "role": "agent"},
            sort=[("created_at", -1)],
        )
        if last_msg is None:
            # No agent message to append to — create new
            await MessageService.add_message(
                session_id=session_id, role="agent",
                timeline_entries=timeline_entries,
                token_usage=token_usage or {},
                request_id=request_id,
            )
            return

        # Atomically append entries + merge token_usage
        update_doc: dict[str, Any] = {
            "$push": {"timeline_entries": {"$each": timeline_entries}},
            "$set": {"request_id": request_id or last_msg.get("request_id", "")},
        }
        if token_usage:
            update_doc["$set"]["token_usage"] = token_usage
        await col.update_one({"_id": last_msg["_id"]}, update_doc)

    # 忽略标记文案——与 resume 的 tool_result 同构持久化，前端卡片
    # 已答态直接展示该文本。
    DISMISSED_RESULT_TEXT = "(用户已忽略此问题)"
    _INTERRUPT_TOOL_NAMES = ("ask_clarification", "confirm_workflow")

    @staticmethod
    async def dismiss_pending_clarification(session_id: str) -> bool:
        """Close the pending ask_clarification/confirm_workflow card.

        用户不想回答 agent 的追问（问题不对 / 想改传文件）时关闭待答卡片：
        给最后一条 agent 消息里未答的 interrupt tool_call 追加一条合成
        tool_result（与 resume 持久化答案完全同构，前端按 tool_call_id
        配对合并）。卡片进入已答态后，下一次发送即走普通 stream 新一轮。

        LLM 上下文不受影响——checkpointer 里的 pending interrupt 由下一次
        普通 stream 输入自然丢弃（annotate_interruptions 合成
        「执行被中断」ToolMessage 补齐孤儿 tool call 配对）。

        Returns:
            True 表示找到并关闭了待答卡片；False 表示没有待答卡片（幂等）。
        """
        col = MessageService._collection()
        last_msg = await col.find_one(
            {"session_id": session_id, "role": "agent"},
            sort=[("created_at", -1)],
        )
        if last_msg is None:
            return False

        entries = last_msg.get("timeline_entries") or []
        # 已应答 tool_call 的配对键集合（tool_call_id 优先，回退
        # tool_name——与前端 agentMessageToDisplay 的合并逻辑一致）。
        answered_keys: set[str] = set()
        for e in entries:
            if e.get("type") == "tool_result":
                answered_keys.add(e.get("tool_call_id") or e.get("tool_name") or "")
        # 倒序定位最后一个未答的 interrupt tool_call（checkpointer 同时
        # 只挂起一个 interrupt，即流式结束前的最后一次追问）。
        for e in reversed(entries):
            if e.get("type") != "tool_call":
                continue
            name = e.get("tool_name") or ""
            if name not in MessageService._INTERRUPT_TOOL_NAMES:
                continue
            if (e.get("id") or name) in answered_keys:
                continue
            await col.update_one(
                {"_id": last_msg["_id"]},
                {
                    "$push": {
                        "timeline_entries": {
                            "type": "tool_result",
                            "tool_name": name,
                            "content": MessageService.DISMISSED_RESULT_TEXT,
                            "status": "success",
                            "tool_call_id": e.get("id") or "",
                        }
                    }
                },
            )
            return True
        return False

    @staticmethod
    async def list_messages(session_id: str) -> list[dict]:
        """List all messages for a session, ordered by creation time."""
        cursor = MessageService._collection().find({"session_id": session_id}).sort("created_at", 1)
        return await cursor.to_list(length=1000)
