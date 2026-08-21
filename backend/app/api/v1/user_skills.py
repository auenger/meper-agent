"""用户技能与记忆 REST API（"技能广场 / 我的技能 / 我的记忆"，§7/§8）。

- GET    /user-skills              我的技能列表（own + installed，含 stats）
- GET    /user-skills/{skill_id}   详情（含磁盘全文；已发布技能任何人可预览）
- PUT    /user-skills/{skill_id}   编辑内容（markdown 编辑器直改）
- DELETE /user-skills/{skill_id}   删除
- GET    /user-skills/marketplace  技能广场（已发布，积分排序，含我的安装/投票态）
- POST   /user-skills/{id}/submit  提交发布审核
- POST   /user-skills/{id}/install 安装（重名需 alias）；DELETE 卸载
- POST   /user-skills/{id}/fork    复制为己用（derived_from 溯源）
- POST   /user-skills/messages/vote     消息级反馈（§8.2 v2：赞回复→本轮技能派生加分）
- GET    /user-skills/sessions/{sid}/feedback   会话各轮反馈态与使用技能
- GET/POST /user-skills/admin/review  管理员审核台
- GET/PUT/DELETE /user-skills/memory  我的记忆
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.errors import NotFoundError
from app.core.security import get_current_user, require_any_role
from app.models.user_skill import MEMORY_TOTAL_CHAR_LIMIT
from app.schemas.user import UserResponse
from app.services.user_profile_service import MemoryError, UserProfileService
from app.services.user_skill_service import UserSkillError, UserSkillService

router = APIRouter(prefix="/user-skills", tags=["user-skills"])


class SkillItem(BaseModel):
    # alias 接收 Mongo 的 _id；serialization_alias 确保 FastAPI 响应输出 "id"
    # （默认 by_alias 序列化会把 _id 原样吐回，前端拿不到 id → undefined bug）
    id: str = Field(alias="_id", serialization_alias="id")
    name: str
    description: str = ""
    status: str = "private"
    source: str = "own"
    binding_enabled: bool = True
    stats: dict = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class SkillDetail(SkillItem):
    content: str = ""


class SkillCreateRequest(BaseModel):
    name: str = Field(..., description="技能名（用户命名空间内唯一）")
    content: str = Field(..., description="SKILL.md 全文（含 frontmatter）")


class SkillUpdateRequest(BaseModel):
    content: str = ""
    binding_enabled: bool | None = None


class InstallRequest(BaseModel):
    alias: str = Field(default="", description="安装别名（启用列表内重名时必填，§7.4）")


class MessageVoteRequest(BaseModel):
    """消息级反馈（§8.2 v2）：点赞对象是模型的一轮回复，技能加分由后台派生。"""
    session_id: str = Field(..., description="会话 id")
    request_id: str = Field(..., description="本轮执行请求 id（消息的轮次键）")
    value: int = Field(..., description="1 = 👍 | -1 = 👎")


class ReviewRequest(BaseModel):
    action: str = Field(..., description="approve | reject")
    reason: str = Field(default="", description="驳回理由（展示给作者）")


class MemoryResponse(BaseModel):
    entries: list[str] = Field(default_factory=list)
    usage: str = ""


class MemoryUpdateRequest(BaseModel):
    entries: list[str] = Field(default_factory=list)


def _skill_item(doc: dict) -> SkillItem:
    return SkillItem(
        _id=doc["_id"], name=doc.get("name", ""),
        description=doc.get("description", ""), status=doc.get("status", "private"),
        source=doc.get("source", "own"),
        binding_enabled=doc.get("binding_enabled", True),
        stats=doc.get("stats") or {},
        created_at=doc.get("created_at", ""), updated_at=doc.get("updated_at", ""),
    )


@router.get("")
async def list_my_skills(
    current_user: UserResponse = Depends(get_current_user),
) -> list[SkillItem]:
    skills = await UserSkillService.list_skills(current_user.id)
    return [_skill_item(s) for s in skills]


@router.get("/memory")
async def get_my_memory(
    current_user: UserResponse = Depends(get_current_user),
) -> MemoryResponse:
    entries = await UserProfileService.get_entries(current_user.id)
    total = sum(len(e) for e in entries)
    return MemoryResponse(
        entries=entries,
        usage=f"{total}/{MEMORY_TOTAL_CHAR_LIMIT} chars",
    )


@router.put("/memory")
async def update_my_memory(
    body: MemoryUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> MemoryResponse:
    try:
        result = await UserProfileService.set_entries(current_user.id, body.entries)
    except MemoryError as exc:
        raise NotFoundError(code="MEMORY_INVALID", message=exc.message) from exc
    entries = result["entries"]
    return MemoryResponse(
        entries=entries,
        usage=f"{sum(len(e) for e in entries)}/{MEMORY_TOTAL_CHAR_LIMIT} chars",
    )


@router.delete("/memory")
async def clear_my_memory(
    current_user: UserResponse = Depends(get_current_user),
) -> MemoryResponse:
    await UserProfileService.clear(current_user.id)
    return MemoryResponse(entries=[], usage=f"0/{MEMORY_TOTAL_CHAR_LIMIT} chars")


# ---------------------------------------------------------------------------
# 技能广场（§7.3）——必须在 /{skill_id} 之前注册，避免路径参数吞掉字面量
# ---------------------------------------------------------------------------


@router.get("/marketplace")
async def marketplace(
    q: str = Query(default="", description="按名称/描述搜索"),
    current_user: UserResponse = Depends(get_current_user),
) -> list[dict]:
    return await UserSkillService.marketplace(current_user.id, q=q)


@router.get("/sessions/{session_id}/feedback")
async def get_session_feedback(
    session_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> list[dict]:
    """会话内各轮反馈态与使用技能（消息级 👍/👎 渲染数据，§8.2 v2）。

    注册顺序须在 GET /{skill_id} 之前——否则 /sessions/... 会被通配吞掉。
    """
    return await UserSkillService.session_message_feedback(current_user.id, session_id)


@router.post("/messages/vote")
async def vote_message(
    body: MessageVoteRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    """消息级反馈：点赞/点踩模型的一轮回复（§8.2 v2）。

    一轮一票、最新动作为准；本轮 load 过的技能集体获得派生加减分。
    """
    try:
        return await UserSkillService.vote_message(
            current_user.id, body.session_id, body.request_id, body.value,
        )
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc


@router.post("/official", status_code=201)
async def create_official_skill(
    body: SkillCreateRequest,
    creator: UserResponse = Depends(require_any_role("admin", "developer")),
) -> dict:
    """管理员从文本创建官方技能（§7.6 单一创建入口按角色分流）。"""
    try:
        return await UserSkillService.create_official(creator.id, body.name, body.content)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc


@router.get("/admin/review")
async def list_for_review(
    status: str = Query(default="submitted"),
    _: UserResponse = Depends(require_any_role("admin")),
) -> list[dict]:
    return await UserSkillService.list_for_review(status)


@router.post("/admin/review/{skill_id}")
async def review_skill(
    skill_id: str,
    body: ReviewRequest,
    _: UserResponse = Depends(require_any_role("admin")),
) -> dict:
    try:
        return await UserSkillService.review_skill(skill_id, body.action, _.id, body.reason)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc


@router.post("")
async def create_my_skill(
    body: SkillCreateRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> SkillItem:
    """手动创建个人技能（与"会话内让 agent 保存"产物完全等价，§3.1 备选入口）。"""
    try:
        doc = await UserSkillService.create_skill(current_user.id, body.name, body.content)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc
    await UserSkillService.append_log(
        user_id=current_user.id, session_id="", kind="skill",
        action="create(api)", name=doc["name"], ok=True,
    )
    return _skill_item({**doc, "source": "own"})


@router.get("/{skill_id}")
async def get_my_skill(
    skill_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> SkillDetail:
    # 官方技能（tool_*）：任何人可读（广场"查看"预览，§7.6）
    if skill_id.startswith("tool_"):
        from app.services.tool_service import ToolService

        doc = await ToolService.get_tool(skill_id)
        if doc is None or doc.get("source") != "markdown":
            raise NotFoundError(code="USER_SKILL_NOT_FOUND", message=f"Skill {skill_id} 不存在")
        file = await ToolService.get_tool_file_content(skill_id, "SKILL.md")
        content = file.get("content", "") if file else (doc.get("instructions") or "")
        return SkillDetail(
            _id=doc["_id"], name=doc.get("name", ""), description=doc.get("description", ""),
            status="published", source="installed", binding_enabled=True,
            stats=doc.get("stats") or {}, created_at=doc.get("created_at", ""),
            updated_at=doc.get("updated_at", ""), content=content,
        )

    doc = await UserSkillService.get_skill(skill_id)
    if doc is None:
        raise NotFoundError(code="USER_SKILL_NOT_FOUND", message=f"Skill {skill_id} 不存在")
    is_owner = doc.get("owner_user_id") == current_user.id
    is_public = doc.get("status") in ("published", "submitted")
    if not is_owner and not is_public:
        raise NotFoundError(code="USER_SKILL_NOT_FOUND", message=f"Skill {skill_id} 不存在")
    content = await UserSkillService.read_content(doc)
    item = _skill_item({**doc, "source": "own" if is_owner else "installed"})
    return SkillDetail(**item.model_dump(), content=content)


@router.put("/{skill_id}")
async def update_my_skill(
    skill_id: str,
    body: SkillUpdateRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> SkillItem:
    doc = await UserSkillService.get_skill(skill_id)
    if doc is None or doc.get("owner_user_id") != current_user.id:
        raise NotFoundError(code="USER_SKILL_NOT_FOUND", message=f"Skill {skill_id} 不存在")

    if body.content:
        try:
            doc = await UserSkillService.update_content(current_user.id, skill_id, body.content)
        except UserSkillError as exc:
            raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc
        await UserSkillService.append_log(
            user_id=current_user.id, session_id="", kind="skill",
            action="edit(api)", name=doc.get("name", ""), ok=True,
        )
    if body.binding_enabled is not None:
        await UserSkillService._binding_col().update_one(
            {"user_id": current_user.id, "skill_id": skill_id},
            {"$set": {"enabled": body.binding_enabled}},
        )
    refreshed = await UserSkillService.get_skill(skill_id) or doc
    return _skill_item({**refreshed, "source": "own"})


@router.delete("/{skill_id}")
async def delete_my_skill(
    skill_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    try:
        await UserSkillService.delete_skill(current_user.id, skill_id)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_NOT_FOUND", message=exc.message) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# 发布 / 安装 / fork / 投票（§7/§8）
# ---------------------------------------------------------------------------


@router.post("/{skill_id}/submit")
async def submit_for_review(
    skill_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> SkillItem:
    try:
        doc = await UserSkillService.submit_for_review(current_user.id, skill_id)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc
    return _skill_item({**doc, "source": "own"})


@router.post("/{skill_id}/install")
async def install_skill(
    skill_id: str,
    body: InstallRequest,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    # §7.6 管理员无个人技能：安装=进个人启用列表，admin 无此列表——
    # 其使用用户技能的路径是 fork 收录为官方（绑定 Agent 全员生效）
    if (current_user.role or "") in ("admin", "developer"):
        raise NotFoundError(
            code="USER_SKILL_INVALID",
            message=(
                "Admins have no personal skills to install into — "
                "use fork to promote it to an official skill instead."
            ),
        )
    try:
        return await UserSkillService.install(current_user.id, skill_id, body.alias)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc


@router.delete("/{skill_id}/install")
async def uninstall_skill(
    skill_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    await UserSkillService.uninstall(current_user.id, skill_id)
    return {"ok": True}


@router.post("/{skill_id}/fork")
async def fork_skill(
    skill_id: str,
    current_user: UserResponse = Depends(get_current_user),
) -> dict:
    """Fork：普通用户=私有副本；管理员=收录为官方技能（tools 表，§7.6）。"""
    try:
        as_official = (current_user.role or "") in ("admin", "developer")
        return await UserSkillService.fork(current_user.id, skill_id, as_official=as_official)
    except UserSkillError as exc:
        raise NotFoundError(code="USER_SKILL_INVALID", message=exc.message) from exc
