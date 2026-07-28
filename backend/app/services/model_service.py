"""Model business logic — CRUD operations and API key encryption."""
from __future__ import annotations

import re

from loguru import logger

from app.core.crypto import (
    decrypt_api_key,
    encrypt_api_key,
    get_encryption_key,
    mask_api_key,
)
from app.core.errors import ConflictError, ValidationError
from app.db.mongodb import get_database
from app.models.model import Model, ModelStatus


class ModelService:
    """Service layer for Model (LLM endpoint) operations."""

    COLLECTION = "models"

    @staticmethod
    def _collection():
        return get_database()[ModelService.COLLECTION]

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    @staticmethod
    async def create_model(
        model_id: str,
        name: str,
        base_url: str,
        api_key: str,
        compatibility_type: str = "openai",
        auth_type: str = "bearer",
        auth_header_format: str = "Bearer {key}",
        default_params: dict | None = None,
        provider_tag: str = "",
        task_type: str = "chat",
    ) -> dict:
        """Create a new Model record with encrypted API key.

        Args:
            model_id: Upstream model identifier (e.g. "deepseek-chat").
            name: Display name (e.g. "DeepSeek V3 Chat").
            base_url: Upstream base URL.
            api_key: Plaintext API key — will be encrypted before storage.
            compatibility_type: "openai" or "anthropic".
            auth_type: Authentication scheme ("bearer" | "x_api_key" |
                "api_key_header" | "custom").
            auth_header_format: Auth header template (used when auth_type=custom).
            default_params: Default inference parameters.
            provider_tag: Optional grouping tag.
            task_type: Model purpose ("chat" | "embedding" | "rerank").

        Returns:
            Created Model MongoDB document.

        Raises:
            ConflictError: If model_id is already taken.
        """
        # model_id uniqueness check
        existing = await ModelService._collection().find_one({"model_id": model_id})
        if existing is not None:
            raise ConflictError(
                code="MODEL_ID_CONFLICT",
                message=f"模型标识 '{model_id}' 已被占用",
                details={"field": "model_id"},
            )

        # Encrypt API key
        master_key = get_encryption_key()
        encrypted_key = encrypt_api_key(api_key, master_key)

        model = Model(
            model_id=model_id,
            name=name,
            base_url=base_url,
            api_key=encrypted_key,
            compatibility_type=compatibility_type,
            auth_type=auth_type,
            auth_header_format=auth_header_format,
            default_params=default_params or {
                "temperature": 0.7,
                "max_tokens": 4096,
                "context_window": 128000,
            },
            provider_tag=provider_tag,
            status=ModelStatus.ACTIVE,
            task_type=task_type,
        )

        doc = {
            "_id": model.id,
            "model_id": model.model_id,
            "name": model.name,
            "base_url": model.base_url,
            "api_key": model.api_key,
            "compatibility_type": model.compatibility_type.value,
            "auth_type": model.auth_type.value,
            "auth_header_format": model.auth_header_format,
            "default_params": model.default_params,
            "status": model.status.value,
            "task_type": model.task_type.value,
            "last_test_success": None,
            "last_test_at": "",
            "provider_tag": model.provider_tag,
            "version": model.version,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

        try:
            await ModelService._collection().insert_one(doc)
        except Exception as exc:
            from pymongo.errors import DuplicateKeyError

            if isinstance(exc, DuplicateKeyError):
                raise ConflictError(
                    code="MODEL_ID_CONFLICT",
                    message=f"模型标识 '{model_id}' 已被占用",
                ) from exc
            raise ValidationError(
                code="MODEL_CREATE_FAILED",
                message="模型创建失败，请稍后重试",
            ) from exc

        logger.info(
            "model_created",
            model_id=model.model_id,
            model_name=model.name,
        )

        # Return doc with masked api_key for API response
        doc["api_key"] = mask_api_key(api_key)
        return doc

    @staticmethod
    async def get_model(model_id: str) -> dict | None:
        """Get a Model by its _id.

        Returns the document with masked api_key (safe for API response).
        """
        doc = await ModelService._collection().find_one({"_id": model_id})
        if doc is None:
            return None
        doc["api_key"] = ModelService._mask_doc_api_key(doc)
        return doc

    @staticmethod
    async def list_models(
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        provider_tag: str | None = None,
        task_type: str | None = None,
    ) -> tuple[list[dict], int]:
        """List Models with pagination and optional filtering.

        Args:
            page: Page number (1-based).
            page_size: Items per page (max 100).
            status: Optional status filter ("active" / "inactive").
            provider_tag: Optional provider tag filter.
            task_type: Optional model purpose filter ("chat" / "embedding" / "rerank").

        Returns:
            Tuple of (model_docs, total_count).
        """
        col = ModelService._collection()
        filter_query: dict = {}
        if status:
            filter_query["status"] = status
        if provider_tag:
            filter_query["provider_tag"] = {"$regex": re.escape(provider_tag), "$options": "i"}
        if task_type:
            filter_query["task_type"] = task_type

        total = await col.count_documents(filter_query)
        cursor = (
            col.find(filter_query)
            .sort("updated_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)

        # Mask api_key in all returned docs
        for item in items:
            item["api_key"] = ModelService._mask_doc_api_key(item)

        return items, total

    @staticmethod
    async def update_model(
        model_id: str,
        model_id_str: str,
        name: str,
        base_url: str,
        api_key: str,
        compatibility_type: str = "openai",
        auth_type: str = "bearer",
        auth_header_format: str = "Bearer {key}",
        default_params: dict | None = None,
        provider_tag: str = "",
        task_type: str = "chat",
    ) -> dict | None:
        """Update an existing Model (full replacement via PUT).

        Args:
            model_id: The Model's _id.
            model_id_str: New upstream model identifier.
            name: New display name.
            base_url: New upstream base URL.
            api_key: New plaintext API key.
            compatibility_type: New compatibility type.
            auth_type: New authentication scheme.
            auth_header_format: New auth header template (for custom auth).
            default_params: New default parameters.
            provider_tag: New provider tag.
            task_type: New model purpose ("chat" | "embedding" | "rerank").

        Returns:
            Updated Model document (with masked api_key), or None if not found.

        Raises:
            ConflictError: If model_id_str conflicts with another Model.
        """
        col = ModelService._collection()

        existing_doc = await col.find_one({"_id": model_id})
        if existing_doc is None:
            return None

        # Check model_id uniqueness (exclude self)
        id_conflict = await col.find_one(
            {"model_id": model_id_str, "_id": {"$ne": model_id}}
        )
        if id_conflict is not None:
            raise ConflictError(
                code="MODEL_ID_CONFLICT",
                message=f"模型标识 '{model_id_str}' 已被占用",
                details={"field": "model_id"},
            )

        from app.models.base import utc_now

        now_iso = utc_now().isoformat()
        new_version = existing_doc.get("version", 1) + 1

        # Preserve existing key when api_key is empty (partial update),
        # otherwise encrypt the new plaintext key.
        if api_key:
            master_key = get_encryption_key()
            encrypted_key = encrypt_api_key(api_key, master_key)
        else:
            encrypted_key = existing_doc.get("api_key", "")

        set_fields: dict = {
            "model_id": model_id_str,
            "name": name,
            "base_url": base_url,
            "api_key": encrypted_key,
            "compatibility_type": compatibility_type,
            "auth_type": auth_type,
            "auth_header_format": auth_header_format,
            "default_params": default_params or {
                "temperature": 0.7,
                "max_tokens": 4096,
                "context_window": 128000,
            },
            "provider_tag": provider_tag,
            "task_type": task_type,
            "version": new_version,
            "updated_at": now_iso,
        }

        await col.update_one({"_id": model_id}, {"$set": set_fields})

        logger.info(
            "model_updated",
            model_id=model_id,
            new_version=new_version,
        )

        updated = await ModelService.get_model(model_id)
        return updated

    @staticmethod
    async def delete_model(model_id: str) -> bool:
        """Delete a Model by its _id.

        Checks whether any Agent references this model before deleting.

        Args:
            model_id: The Model's _id.

        Returns:
            True if deleted, False if not found.

        Raises:
            ConflictError: If the model is referenced by one or more Agents.
        """
        col = ModelService._collection()

        existing_doc = await col.find_one({"_id": model_id})
        if existing_doc is None:
            return False

        # Check Agent references — list ALL referencing agents (like TOOL_IN_USE)
        agents_col = get_database()["agents"]
        cursor = agents_col.find(
            {"$or": [
                {"default_model": model_id},
                {"llm_config.default_model": model_id},  # backward compat
            ]},
            {"name": 1},
        )
        referencing_agents = await cursor.to_list(length=100)
        if referencing_agents:
            agent_names = [a.get("name", a.get("_id", "")) for a in referencing_agents]
            raise ConflictError(
                code="MODEL_IN_USE",
                message=f"模型正在被以下 Agent 引用，无法删除：{', '.join(agent_names)}",
                details={"agent_names": agent_names},
            )

        result = await col.delete_one({"_id": model_id})
        if result.deleted_count > 0:
            logger.info(
                "model_deleted",
                model_id=model_id,
                model_name=existing_doc.get("name"),
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Connectivity testing
    # ------------------------------------------------------------------

    @staticmethod
    async def test_model(model_id: str) -> dict:
        """Send a minimal probe request to validate model connectivity.

        Reads the stored Model document, decrypts the api_key, builds a
        LangChain chat client via ``llm_factory.build_client_from_doc``,
        and invokes a short "ping" prompt. Captures latency and any
        upstream error.

        Args:
            model_id: The Model's ``_id``.

        Returns:
            Dict with the following keys:
            - ``success`` (bool): whether the probe succeeded.
            - ``latency_ms`` (int): round-trip duration in milliseconds.
            - ``reply`` (str): model's reply text (truncated to 500 chars).
            - ``error`` (str): empty on success, otherwise a short error
              description (translated/curated for display).
            - ``error_code`` (str): machine-readable code, "" on success.

        Raises:
            NotFoundError: If the model is not found or api_key cannot
                be decrypted (treated as misconfiguration → 404/422).
        """
        import time
        from datetime import UTC, datetime

        from langchain_core.messages import HumanMessage

        from app.core.errors import NotFoundError
        from app.engine.llm_factory import build_client_from_doc

        doc = await ModelService.get_model_config_by_id(model_id)
        if doc is None:
            raise NotFoundError(
                code="MODEL_NOT_FOUND",
                message=f"模型 {model_id} 不存在或密钥解密失败",
            )

        started_at = time.perf_counter()
        try:
            client = build_client_from_doc(doc, {})
            reply = await client.ainvoke([HumanMessage(content="hello")])
            latency_ms = int((time.perf_counter() - started_at) * 1000)

            # Extract text content (LangChain AIMessage .content may be str or list)
            content = reply.content if hasattr(reply, "content") else reply
            text = content if isinstance(content, str) else str(content)
            truncated = text[:500]

            logger.info(
                "model_test_ok",
                model_id=model_id,
                latency_ms=latency_ms,
            )
            tested_at = datetime.now(UTC).isoformat()
            result = {
                "success": True,
                "latency_ms": latency_ms,
                "reply": truncated,
                "error": "",
                "error_code": "",
                "tested_at": tested_at,
            }
            await ModelService._persist_test_result(model_id, True, tested_at)
            return result

        except Exception as exc:
            latency_ms = int((time.perf_counter() - started_at) * 1000)
            error_code, error_msg = _curate_test_error(exc)

            logger.warning(
                "model_test_failed",
                model_id=model_id,
                latency_ms=latency_ms,
                error_code=error_code,
                error=str(exc)[:300],
            )
            tested_at = datetime.now(UTC).isoformat()
            result = {
                "success": False,
                "latency_ms": latency_ms,
                "reply": "",
                "error": error_msg,
                "error_code": error_code,
                "tested_at": tested_at,
            }
            await ModelService._persist_test_result(model_id, False, tested_at)
            return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _persist_test_result(model_id: str, success: bool, tested_at: str) -> None:
        """Persist the most recent connectivity-test outcome onto the model doc.

        Keeps the list view's connectivity badge in sync without a separate
        refresh — the test endpoint is the only writer for these fields.
        """
        await ModelService._collection().update_one(
            {"_id": model_id},
            {"$set": {"last_test_success": success, "last_test_at": tested_at}},
        )

    @staticmethod
    async def get_model_config_by_id(model_id: str) -> dict | None:
        """Get full model config including decrypted API key.

        This is an internal method used by llm_factory to construct
        LLM client instances. The returned document contains the
        plaintext API key — never expose this via the API layer.

        Args:
            model_id: The Model's _id (e.g. "model_01HXYZ...").

        Returns:
            Model document with decrypted api_key, or None.
        """
        doc = await ModelService._collection().find_one({"_id": model_id})
        if doc is None:
            return None

        # Decrypt API key for internal use
        try:
            master_key = get_encryption_key()
            doc["api_key"] = decrypt_api_key(doc["api_key"], master_key)
        except Exception:
            logger.error(
                "model_api_key_decrypt_failed",
                model_id=model_id,
            )
            return None

        return doc

    @staticmethod
    def _mask_doc_api_key(doc: dict) -> str:
        """Mask the encrypted api_key in a doc for safe API response.

        Since the stored value is encrypted, we decrypt first to get
        the plaintext for masking.
        """
        encrypted = doc.get("api_key", "")
        if not encrypted:
            return ""
        try:
            master_key = get_encryption_key()
            plaintext = decrypt_api_key(encrypted, master_key)
            return mask_api_key(plaintext)
        except Exception:
            # If decryption fails, return a generic mask
            return "****"


def _curate_test_error(exc: Exception) -> tuple[str, str]:
    """Map an upstream exception to a human-readable (code, message) pair.

    Keeps the mapping close to the service layer so the API stays clean.
    Returns:
        (error_code, error_message) tuple.
    """
    exc_name = type(exc).__name__
    exc_msg = str(exc).lower()

    # Authentication / key errors
    if any(kw in exc_msg for kw in ("401", "unauthorized", "invalid api key", "invalid x-api-key", "authentication")):
        return "AUTH_FAILED", "API Key 无效或已过期，请检查密钥配置"

    # Permission / quota
    if any(kw in exc_msg for kw in ("403", "forbidden", "quota", "insufficient_quota")):
        return "QUOTA_EXCEEDED", "API 配额不足或权限受限"

    # Not found — model_id may be wrong
    if any(kw in exc_msg for kw in ("404", "not found", "model_not_found")):
        return "MODEL_NOT_FOUND_UPSTREAM", "上游未找到该模型标识，请确认 model_id 是否正确"

    # Rate limit
    if any(kw in exc_msg for kw in ("429", "rate_limit", "rate limit", "too many requests")):
        return "RATE_LIMITED", "上游请求频率超限，请稍后重试"

    # Timeout
    if any(kw in exc_msg for kw in ("timeout", "timed out")):
        return "TIMEOUT", "连接上游超时，请检查网络或 base_url 是否可达"

    # Connection errors
    if any(kw in exc_msg for kw in ("connection", "connect", "resolve", "name or service", "network")):
        return "CONNECTION_ERROR", "无法连接到上游服务，请检查 base_url 和网络"

    # SSL errors
    if any(kw in exc_msg for kw in ("ssl", "certificate", "tls")):
        return "SSL_ERROR", "SSL 证书验证失败，请检查 base_url"

    # Fallback
    return "UNKNOWN_ERROR", f"测试失败 ({exc_name}): {str(exc)[:200]}"
