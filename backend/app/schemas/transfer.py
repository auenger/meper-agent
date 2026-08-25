"""Pydantic schemas for resource transfer (.afpkg export / import)."""
from __future__ import annotations

from pydantic import BaseModel, Field

# 支持导入导出的资源 kind（与 manifest.resources[].kind 一一对应）。
# knowledge_base 仅支持 tree（Wiki）型；vector 型见 docs/resource-transfer-plan.md 非目标。
TRANSFER_KINDS: frozenset[str] = frozenset(
    {
        "agent",
        "workflow",
        "skill",           # tools 集合 source=markdown（官方 Skill）
        "tool",            # tools 集合 source=openapi/code/prebuilt（自定义工具）
        "mcp_connection",
        "mcp_category",
        "model",
        "knowledge_base",  # tree 型
    }
)


class ResourceRef(BaseModel):
    """A single top-level resource to export."""

    kind: str
    id: str = Field(default="", description="资源 ID（skill/tool/mcp/model/kb/agent/workflow 的 _id；mcp_category 同）")


class ExportRequest(BaseModel):
    """Request body for POST /transfer/export."""

    resources: list[ResourceRef] = Field(..., min_length=1, max_length=100)
    include_dependencies: bool = Field(default=True, description="是否自动携带依赖资源（引用图闭包）")


class ImportItem(BaseModel):
    """One imported resource in the report."""

    kind: str
    name: str = ""
    original_id: str = ""
    new_id: str = ""
    existing_id: str = Field(default="", description="复用模式下指向的本实例已有资源 ID")
    renamed_from: str = Field(default="", description="名称冲突改名时改名前的基础名，否则为空")


class ImportWarningItem(BaseModel):
    """A non-fatal issue: 敏感字段剔除 / 缺失依赖 / 改名等。"""

    kind: str = ""
    name: str = ""
    field: str = ""
    message: str


class ImportErrorItem(BaseModel):
    """A fatal per-resource error（该资源导入失败，不阻断其他资源）。"""

    kind: str = ""
    name: str = ""
    message: str


class ImportSummary(BaseModel):
    created: int = 0
    reused: int = 0
    skipped: int = 0
    warnings: int = 0
    errors: int = 0


class ImportReport(BaseModel):
    """Result of POST /transfer/import（dry_run 与真导入同构）."""

    dry_run: bool = False
    summary: ImportSummary = Field(default_factory=ImportSummary)
    created: list[ImportItem] = Field(default_factory=list)
    reused: list[ImportItem] = Field(default_factory=list)
    skipped: list[ImportItem] = Field(default_factory=list)
    warnings: list[ImportWarningItem] = Field(default_factory=list)
    errors: list[ImportErrorItem] = Field(default_factory=list)
