"""File management data models — FileRef and FileUsage.

File 是一等公民，独立于 session/workflow/cron 等消费者。
所有使用方通过 FileUsage 引用 FileRef，实现跨场景复用。
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import generate_id, utc_now


class FileConsumerKind(StrEnum):
    """文件使用者类型 — 决定生命周期和清理策略。

    新增消费者只需添加枚举值，模型零改动。
    """

    USER_LIBRARY = "user_library"  # 用户文件库（长期持有）
    SESSION_MESSAGE = "session_message"  # 聊天消息附件
    WORKFLOW_RUN = "workflow_run"  # Workflow 运行时引用
    CRON_JOB = "cron_job"  # 定时任务配置（长期持有）
    KNOWLEDGE_BASE = "knowledge_base"  # 向量知识库文档（长期持有）


class FileRef(BaseModel):
    """文件引用 — 一等公民，独立聚合。

    存储位置：{WORKSPACES_CONTAINER_DIR}/{user_id}/files/{file_id}

    FileRef 不绑定任何消费者，所有使用方通过 FileUsage 引用。
    这使得文件可以在多个场景中复用（聊天、workflow、定时任务等）。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("file"), alias="_id")
    owner_user_id: str = Field(..., description="所属用户（权限根）")
    storage_key: str = Field(
        ...,
        description="存储路径，格式: {user_id}/files/{file_id}",
    )
    name: str = Field(..., description="原始文件名")
    size: int = Field(..., ge=0, description="文件大小（字节数）")
    mime_type: str = Field(
        default="application/octet-stream",
        description="MIME 类型",
    )
    sha256: str = Field(..., description="文件内容哈希（用于去重校验）")
    origin_kind: FileConsumerKind = Field(
        ...,
        description="最初来源类型",
    )
    origin_id: str = Field(
        ...,
        description="来源 ID（如 msg_xxx / cron_xxx / user_xxx）",
    )
    status: str = Field(
        default="active",
        description="文件状态: active | trashed",
    )
    created_at: str = Field(
        default_factory=lambda: utc_now().isoformat(),
        description="创建时间（ISO 时间戳）",
    )
    updated_at: str = Field(
        default_factory=lambda: utc_now().isoformat(),
        description="更新时间（ISO 时间戳）",
    )


class FileUsage(BaseModel):
    """文件使用记录 — 跟踪所有引用方。

    清理规则：usages 为空时，FileRef 可被手动删除。
    联合唯一约束：(file_id, consumer_kind, consumer_id) 不可重复。
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("fu"), alias="_id")
    file_id: str = Field(..., description="指向 FileRef.id")
    consumer_kind: FileConsumerKind = Field(
        ...,
        description="消费者类型",
    )
    consumer_id: str = Field(
        ...,
        description="消费者 ID（如 msg_xxx / run_xxx / cron_xxx）",
    )
    granted_at: str = Field(
        default_factory=lambda: utc_now().isoformat(),
        description="引用授予时间（ISO 时间戳）",
    )
    expires_at: str | None = Field(
        default=None,
        description="到期时间（None 表示长期持有）",
    )
