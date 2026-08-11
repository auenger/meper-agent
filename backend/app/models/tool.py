"""Tool data model for MongoDB — unified tool pool (Markdown Skill / MCP / etc.)."""
from typing import Any

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from app.models.base import generate_id, utc_now


class SkillFile(BaseModel):
    """A single file inside a Skill directory package."""

    path: str = Field(..., description="相对路径，如 'SKILL.md' 或 'steps/step-01.md'")
    content: str = Field(..., description="文件内容（UTF-8 文本）")
    size: int = Field(default=0, description="文件大小（bytes）")


class Tool(BaseModel):
    """MongoDB tool document model.

    Follows the same pattern as ``Agent`` — raw Pydantic model,
    serialized to dict for MongoDB insertion/update.

    ``source`` distinguishes the origin (``"markdown"`` for uploaded
    Skill files, ``"mcp"`` for MCP-discovered tools, etc.).

    ``files`` stores directory-based Skill packages: a list of
    :class:`SkillFile` entries (path + content).  Legacy single-file
    tools keep ``files`` as an empty list.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: generate_id("tool"), alias="_id")
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    instructions: str = Field(default="", description="Markdown body / usage notes")
    source: str = Field(
        default="markdown",
        description="Origin: markdown / mcp / openapi / code / prebuilt",
    )
    source_file: str = Field(default="", description="Original filename")
    mcp_connection_id: str = Field(
        default="",
        description="关联的 MCP 连接 ID（仅 source=mcp 时有效）",
    )
    # ── Custom tool fields (source=openapi / code / prebuilt) ──────────
    # 用户参数：Agent 绑定时填入的参数（含敏感字段如 token）
    user_args_schema: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "用户参数 JSON Schema。Agent 绑定时由用户填入固定值。"
            "字段标记 sensitive=true 的加密存储。"
            "模板用 {{user.xxx}} 引用。不暴露给 LLM。"
        ),
    )
    # LLM 参数：运行时 LLM 调用时动态填入
    llm_args_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="LLM 参数 JSON Schema。LLM 调用时根据对话上下文填入。模板用 {{llm.xxx}} 引用。",
    )
    endpoint: dict[str, Any] = Field(
        default_factory=dict,
        description="HTTP endpoint 定义（source=openapi 时）",
    )
    code: str = Field(
        default="",
        description="用户自定义 Python 代码（source=code 时）",
    )
    prebuilt_name: str = Field(
        default="",
        description="预构建工具名称（source=prebuilt 时）",
    )
    version: int = Field(default=1, ge=1)
    tags: list[str] = Field(default_factory=list)
    avatar: str = Field(
        default="",
        description="头像：图片 URL 路径(/api/v1/skill-avatars/{id}.png)或空",
    )
    files: list[SkillFile] = Field(
        default_factory=list,
        description="目录模式下的文件列表（单文件模式为空）",
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
