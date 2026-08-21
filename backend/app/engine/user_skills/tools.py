"""用户技能与记忆工具（主 agent 常驻，§3/§4/§6.4）。

- skill_manage：仅当用户显式要求时 create/edit/patch/delete（规范段约束）
- memory：发现持久偏好即静默保存（显式托付才确认）
- 身份门控（§6.2）：IM 渠道（user_id 以 "channel:" 前缀）写入被拒
- 先读后写（§3.3）：patch/edit 前必须在本请求内 load_skill 过——
  loaded_tracker 由 context 装配时创建并与 load 回调共享
"""
from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from loguru import logger
from pydantic import BaseModel, Field

from app.services.user_profile_service import MemoryError, UserProfileService
from app.services.user_skill_service import UserSkillError, UserSkillService

CHANNEL_DENY = (
    "Denied: personal skills and memory are not available on channel sessions."
)


def _result(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def is_channel_user(user_id: str) -> bool:
    return (user_id or "").startswith("channel:")


# ---------------------------------------------------------------------------
# skill_manage
# ---------------------------------------------------------------------------


class _SkillManageArgs(BaseModel):
    action: str = Field(..., description="create | edit | patch | delete")
    name: str = Field(..., description="技能名（用户命名空间内唯一）")
    content: str = Field(default="", description="create/edit 时的完整 SKILL.md 内容（含 frontmatter）")
    old_string: str = Field(default="", description="patch 时的旧文本（须唯一匹配）")
    new_string: str = Field(default="", description="patch 时的新文本")
    replace_all: bool = Field(default=False, description="patch 时替换所有匹配")


def make_skill_manage_tool(
    user_id: str, session_id: str, loaded_tracker: set[str], *, is_admin: bool = False,
) -> StructuredTool:
    """构建绑定当前用户的 skill_manage 工具。

    is_admin（§7.6 管理员无个人技能）：create 直接产出**官方技能**
    （tools 表，绑定 Agent 全员生效）；edit/patch/delete 仍仅作用于
    个人技能——官方技能的修改一律走 Studio（会话输入不可信，保住边界）。
    """

    async def _skill_manage(
        action: str, name: str, content: str = "", old_string: str = "",
        new_string: str = "", replace_all: bool = False,
    ) -> str:
        """创建、编辑、修补、删除当前用户的技能。

        仅在用户于本对话中明确要求保存/更新/删除技能时调用；不要主动调用。

        - create: 新建技能（同名已存在则转为整体更新，幂等）
        - edit:   用 content 整体替换内容（大改）
        - patch:  用 old_string→new_string 局部替换（小改，省 token）
        - delete: 删除

        用户技能仅支持单个 SKILL.md 文件；所有操作仅作用于当前用户自己的技能。
        """
        if is_channel_user(user_id):
            return _result({"success": False, "error": CHANNEL_DENY})

        action = (action or "").strip().lower()
        try:
            if action == "create":
                if is_admin:
                    # 管理员无个人技能：会话内创建直接产出官方（§7.6）
                    doc = await UserSkillService.create_official(user_id, name, content)
                    await UserSkillService.append_log(
                        user_id=user_id, session_id=session_id, kind="skill",
                        action="create-official", name=doc["name"], ok=True,
                    )
                    return _result({
                        "success": True, "action": "create", "name": doc["name"],
                        "skill_id": doc["_id"],
                        "note": (
                            "Created as an OFFICIAL skill (visible to everyone; an admin "
                            "can bind it to agents). It takes effect on the next turn. "
                            "To edit it later, use the Studio skill center — not this tool."
                        ),
                    })
                doc = await UserSkillService.create_skill(user_id, name, content)
                await UserSkillService.append_log(
                    user_id=user_id, session_id=session_id, kind="skill",
                    action="create", name=name, ok=True,
                )
                return _result({
                    "success": True, "action": "create", "name": doc["name"],
                    "skill_id": doc["_id"],
                    "note": "Created. It becomes visible in your skill list on the next turn.",
                })

            if action == "edit":
                if name not in loaded_tracker:
                    return _result({
                        "success": False,
                        "error": f"Refusing edit for skill '{name}': current content has not "
                                 f"been loaded in this request. Call load_skill('{name}') "
                                 f"first, then retry.",
                    })
                skill_doc = await UserSkillService.find_by_name(user_id, name)
                if skill_doc is None:
                    return _result({"success": False, "error": f"Skill '{name}' not found."})
                updated = await UserSkillService.update_content(user_id, skill_doc["_id"], content)
                await UserSkillService.append_log(
                    user_id=user_id, session_id=session_id, kind="skill",
                    action="edit", name=name, ok=True,
                )
                return _result({"success": True, "action": "edit", "name": updated["name"]})

            if action == "patch":
                if name not in loaded_tracker:
                    return _result({
                        "success": False,
                        "error": f"Refusing patch for skill '{name}': current content has not "
                                 f"been loaded in this request. Call load_skill('{name}') "
                                 f"first, then retry.",
                    })
                result = await UserSkillService.patch_content(
                    user_id, name, old_string, new_string, replace_all,
                )
                await UserSkillService.append_log(
                    user_id=user_id, session_id=session_id, kind="skill",
                    action="patch", name=name, ok=True,
                    detail="skipped" if result.get("skipped") else "",
                )
                payload: dict[str, Any] = {"success": True, "action": "patch", "name": name}
                if result.get("skipped"):
                    payload["skipped"] = True
                    payload["note"] = result.get("reason", "")
                return _result(payload)

            if action == "delete":
                skill_doc = await UserSkillService.find_by_name(user_id, name)
                if skill_doc is None:
                    return _result({"success": True, "action": "delete", "name": name,
                                    "note": "already absent (idempotent)"})
                await UserSkillService.delete_skill(user_id, skill_doc["_id"])
                await UserSkillService.append_log(
                    user_id=user_id, session_id=session_id, kind="skill",
                    action="delete", name=name, ok=True,
                )
                return _result({"success": True, "action": "delete", "name": name})

            return _result({"success": False, "error": f"Unknown action '{action}'."})

        except UserSkillError as exc:
            logger.warning("skill_manage_failed", user_id=user_id, action=action, error=exc.message)
            await UserSkillService.append_log(
                user_id=user_id, session_id=session_id, kind="skill",
                action=action, name=name, ok=False, detail=exc.message,
            )
            payload = {"success": False, "error": exc.message}
            if exc.current_skills:
                payload["current_skills"] = [
                    {"name": s["name"], "load_count": s.get("stats", {}).get("load_count", 0)}
                    for s in exc.current_skills
                ]
            return _result(payload)
        except Exception as exc:  # noqa: BLE001 — 工具层兜底，不让异常打断循环
            logger.exception("skill_manage_error")
            return _result({"success": False, "error": f"Internal error: {exc}"})

    return StructuredTool.from_function(
        _skill_manage,
        name="skill_manage",
        description=(
            "创建、编辑、修补、删除当前用户的个人技能（SKILL.md）。"
            "仅在用户明确要求保存/更新/删除技能时调用。"
            "patch/edit 前必须先 load_skill(name) 读当前内容。"
        ),
        args_schema=_SkillManageArgs,
        coroutine=_skill_manage,
    )


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


class _MemoryArgs(BaseModel):
    action: str = Field(default="", description="add | replace | remove（单条模式）")
    content: str = Field(default="", description="add/replace 时的内容（≤200 字符）")
    old_text: str = Field(default="", description="replace/remove 时定位旧文本（精确匹配）")
    operations: list[dict] | None = Field(
        default=None,
        description="批量原子操作：[{action, content?, old_text?}, ...]，总量统一检查",
    )


def make_memory_tool(user_id: str, session_id: str) -> StructuredTool:
    """构建绑定当前用户的 memory 工具。"""

    async def _memory(
        action: str = "", content: str = "", old_text: str = "",
        operations: list[dict] | None = None,
    ) -> str:
        """管理当前用户的记忆条目（仅用户级）。

        发现持久用户偏好（含显式"记住…"请求）时调用；静默保存，无需宣告。
        单条 ≤200 字符、总量 ≤1375 字符；超限返回现有条目，合并/删旧后重试。
        """
        if is_channel_user(user_id):
            return _result({"success": False, "error": CHANNEL_DENY})

        try:
            result = await UserProfileService.apply(
                user_id,
                action=action, content=content,
                old_text=old_text, operations=operations,
            )
            await UserSkillService.append_log(
                user_id=user_id, session_id=session_id, kind="memory",
                action=action or "batch", ok=True,
            )
            return _result({"success": True, **result})
        except MemoryError as exc:
            logger.info("memory_op_rejected", user_id=user_id, error=exc.message)
            payload = {"success": False, "error": exc.message}
            if exc.entries is not None:
                payload["current_entries"] = exc.entries
                payload["usage"] = (
                    f"{UserProfileService._total_chars(exc.entries)}/"
                    f"{1375} chars"
                )
            return _result(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("memory_tool_error")
            return _result({"success": False, "error": f"Internal error: {exc}"})

    return StructuredTool.from_function(
        _memory,
        name="memory",
        description=(
            "管理当前用户的记忆条目（add/replace/remove 或 operations 批量）。"
            "记录持久用户偏好；条目紧凑高信噪比；满了先合并再重试。"
        ),
        args_schema=_MemoryArgs,
        coroutine=_memory,
    )


def make_user_skill_tools(
    user_id: str, session_id: str, loaded_tracker: set[str], *, is_admin: bool = False,
) -> list[StructuredTool]:
    """装配 skill_manage + memory 两个常驻工具（§2）。"""
    return [
        make_skill_manage_tool(user_id, session_id, loaded_tracker, is_admin=is_admin),
        make_memory_tool(user_id, session_id),
    ]


__all__ = [
    "make_skill_manage_tool",
    "make_memory_tool",
    "make_user_skill_tools",
    "is_channel_user",
]
