"""Tool business logic — CRUD operations and Markdown Skill registration."""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.core.errors import ConflictError, ValidationError
from app.db.mongodb import get_database
from app.engine.tool.skill_fs import (
    delete_skill_dir,
    get_skill_base_path,
    list_skill_files,
    materialize_skill,
    read_skill_file,
)
from app.engine.tool.skill_parser import (
    ParsedSkill,
    ParsedSkillDirectory,
    SkillFileEntry,
)
from app.models.base import generate_id, utc_now

MAX_FILE_SIZE = 1_000_000  # 1 MB
MAX_DIRECTORY_SIZE = 10_000_000  # 10 MB


class ToolService:
    """Service layer for Tool operations.

    Tools are registered from Markdown Skill file uploads.  The parser
    (:func:`parse_skill_markdown`) extracts YAML frontmatter and the
    Markdown body, then the service persists a normalized document in
    the ``tools`` collection.
    """

    COLLECTION = "tools"

    @staticmethod
    def _collection():
        return get_database()[ToolService.COLLECTION]

    # ------------------------------------------------------------------
    # Upload & registration
    # ------------------------------------------------------------------

    @staticmethod
    async def _insert_skill_doc(name: str, doc: dict) -> dict:
        """Insert a Skill document into the tools collection.

        Handles name-conflict check + ``DuplicateKeyError`` + generic
        exception wrapping, which is shared between the single-file and
        directory creation paths.

        Args:
            name: Tool name (for conflict error messages).
            doc: Fully assembled MongoDB document to insert.

        Returns:
            The inserted document.

        Raises:
            ConflictError: If a tool with the same name already exists.
            ValidationError: If the insert fails for an unexpected reason.
        """
        existing = await ToolService._collection().find_one({"name": name})
        if existing is not None:
            raise ConflictError(
                code="TOOL_NAME_CONFLICT",
                message=f"工具名称 '{name}' 已被占用",
                details={"field": "name"},
            )

        try:
            await ToolService._collection().insert_one(doc)
        except Exception as exc:
            from pymongo.errors import DuplicateKeyError

            if isinstance(exc, DuplicateKeyError):
                raise ConflictError(
                    code="TOOL_NAME_CONFLICT",
                    message=f"工具名称 '{name}' 已被占用",
                ) from exc
            raise ValidationError(
                code="TOOL_CREATE_FAILED",
                message="工具创建失败，请稍后重试",
            ) from exc

        return doc

    @staticmethod
    async def create_custom_tool(
        *,
        name: str,
        description: str,
        source: str,
        user_args_schema: dict | None = None,
        llm_args_schema: dict | None = None,
        endpoint: dict | None = None,
        code: str = "",
        prebuilt_name: str = "",
    ) -> dict:
        """Create a custom tool (openapi / code / prebuilt).

        Args:
            user_args_schema: 用户参数 schema（Agent 绑定时填入）。
                字段标记 sensitive=true 的加密存储。
            llm_args_schema: LLM 参数 schema（运行时 LLM 填入）。
        """
        from app.models.tool import Tool

        existing = await ToolService.find_by_name(name)
        if existing:
            from app.core.errors import ValidationError
            raise ValidationError(
                code="TOOL_NAME_EXISTS",
                message=f"工具名 '{name}' 已存在",
            )

        tool = Tool(
            name=name,
            description=description,
            source=source,
            user_args_schema=user_args_schema or {},
            llm_args_schema=llm_args_schema or {},
            endpoint=endpoint or {},
            code=code,
            prebuilt_name=prebuilt_name,
        )
        doc = tool.model_dump(by_alias=True)
        db = get_database()
        await db[ToolService.COLLECTION].insert_one(doc)
        logger.info("custom_tool_created", tool_id=doc["_id"], name=name, source=source)
        return doc

    @staticmethod
    async def create_tool_from_parsed(
        parsed: ParsedSkill,
        source_file: str = "",
    ) -> dict:
        """Create a Tool from a parsed Markdown Skill. (AC3)

        The SKILL.md content is materialized to disk under
        ``SKILLS_DIR/{name}/``.  MongoDB only stores registration
        metadata (no file content).

        Args:
            parsed: Parsed skill data (name/description/schemas/instructions).
            source_file: Original filename.

        Returns:
            Created Tool MongoDB document.

        Raises:
            ConflictError: If a tool with the same name already exists.
        """
        now_iso = utc_now().isoformat()
        doc = {
            "_id": generate_id("tool"),
            "name": parsed.name,
            "description": parsed.description,
            "input_schema": parsed.input_schema,
            "output_schema": parsed.output_schema,
            "instructions": parsed.instructions,
            "source": "markdown",
            "source_file": source_file,
            "version": 1,
            "tags": [],
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        doc = await ToolService._insert_skill_doc(parsed.name, doc)

        # Materialize SKILL.md to disk
        skill_entry = SkillFileEntry(
            path="SKILL.md",
            content=parsed.instructions,
            size=len(parsed.instructions.encode("utf-8")),
        )
        materialize_skill(parsed.name, [skill_entry])

        logger.info(
            "tool_created",
            tool_id=doc["_id"],
            tool_name=doc["name"],
            source_file=source_file,
        )
        return doc

    @staticmethod
    async def create_tool_from_directory(
        parsed_dir: ParsedSkillDirectory,
        directory_name: str = "",
    ) -> dict:
        """Create a Tool from a parsed Skill directory package.

        Files are materialized to ``SKILLS_DIR/{name}/`` on disk.
        MongoDB stores only the registration metadata.

        Args:
            parsed_dir: Parsed directory data (parsed SKILL.md + file list).
            directory_name: Original directory name.

        Returns:
            Created Tool MongoDB document.

        Raises:
            ConflictError: If a tool with the same name already exists.
            ValidationError: If total file size exceeds limit.
        """
        # Validate total size
        total_size = sum(f.size for f in parsed_dir.files)
        if total_size > MAX_DIRECTORY_SIZE:
            raise ValidationError(
                code="DIRECTORY_TOO_LARGE",
                message=f"目录总大小（{total_size} bytes）超过上限（{MAX_DIRECTORY_SIZE} bytes）",
            )

        now_iso = utc_now().isoformat()

        doc = {
            "_id": generate_id("tool"),
            "name": parsed_dir.parsed.name,
            "description": parsed_dir.parsed.description,
            "input_schema": parsed_dir.parsed.input_schema,
            "output_schema": parsed_dir.parsed.output_schema,
            "instructions": parsed_dir.parsed.instructions,
            "source": "markdown",
            "source_file": directory_name,
            "version": 1,
            "tags": [],
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        doc = await ToolService._insert_skill_doc(parsed_dir.parsed.name, doc)

        # Materialize all files to disk
        materialize_skill(parsed_dir.parsed.name, parsed_dir.files)

        logger.info(
            "tool_created_from_directory",
            tool_id=doc["_id"],
            tool_name=doc["name"],
            directory_name=directory_name,
            file_count=len(parsed_dir.files),
        )
        return doc

    @staticmethod
    async def get_tool_files(tool_id: str) -> list[dict] | None:
        """Get the file tree structure for a Tool.

        Scans the Skill directory on disk.

        Args:
            tool_id: The Tool's ID.

        Returns:
            List of file tree nodes, or None if tool not found.
        """
        doc = await ToolService._collection().find_one(
            {"_id": tool_id}, {"name": 1}
        )
        if doc is None:
            return None

        name = doc.get("name")
        if not name:
            return []

        files = list_skill_files(name)
        if not files:
            return []

        return ToolService._build_file_tree(files)

    @staticmethod
    async def get_tool_file_content(
        tool_id: str, file_path: str
    ) -> dict | None:
        """Get a single file's content from a Tool.

        Reads the file from the Skill directory on disk.

        Args:
            tool_id: The Tool's ID.
            file_path: Relative file path within the Skill directory.

        Returns:
            Dict with path/content/size, or None if not found.
        """
        doc = await ToolService._collection().find_one(
            {"_id": tool_id}, {"name": 1}
        )
        if doc is None:
            return None

        name = doc.get("name")
        if not name:
            return None

        content = read_skill_file(name, file_path)
        if content is None:
            return None

        return {"path": file_path, "content": content, "size": len(content.encode("utf-8"))}

    @staticmethod
    async def update_tool_file(
        tool_id: str, file_path: str, new_content: str
    ) -> dict | None:
        """Update a single file's content in a Tool.

        Writes to disk.  If the updated file is SKILL.md, also updates
        name/description/instructions on the Tool document itself.

        Args:
            tool_id: The Tool's ID.
            file_path: Relative file path.
            new_content: New file content.

        Returns:
            Updated file dict, or None if tool/file not found.
        """
        col = ToolService._collection()

        # Verify tool exists
        doc = await col.find_one({"_id": tool_id})
        if doc is None:
            return None

        name = doc.get("name")
        if not name:
            return None

        # Verify file exists on disk
        existing = read_skill_file(name, file_path)
        if existing is None:
            return None

        # Write to disk
        full_path = get_skill_base_path(name) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(new_content, encoding="utf-8")

        now_iso = utc_now().isoformat()
        new_size = len(new_content.encode("utf-8"))

        # Update the document's updated_at timestamp
        await col.update_one(
            {"_id": tool_id},
            {"$set": {"updated_at": now_iso}},
        )

        # If SKILL.md was updated, re-parse and update top-level fields
        if file_path == "SKILL.md":
            try:
                from app.engine.tool.skill_parser import parse_skill_markdown

                parsed = parse_skill_markdown(new_content, "SKILL.md")
                await col.update_one(
                    {"_id": tool_id},
                    {
                        "$set": {
                            "name": parsed.name,
                            "description": parsed.description,
                            "input_schema": parsed.input_schema,
                            "output_schema": parsed.output_schema,
                            "instructions": parsed.instructions,
                        }
                    },
                )
            except Exception as exc:
                logger.warning(
                    "skill_md_reparse_failed",
                    tool_id=tool_id,
                    error=str(exc),
                )

        return {"path": file_path, "content": new_content, "size": new_size}

    @staticmethod
    def _build_file_tree(files: list[dict]) -> list[dict]:
        """Build a hierarchical file tree from flat file list.

        Converts flat paths like 'steps/step-01.md' into nested tree
        structure suitable for Ant Design Tree component.

        Args:
            files: List of file dicts with 'path', 'content', 'size'.

        Returns:
            List of tree node dicts.
        """
        root: dict[str, Any] = {}

        for f in files:
            parts = f["path"].split("/")
            current = root
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                if is_last:
                    # File node
                    current[part] = {
                        "key": f["path"],
                        "title": part,
                        "is_leaf": True,
                        "size": f.get("size", 0),
                    }
                else:
                    # Directory node
                    if part not in current or not isinstance(current[part], dict) or "children" not in current[part]:
                        current[part] = {"children": {}}
                    current = current[part]["children"]

        def _dict_to_nodes(d: dict) -> list[dict]:
            nodes = []
            for name, value in d.items():
                if "children" in value and isinstance(value["children"], dict):
                    # Directory node
                    nodes.append({
                        "key": name,
                        "title": name,
                        "is_leaf": False,
                        "children": _dict_to_nodes(value["children"]),
                    })
                else:
                    # File node
                    nodes.append({
                        "key": value["key"],
                        "title": value["title"],
                        "is_leaf": value.get("is_leaf", True),
                        "size": value.get("size", 0),
                    })
            return nodes

        return _dict_to_nodes(root)

    @staticmethod
    async def find_by_name(name: str) -> dict | None:
        """Look up a tool by name (case-sensitive)."""
        return await ToolService._collection().find_one({"name": name})

    @staticmethod
    async def get_tools_by_ids(tool_ids: list[str]) -> list[dict]:
        """Fetch multiple Tools by their IDs.

        Args:
            tool_ids: List of Tool IDs to fetch.

        Returns:
            List of matching Tool documents (may be fewer than
            ``len(tool_ids)`` if some IDs don't exist).
        """
        if not tool_ids:
            return []
        cursor = ToolService._collection().find({"_id": {"$in": tool_ids}})
        return await cursor.to_list(length=len(tool_ids))

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    @staticmethod
    async def get_tool(tool_id: str) -> dict | None:
        """Get a Tool by ID. (AC4)"""
        return await ToolService._collection().find_one({"_id": tool_id})

    @staticmethod
    async def list_tools(
        page: int = 1,
        page_size: int = 20,
        name: str | None = None,
        source: str | None = None,
        mcp_connection_id: str | None = None,
    ) -> tuple[list[dict], int]:
        """List Tools with pagination and optional filtering. (AC4)

        Args:
            page: Page number (1-based).
            page_size: Items per page.
            name: Optional name substring filter (case-insensitive).
            source: Optional source filter (``markdown`` / ``mcp`` / ``builtin``).
            mcp_connection_id: Optional MCP connection ID filter.

        Returns:
            Tuple of (tool_docs, total_count).
        """
        col = ToolService._collection()
        filter_query: dict = {}
        if name:
            filter_query["name"] = {"$regex": re.escape(name), "$options": "i"}
        if source:
            filter_query["source"] = source
        if mcp_connection_id:
            filter_query["mcp_connection_id"] = mcp_connection_id

        total = await col.count_documents(filter_query)
        cursor = (
            col.find(filter_query)
            .sort("updated_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        return items, total

    @staticmethod
    async def update_tool(
        tool_id: str,
        tags: list[str] | None = None,
    ) -> dict | None:
        """Update an existing Tool. (AC5)

        Only ``tags`` is user-editable. Auto-increments
        ``version`` and updates ``updated_at``.

        Args:
            tool_id: The Tool's ID.
            tags: Optional new tags. None preserves existing.

        Returns:
            Updated Tool document, or None if not found.
        """
        col = ToolService._collection()

        existing_doc = await col.find_one({"_id": tool_id})
        if existing_doc is None:
            return None

        now_iso = utc_now().isoformat()
        new_version = existing_doc.get("version", 1) + 1

        set_fields: dict = {
            "version": new_version,
            "updated_at": now_iso,
        }
        if tags is not None:
            set_fields["tags"] = tags

        await col.update_one({"_id": tool_id}, {"$set": set_fields})

        logger.info(
            "tool_updated",
            tool_id=tool_id,
            new_version=new_version,
        )
        return await ToolService.get_tool(tool_id)

    @staticmethod
    async def set_avatar(tool_id: str, avatar_url: str) -> dict | None:
        """Set only the avatar field.

        The avatar is a cosmetic (display-only) field, not tool config/behavior.
        Tool has no published-immutability guard, so this is a plain field
        update. Used by the avatar upload endpoint.
        """
        col = ToolService._collection()
        if await col.find_one({"_id": tool_id}) is None:
            return None
        await col.update_one(
            {"_id": tool_id},
            {"$set": {"avatar": avatar_url, "updated_at": utc_now().isoformat()}},
        )
        return await ToolService.get_tool(tool_id)

    @staticmethod
    async def delete_tool(tool_id: str) -> bool:
        """Delete a Tool by ID. (AC5)

        Checks that no Agent references the tool via ``tool_ids``.

        Args:
            tool_id: The Tool's ID.

        Returns:
            True if deleted, False if not found.

        Raises:
            ConflictError: If one or more Agents reference this tool.
        """
        col = ToolService._collection()

        existing_doc = await col.find_one({"_id": tool_id})
        if existing_doc is None:
            return False

        # Check Agent references (both skill_ids and legacy tool_ids)
        agents_col = get_database()["agents"]
        cursor = agents_col.find(
            {"$or": [{"tool_ids": tool_id}, {"skill_ids": tool_id}]},
            {"name": 1},
        )
        referencing_agents = await cursor.to_list(length=100)
        if referencing_agents:
            agent_names = [a.get("name", a.get("_id", "")) for a in referencing_agents]
            raise ConflictError(
                code="TOOL_IN_USE",
                message=(
                    f"工具 '{existing_doc.get('name')}' 正在被以下 Agent 引用，"
                    f"无法删除：{', '.join(agent_names)}"
                ),
                details={"agent_names": agent_names},
            )

        result = await col.delete_one({"_id": tool_id})
        if result.deleted_count > 0:
            # Clean up skill files on disk
            tool_name = existing_doc.get("name")
            if tool_name:
                delete_skill_dir(tool_name)

            # Best-effort: 删除该 Tool 的头像文件（失败仅记日志，不阻断删除）
            try:
                import pathlib

                from app.core.config import settings

                avatar_file = pathlib.Path(settings.SKILL_AVATARS_CONTAINER_DIR) / f"{tool_id}.png"
                if avatar_file.exists():
                    avatar_file.unlink()
            except Exception as exc:
                logger.warning("tool_avatar_cleanup_failed", tool_id=tool_id, error=str(exc))

            logger.info(
                "tool_deleted",
                tool_id=tool_id,
                tool_name=tool_name,
            )
            return True
        return False
