"""UserSkillService — 用户技能：磁盘内容 + DB 元数据双写（§5.1/§5.2）。

磁盘布局（与 agent 技能同构，用户命名空间隔离）：

    {SKILLS_CONTAINER_DIR}/
      ├─ {skill_name}/SKILL.md              # agent 技能（全局池，不动）
      └─ users/{user_id}/{name}/SKILL.md    # 用户技能（个人目录树）

约定：
- 磁盘是内容事实源；user_skills 集合只存元数据（name/description/status/stats）
- (owner_user_id, name) 唯一 = 目录名 + DB 唯一索引双约束（§7.4）
- 首期仅 SKILL.md 单文件；多文件扩展时同一目录下加（§5.1）
"""
from __future__ import annotations

import contextlib
import re
import shutil
from pathlib import Path

from loguru import logger

from app.db.mongodb import get_database
from app.models.base import generate_id, utc_now
from app.models.user_skill import MAX_USER_SKILLS, SKILL_NAME_PATTERN

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_NAME_RE = re.compile(SKILL_NAME_PATTERN)


class UserSkillError(Exception):
    """用户技能操作失败（工具层转成 JSON 错误结果，不 raise 到 LLM 循环）。"""

    def __init__(self, message: str, *, current_skills: list[dict] | None = None):
        super().__init__(message)
        self.message = message
        self.current_skills = current_skills


class UserSkillService:
    """用户技能存储与查询。全部静态方法，与 SessionService 同风格。"""

    COLLECTION = "user_skills"
    BINDING_COLLECTION = "user_skill_bindings"
    LOG_COLLECTION = "skill_logs"
    MESSAGE_FEEDBACK_COLLECTION = "message_feedback"  # 消息级反馈（§8.2 v2 事实源）

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    @staticmethod
    def _homes_root() -> Path:
        """用户资产根（个人技能等持久数据）：``{HOMES_CONTAINER_DIR}/{uid}/…``。

        派生：未配置时取 SKILLS_CONTAINER_DIR 同级的 ``homes/``。
        与 workspace 同级同隔离（按 uid），但**持久**——不参与 workspace
        的定期清理；沙箱挂载时按用户只读挂入 ``{uid}/skills``，无跨用户泄漏。
        """
        from app.core.config import settings

        if settings.HOMES_CONTAINER_DIR:
            return Path(settings.HOMES_CONTAINER_DIR).expanduser()
        return Path(settings.SKILLS_CONTAINER_DIR).expanduser().parent / "homes"

    @staticmethod
    def _user_skills_dir(user_id: str) -> Path:
        """单个用户的技能目录 ``{homes}/{uid}/skills/``，含惰性迁移。

        历史布局迁移（逐用户、幂等）：
        - v1 ``{skills}/users/{uid}``        → v2 独立根 ``{parent}/user_skills/{uid}``
        - 统一迁到 v3 ``{homes}/{uid}/skills``
        """
        from app.core.config import settings

        target = UserSkillService._homes_root() / user_id / "skills"
        if target.exists():
            return target
        skills_root = Path(settings.SKILLS_CONTAINER_DIR).expanduser()
        for legacy in (
            skills_root / "users" / user_id,                    # v1 池内布局
            skills_root.parent / "user_skills" / user_id,       # v2 独立根
        ):
            if legacy.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(target))
                # 旧父目录空了就清壳（users/ 或 user_skills/ 整根移完后）
                # 非空（其他用户未迁）rmdir 抛 OSError——留给下一次
                with contextlib.suppress(OSError):
                    legacy.parent.rmdir()
                return target
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def skill_file(user_id: str, name: str) -> Path:
        return UserSkillService._user_skills_dir(user_id) / name / "SKILL.md"

    # ------------------------------------------------------------------
    # 创建 / 编辑 / 删除
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_name(name: str) -> str:
        if not _NAME_RE.match(name or ""):
            raise UserSkillError(
                f"Invalid skill name '{name}'. Use letters/digits/dash/underscore, "
                f"max 64 chars, starting with a letter or digit."
            )
        return name

    @staticmethod
    def _extract_description(content: str) -> str:
        m = _FRONTMATTER_RE.match(content or "")
        if not m:
            return ""
        for line in m.group(1).splitlines():
            if line.strip().startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
        return ""

    @staticmethod
    async def _check_soft_limit(user_id: str) -> None:
        count = await UserSkillService._binding_col().count_documents({"user_id": user_id})
        if count >= MAX_USER_SKILLS:
            current = await UserSkillService.list_skills(user_id)
            raise UserSkillError(
                f"Skill limit reached ({count}/{MAX_USER_SKILLS}). "
                f"Delete or consolidate existing skills before creating new ones.",
                current_skills=current,
            )

    @staticmethod
    async def create_skill(user_id: str, name: str, content: str) -> dict:
        """新建用户技能；同名且属于自己 → 转 edit（幂等，§3.3）。"""
        name = UserSkillService._validate_name(name)
        existing = await UserSkillService.find_by_name(user_id, name)
        if existing:
            return await UserSkillService._rewrite(existing, content)

        await UserSkillService._check_soft_limit(user_id)

        skill_id = generate_id("usk")
        now = utc_now().isoformat()
        doc = {
            "_id": skill_id,
            "owner_user_id": user_id,
            "name": name,
            "description": UserSkillService._extract_description(content),
            "status": "private",
            "derived_from": None,
            "stats": {"load_count": 0, "up": 0, "down": 0},
            "created_at": now,
            "updated_at": now,
            "published_at": None,
            "approved_by": None,
        }

        skill_file = UserSkillService.skill_file(user_id, name)
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")

        await UserSkillService._col().insert_one(doc)
        await UserSkillService._ensure_binding(user_id, skill_id, source="own")
        logger.info("user_skill_created", user_id=user_id, name=name, skill_id=skill_id)
        return doc

    @staticmethod
    async def update_content(user_id: str, skill_id: str, content: str) -> dict:
        """整体替换内容（edit，大改）——owner 校验在外层 API/工具完成。"""
        doc = await UserSkillService.get_skill(skill_id)
        if doc is None:
            raise UserSkillError(f"Skill '{skill_id}' not found.")
        return await UserSkillService._rewrite(doc, content)

    @staticmethod
    async def _rewrite(doc: dict, content: str) -> dict:
        skill_file = UserSkillService.skill_file(doc["owner_user_id"], doc["name"])
        skill_file.parent.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(content, encoding="utf-8")
        await UserSkillService._col().update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "description": UserSkillService._extract_description(content),
                "updated_at": utc_now().isoformat(),
            }},
        )
        doc["description"] = UserSkillService._extract_description(content)
        logger.info("user_skill_rewritten", skill_id=doc["_id"], name=doc["name"])
        return doc

    @staticmethod
    async def patch_content(
        user_id: str, name: str,
        old_string: str, new_string: str, replace_all: bool = False,
    ) -> dict:
        """局部替换（patch）：old_string 须唯一匹配（§3.3）。"""
        doc = await UserSkillService.find_by_name(user_id, name)
        if doc is None:
            raise UserSkillError(f"Skill '{name}' not found.")

        skill_file = UserSkillService.skill_file(user_id, name)
        if not skill_file.exists():
            raise UserSkillError(f"Skill file for '{name}' missing on disk.")
        content = skill_file.read_text(encoding="utf-8", errors="replace")

        count = content.count(old_string)
        if count == 0:
            # 幂等：已打过补丁或内容已变（§3.3）
            return {"ok": True, "skipped": True, "reason": "already patched or content changed", "skill": doc}
        if count > 1 and not replace_all:
            raise UserSkillError(
                f"old_string matches {count} locations in '{name}'. "
                f"Provide more surrounding context to make it unique, or set replace_all=true."
            )
        new_content = content.replace(old_string, new_string) if (count > 1 or replace_all) \
            else content.replace(old_string, new_string, 1)
        return {
            "ok": True,
            "skipped": False,
            "skill": await UserSkillService._rewrite(doc, new_content),
        }

    @staticmethod
    async def delete_skill(user_id: str, skill_id: str) -> None:
        doc = await UserSkillService.get_skill(skill_id)
        if doc is None or doc.get("owner_user_id") != user_id:
            raise UserSkillError(f"Skill '{skill_id}' not found or not yours.")
        skill_dir = UserSkillService.skill_file(user_id, doc["name"]).parent
        if skill_dir.exists():
            import shutil

            shutil.rmtree(skill_dir, ignore_errors=True)
        await UserSkillService._col().delete_one({"_id": skill_id})
        await UserSkillService._binding_col().delete_many({"skill_id": skill_id})
        logger.info("user_skill_deleted", user_id=user_id, skill_id=skill_id, name=doc["name"])

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    async def find_by_name(user_id: str, name: str) -> dict | None:
        return await UserSkillService._col().find_one({
            "owner_user_id": user_id, "name": name,
        })

    @staticmethod
    async def get_skill(skill_id: str) -> dict | None:
        return await UserSkillService._col().find_one({"_id": skill_id})

    @staticmethod
    async def read_content(doc: dict) -> str:
        skill_file = UserSkillService.skill_file(doc["owner_user_id"], doc["name"])
        if not skill_file.exists():
            return ""
        return skill_file.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    async def list_skills(user_id: str) -> list[dict]:
        """我名下（own）+ 安装（installed）的技能，含 stats 与别名。

        installed 绑定可指向两类源（§7.6 逻辑单市·物理双库）：
        - usk_*：他人发布的用户技能（内容在 owner 的个人目录）
        - tool_*：官方技能（内容在全局池，安装=随身携带到未绑定的 agent）
        每项附 content_path（磁盘上 SKILL.md 的真实位置，供 load 解析）。
        """
        bindings = await UserSkillService._binding_col().find(
            {"user_id": user_id}
        ).to_list(MAX_USER_SKILLS * 2)
        if not bindings:
            return []
        result: list[dict] = []

        # usk_* 绑定 → user_skills
        usk_ids = [b["skill_id"] for b in bindings if b["skill_id"].startswith("usk_")]
        if usk_ids:
            docs = await UserSkillService._col().find({"_id": {"$in": usk_ids}}).to_list(len(usk_ids))
            by_id = {d["_id"]: d for d in docs}
            for b in bindings:
                d = by_id.get(b["skill_id"])
                if d:
                    d = dict(d)
                    d["source"] = b.get("source", "own")
                    d["binding_enabled"] = b.get("enabled", True)
                    d["alias"] = b.get("alias", "") or d["name"]
                    # ⚠ 内容路径按 owner 解析（安装的技能不在安装者目录——修复必错 bug）
                    d["content_path"] = UserSkillService.skill_file(d["owner_user_id"], d["name"])
                    result.append(d)

        # tool_* 绑定 → 官方技能（tools 表，全局池路径）
        tool_ids = [b["skill_id"] for b in bindings if b["skill_id"].startswith("tool_")]
        if tool_ids:
            from app.services.tool_service import ToolService

            tool_docs = await ToolService.get_tools_by_ids(tool_ids)
            tool_by_id = {d["_id"]: d for d in tool_docs}
            for b in bindings:
                d = tool_by_id.get(b["skill_id"])
                if d:
                    from app.core.config import settings

                    d = dict(d)
                    d["source"] = "installed"
                    d["binding_enabled"] = b.get("enabled", True)
                    d["alias"] = b.get("alias", "") or d["name"]
                    d["content_path"] = (
                        Path(settings.SKILLS_CONTAINER_DIR).expanduser() / d["name"] / "SKILL.md"
                    )
                    result.append(d)
        return result

    @staticmethod
    async def enabled_skills(user_id: str) -> list[dict]:
        """注入与 load 白名单的数据源：启用列表中 enabled=True 的（§5.2）。

        每项含 effective_name = alias or name（load_skill 键）与
        content_path（磁盘真实路径——own/installed-usk 在 owner 目录，
        installed-tool 在全局池，§7.6）。
        """
        skills = await UserSkillService.list_skills(user_id)
        for s in skills:
            s["effective_name"] = s.get("alias") or s["name"]
        return [s for s in skills if s.get("binding_enabled", True)]

    # ------------------------------------------------------------------
    # Binding / 统计 / 审计
    # ------------------------------------------------------------------

    @staticmethod
    async def _ensure_binding(user_id: str, skill_id: str, *, source: str = "own") -> None:
        await UserSkillService._binding_col().update_one(
            {"user_id": user_id, "skill_id": skill_id},
            {"$setOnInsert": {
                "_id": generate_id("bnd"),
                "user_id": user_id,
                "skill_id": skill_id,
                "source": source,
                "enabled": True,
                "created_at": utc_now().isoformat(),
            }},
            upsert=True,
        )

    @staticmethod
    async def record_load(skill_id: str, name: str, user_id: str, session_id: str,
                          request_id: str = "") -> None:
        """load_skill 命中用户技能时：$inc load_count + 审计（计票资格依据，§8.2）。

        request_id 是消息级反馈的轮次键（§8.2 消息点赞按轮派发技能分）。
        """
        await UserSkillService._col().update_one(
            {"_id": skill_id},
            {"$inc": {"stats.load_count": 1}},
        )
        await UserSkillService.append_log(
            user_id=user_id, session_id=session_id, kind="load",
            action="load", name=name, ok=True, skill_id=skill_id,
            request_id=request_id,
        )

    @staticmethod
    async def record_official_load(tool_id: str, name: str, user_id: str, session_id: str,
                                   request_id: str = "") -> None:
        """load_skill 命中官方技能时：$inc tools.stats.load_count + 审计（§7.6 积分统一）。"""
        from app.services.tool_service import ToolService

        await ToolService._collection().update_one(
            {"_id": tool_id},
            # $inc 对缺失的嵌套字段会自动创建（存量文档无 stats 也可用）
            {"$inc": {"stats.load_count": 1}},
        )
        await UserSkillService.append_log(
            user_id=user_id, session_id=session_id, kind="load",
            action="load", name=name, ok=True, skill_id=tool_id,
            request_id=request_id,
        )

    @staticmethod
    async def append_log(
        *, user_id: str, session_id: str, kind: str, action: str,
        name: str = "", ok: bool = True, detail: str = "", skill_id: str = "",
        request_id: str = "",
    ) -> None:
        doc = {
            "_id": generate_id("slog"),
            "user_id": user_id,
            "session_id": session_id,
            "kind": kind,
            "action": action,
            "name": name,
            "skill_id": skill_id,  # load 记录的稳定键（name 可被 rename/alias，§7.4）
            "ok": ok,
            "detail": detail[:500],
            "created_at": utc_now().isoformat(),
        }
        if request_id:
            doc["request_id"] = request_id  # 消息级反馈的轮次键（§8.2）
        await UserSkillService._log_col().insert_one(doc)

    # ------------------------------------------------------------------
    # collections
    # ------------------------------------------------------------------

    @staticmethod
    def _col():
        return get_database()[UserSkillService.COLLECTION]

    @staticmethod
    def _binding_col():
        return get_database()[UserSkillService.BINDING_COLLECTION]

    @staticmethod
    def _log_col():
        return get_database()[UserSkillService.LOG_COLLECTION]

    @staticmethod
    def _msg_feedback_col():
        return get_database()[UserSkillService.MESSAGE_FEEDBACK_COLLECTION]

    # ------------------------------------------------------------------
    # 市场：发布 / 审核 / 广场 / 安装 / 投票 / fork（§7/§8）
    # ------------------------------------------------------------------

    @staticmethod
    async def submit_for_review(user_id: str, skill_id: str) -> dict:
        doc = await UserSkillService.get_skill(skill_id)
        if doc is None or doc.get("owner_user_id") != user_id:
            raise UserSkillError(f"Skill '{skill_id}' not found or not yours.")
        if doc.get("status") not in ("private", "hidden"):
            raise UserSkillError(
                f"Skill status is '{doc.get('status')}' — only private skills can be submitted."
            )
        await UserSkillService._col().update_one(
            {"_id": skill_id}, {"$set": {"status": "submitted", "updated_at": utc_now().isoformat()}}
        )
        doc["status"] = "submitted"
        return doc

    @staticmethod
    async def review_skill(skill_id: str, action: str, reviewer: str, reason: str = "") -> dict:
        """管理员审核：approve → published；reject → 回 private（附理由，§8.1）。"""
        doc = await UserSkillService.get_skill(skill_id)
        if doc is None:
            raise UserSkillError(f"Skill '{skill_id}' not found.")
        now = utc_now().isoformat()
        if action == "approve":
            await UserSkillService._col().update_one(
                {"_id": skill_id},
                {"$set": {"status": "published", "published_at": now,
                          "approved_by": reviewer, "updated_at": now, "review_note": ""}},
            )
            doc["status"] = "published"
        elif action == "reject":
            await UserSkillService._col().update_one(
                {"_id": skill_id},
                {"$set": {"status": "private", "updated_at": now, "review_note": reason[:500]}},
            )
            doc["status"] = "private"
        else:
            raise UserSkillError(f"Unknown review action '{action}'.")
        await UserSkillService.append_log(
            user_id=doc.get("owner_user_id", ""), session_id="", kind="skill",
            action=f"review-{action}", name=doc.get("name", ""), ok=True, detail=reason,
        )
        return doc

    @staticmethod
    async def list_for_review(status: str = "submitted") -> list[dict]:
        cursor = UserSkillService._col().find({"status": status}).sort("updated_at", 1)
        docs = await cursor.to_list(100)
        for d in docs:
            d["id"] = d["_id"]  # 响应统一带 id
        return docs

    @staticmethod
    async def marketplace(
        viewer_id: str, q: str = "", limit: int = 50,
    ) -> list[dict]:
        """逻辑单市：官方技能（tools 表）+ 已发布用户技能合并返回（§7.6）。

        官方在前（策展稳定），组内按积分 desc → load_count desc；
        每项含 kind（official/user）、作者显示名、我的安装态与投票态。
        """
        query: dict = {"status": "published"}
        if q:
            query["$or"] = [
                {"name": {"$regex": q, "$options": "i"}},
                {"description": {"$regex": q, "$options": "i"}},
            ]
        cursor = (
            UserSkillService._col().find(query)
            .sort([("stats.up", -1), ("stats.load_count", -1)])
            .limit(limit)
        )
        user_docs = await cursor.to_list(limit)

        from app.services.tool_service import ToolService

        tool_query: dict = {"source": "markdown"}
        if q:
            tool_query["$or"] = [
                {"name": {"$regex": q, "$options": "i"}},
                {"description": {"$regex": q, "$options": "i"}},
            ]
        tool_docs = await ToolService._collection().find(tool_query).to_list(limit)

        # 作者显示名（官方 created_by + 用户 owner），一次批量查
        author_ids = list({d["owner_user_id"] for d in user_docs if d.get("owner_user_id")})
        author_ids += list({d["created_by"] for d in tool_docs if d.get("created_by")})
        name_by_id: dict[str, str] = {}
        if author_ids:
            users = await get_database()["users"].find(
                {"_id": {"$in": author_ids}}, {"username": 1, "nickname": 1}
            ).to_list(len(author_ids))
            name_by_id = {
                u["_id"]: (u.get("nickname") or u.get("username") or u["_id"]) for u in users
            }

        my_bindings = {
            b["skill_id"]: b
            for b in await UserSkillService._binding_col().find({"user_id": viewer_id}).to_list(100)
        } if viewer_id else {}

        def _decorate(d: dict, kind: str) -> dict:
            d = dict(d)
            d["kind"] = kind
            d["id"] = d["_id"]  # 响应统一带 id（前端不再依赖 _id）
            owner = d.get("owner_user_id") if kind == "user" else d.get("created_by", "")
            if kind == "user":
                d["author_name"] = name_by_id.get(owner, owner or "未知")
            else:
                d["author_name"] = name_by_id.get(owner, "官方") if owner else "官方"
            b = my_bindings.get(d["_id"])
            d["installed"] = bool(b)
            d["is_own"] = d.get("owner_user_id") == viewer_id if kind == "user" else False
            stats = d.get("stats") or {}
            d["score"] = int(stats.get("up", 0) or 0) - int(stats.get("down", 0) or 0)
            return d

        officials = sorted(
            [_decorate(d, "official") for d in tool_docs],
            key=lambda x: (-x["score"], -int((x.get("stats") or {}).get("load_count", 0) or 0)),
        )
        users_list = [_decorate(d, "user") for d in user_docs]
        return officials + users_list

    @staticmethod
    async def install(user_id: str, skill_id: str, alias: str = "") -> dict:
        """安装已发布/官方技能（引用不复制，§7.3/§7.6）；启用列表内重名需 alias。"""
        is_official = skill_id.startswith("tool_")
        if is_official:
            from app.services.tool_service import ToolService

            doc = await ToolService.get_tool(skill_id)
            if doc is None or doc.get("source") != "markdown":
                raise UserSkillError(f"Official skill '{skill_id}' not found.")
        else:
            doc = await UserSkillService.get_skill(skill_id)
            if doc is None or doc.get("status") != "published":
                raise UserSkillError(f"Skill '{skill_id}' is not published.")
            if doc.get("owner_user_id") == user_id:
                raise UserSkillError("This is your own skill — nothing to install.")

        existing = await UserSkillService._binding_col().find_one(
            {"user_id": user_id, "skill_id": skill_id}
        )
        if existing:
            raise UserSkillError("Already installed.")

        await UserSkillService._check_soft_limit(user_id)

        effective = (alias or "").strip() or doc["name"]
        if effective != doc["name"]:
            UserSkillService._validate_name(effective)
        # 启用列表内 effective name 唯一（§7.4 三层重名规则）
        for s in await UserSkillService.enabled_skills(user_id):
            if s["effective_name"] == effective:
                raise UserSkillError(
                    f"Name conflict: you already have a skill named '{effective}'. "
                    f"Provide an alias to install under a different name."
                )

        await UserSkillService._binding_col().insert_one({
            "_id": generate_id("bnd"),
            "user_id": user_id,
            "skill_id": skill_id,
            "source": "installed",
            "alias": effective if effective != doc["name"] else "",
            "enabled": True,
            "created_at": utc_now().isoformat(),
        })
        return {"ok": True, "effective_name": effective}

    @staticmethod
    async def uninstall(user_id: str, skill_id: str) -> None:
        await UserSkillService._binding_col().delete_one(
            {"user_id": user_id, "skill_id": skill_id, "source": "installed"}
        )

    @staticmethod
    async def fork(user_id: str, skill_id: str, *, as_official: bool = False) -> dict:
        """复制技能为己用（derived_from 溯源，§7.4/§7.6）。

        - 普通用户（as_official=False）：副本进个人空间，私有、积分归零
        - 管理员（as_official=True）：**收录为官方技能**——副本进 tools 表
          （绑定 Agent 全员生效），created_by=操作者；源必须是用户技能
          （fork 官方无意义，管理员可直接编辑官方）
        """
        is_official_source = skill_id.startswith("tool_")
        if as_official:
            if is_official_source:
                raise UserSkillError(
                    "Official skills can be edited directly — forking them as official is pointless."
                )
            return await UserSkillService._fork_as_official(user_id, skill_id)

        doc = await UserSkillService.get_skill(skill_id)
        if doc is not None:
            if doc.get("owner_user_id") == user_id:
                raise UserSkillError("This is already your own skill.")
            content = await UserSkillService.read_content(doc)
            if not content:
                raise UserSkillError("Source skill has no content on disk.")
            base_name = doc["name"]
            description = doc.get("description", "")
        elif is_official_source:
            # 官方技能（agent 级，全局池）——§7.4 任何 agent 技能可 fork
            from app.services.tool_service import ToolService

            file = await ToolService.get_tool_file_content(skill_id, "SKILL.md")
            if file is None or not file.get("content"):
                raise UserSkillError(f"Official skill '{skill_id}' not found or has no SKILL.md.")
            content = file["content"]
            tool_doc = await ToolService.get_tool(skill_id)
            base_name = (tool_doc or {}).get("name", "") or "official_skill"
            description = (tool_doc or {}).get("description", "") or ""
        else:
            raise UserSkillError(f"Skill '{skill_id}' not found.")

        # 命名：原名可用则用，否则自动 -fork 后缀（§7.4 个人空间唯一）
        # 注意不能用 next(异步生成器)——await 在生成器表达式里产生 async_generator，
        # next() 无法消费（测试暴露的潜在 bug，此处用普通循环）。
        candidates = [base_name] + [f"{base_name}-fork{i}" for i in range(1, 50)]
        name: str | None = None
        for candidate in candidates:
            if not await UserSkillService.find_by_name(user_id, candidate):
                name = candidate
                break
        if name is None:
            raise UserSkillError("Could not derive a free name; please rename after forking.")

        created = await UserSkillService.create_skill(user_id, name, content)
        await UserSkillService._col().update_one(
            {"_id": created["_id"]},
            {"$set": {
                "derived_from": skill_id,
                "derived_from_name": base_name,  # 溯源快照（fork 时的源名，§7.6）
                "description": description or created.get("description", ""),
            }},
        )
        created["derived_from"] = skill_id
        created["derived_from_name"] = base_name
        created["id"] = created["_id"]  # 响应统一带 id
        created["forked_as"] = name
        return created

    @staticmethod
    async def create_official(user_id: str, name: str, content: str) -> dict:
        """管理员从文本直接创建官方技能（§7.6 单一创建入口按角色分流）。"""
        from app.engine.tool.skill_fs import materialize_skill
        from app.engine.tool.skill_parser import SkillFileEntry
        from app.services.tool_service import ToolService

        UserSkillService._validate_name(name)
        if await ToolService.find_by_name(name):
            raise UserSkillError(f"Official skill '{name}' already exists — pick another name.")

        materialize_skill(name, [SkillFileEntry(
            path="SKILL.md", content=content, size=len(content.encode("utf-8")),
        )])
        now_iso = utc_now().isoformat()
        doc = {
            "_id": generate_id("tool"),
            "name": name,
            "description": UserSkillService._extract_description(content),
            "input_schema": {},
            "output_schema": {},
            "instructions": content,
            "source": "markdown",
            "source_file": "",
            "version": 1,
            "tags": [],
            "created_by": user_id,
            "stats": {"load_count": 0, "up": 0, "down": 0},
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        doc = await ToolService._insert_skill_doc(name, doc)
        await UserSkillService.append_log(
            user_id=user_id, session_id="", kind="skill",
            action="create-official(api)", name=name, ok=True,
        )
        logger.info("official_skill_created", name=name, by=user_id)
        return doc

    @staticmethod
    async def _fork_as_official(user_id: str, skill_id: str) -> dict:
        """管理员收录：用户技能 → 官方技能（tools 表，§7.6 管理员 fork 语义）。"""
        from app.engine.tool.skill_fs import materialize_skill
        from app.engine.tool.skill_parser import SkillFileEntry
        from app.services.tool_service import ToolService

        doc = await UserSkillService.get_skill(skill_id)
        if doc is None or doc.get("status") != "published":
            raise UserSkillError(f"Skill '{skill_id}' is not published — only published skills can be promoted.")
        content = await UserSkillService.read_content(doc)
        if not content:
            raise UserSkillError("Source skill has no content on disk.")

        base_name = doc["name"]
        # 官方名空间全局唯一：原名可用则用，否则自动 -official 后缀
        candidates = [base_name] + [f"{base_name}-official{i}" for i in range(1, 50)]
        name: str | None = None
        for candidate in candidates:
            if not await ToolService.find_by_name(candidate):
                name = candidate
                break
        if name is None:
            raise UserSkillError("Could not derive a free official name.")

        materialize_skill(name, [SkillFileEntry(
            path="SKILL.md", content=content, size=len(content.encode("utf-8")),
        )])
        now_iso = utc_now().isoformat()
        official_doc = {
            "_id": generate_id("tool"),
            "name": name,
            "description": doc.get("description", ""),
            "input_schema": {},
            "output_schema": {},
            "instructions": content,
            "source": "markdown",
            "source_file": "",
            "version": 1,
            "tags": [],
            "created_by": user_id,
            "stats": {"load_count": 0, "up": 0, "down": 0},
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        official_doc = await ToolService._insert_skill_doc(name, official_doc)
        await UserSkillService.append_log(
            user_id=user_id, session_id="", kind="skill",
            action="promote(api)", name=name, ok=True, detail=f"from {skill_id}",
        )
        logger.info("user_skill_promoted_official", skill_id=skill_id, name=name, by=user_id)
        official_doc["id"] = official_doc["_id"]  # 响应统一带 id
        official_doc["kind"] = "official"
        official_doc["promoted_from"] = skill_id
        return official_doc

    # ------------------------------------------------------------------
    # 消息级反馈（§8.2 v2）：点赞模型回复 → 本轮 load 的技能集体派生加减分
    # ------------------------------------------------------------------

    @staticmethod
    async def _round_skill_ids(user_id: str, session_id: str, request_id: str) -> list[str]:
        """本轮（request_id 精确匹配）load 过的技能 id 集合。

        旧 load 记录无 request_id——不支持按轮反馈（新功能从新数据生效）。
        """
        return [
            sid for sid in await UserSkillService._log_col().distinct(
                "skill_id",
                {"user_id": user_id, "session_id": session_id,
                 "request_id": request_id, "kind": "load", "ok": True,
                 "skill_id": {"$ne": ""}},
            )
            if sid
        ]

    @staticmethod
    async def _apply_skill_delta(skill_id: str, value: int, *, rollback: bool = False) -> None:
        """按投票方向给技能加减分；rollback=True 撤销该方向（赞的撤销是 up-1，
        不是 down+1——改票回滚必须精确抵消原发放）。

        用户技能 score ≤ -3 自动 hidden（§8.2）；官方不适用（下架是管理员决策）。
        """
        if value not in (1, -1):
            return
        # 字段由投票方向决定，步长由是否撤销决定：
        # apply 👍→up+1 / 👎→down+1；rollback 👍→up-1 / 👎→down-1
        field = "stats.up" if value == 1 else "stats.down"
        step = -1 if rollback else 1
        update: dict = {"$inc": {field: step}}
        if skill_id.startswith("tool_"):
            from app.services.tool_service import ToolService

            await ToolService._collection().update_one({"_id": skill_id}, update)
            return
        await UserSkillService._col().update_one({"_id": skill_id}, update)
        # $inc 后读最新值检查自动隐藏（读-写两跳，低频操作可接受）
        doc = await UserSkillService._col().find_one({"_id": skill_id}, {"stats": 1, "status": 1})
        stats = (doc or {}).get("stats") or {}
        score = int(stats.get("up", 0) or 0) - int(stats.get("down", 0) or 0)
        if score <= -3 and (doc or {}).get("status") != "hidden":
            await UserSkillService._col().update_one(
                {"_id": skill_id}, {"$set": {"status": "hidden"}}
            )

    @staticmethod
    async def vote_message(user_id: str, session_id: str, request_id: str, value: int) -> dict:
        """消息级反馈：用户点赞/点踩模型的某轮回复（§8.2 v2）。

        - 一轮一票、最新动作为准：改票按旧快照精确回滚再发放新值
        - 反馈对象是「回复」，技能加分是派生行为——message_feedback 是
          事实源（skill_ids 快照），stats.up/down 为派生缓存；后续其它
          反馈操作（agent 满意度/奖励等）可在同一记录上扩展
        - 官方（tool_*）与用户（usk_*）技能同规则（§7.6 逻辑单市）
        """
        if value not in (1, -1):
            raise UserSkillError("Vote value must be 1 (up) or -1 (down).")

        col = UserSkillService._msg_feedback_col()
        key = {"user_id": user_id, "session_id": session_id, "request_id": request_id}
        existing = await col.find_one(key)
        old_value = int((existing or {}).get("value", 0) or 0)
        if old_value == value:
            return {"ok": True, "changed": False}

        # ⚠️ 时序窗口：load 埋点是 fire-and-forget（context.py create_task），
        # 极端情况（load 恰在流末尾 + 用户秒赞）快照可能不全——快照固定语义
        # 不回补；漂移由 rebuild_stats_from_feedback() 兜底重算。
        skill_ids = await UserSkillService._round_skill_ids(user_id, session_id, request_id)

        # 回滚旧快照 → 发放新快照（同一轮技能集合不变，快照固定）
        if existing and old_value:
            for sid in existing.get("skill_ids", []):
                await UserSkillService._apply_skill_delta(sid, old_value, rollback=True)
        for sid in skill_ids:
            await UserSkillService._apply_skill_delta(sid, value)

        now = utc_now().isoformat()
        await col.update_one(
            key,
            {"$set": {"value": value, "skill_ids": skill_ids, "updated_at": now},
             "$setOnInsert": {"_id": generate_id("mfb"), "created_at": now}},
            upsert=True,
        )
        # 反馈审计（L-1）：终态只有 value，改票历史必须可追溯
        await UserSkillService.append_log(
            user_id=user_id, session_id=session_id, kind="feedback",
            action="vote" if not old_value else "revote",
            name=",".join(skill_ids) or request_id,
            detail=f"{old_value} -> {value}",
        )
        return {"ok": True, "changed": True, "skill_ids": skill_ids}

    @staticmethod
    async def rebuild_stats_from_feedback() -> dict[str, dict]:
        """从 message_feedback 事实源全量重算所有技能的 stats.up/down（§8.2 v2）。

        派生缓存（$inc+回滚）的任何中途失败都会漂移——此函数是唯一的
        自愈路径：聚合 feedback 的 value × skill_ids 快照，覆盖写回两库。
        load_count 不动（其事实源是 load 日志，非反馈）。

        Returns:
            {skill_id: {"up": n, "down": n}} — 重算结果
        """
        up: dict[str, int] = {}
        down: dict[str, int] = {}
        async for fb in UserSkillService._msg_feedback_col().find({"value": {"$in": [1, -1]}}):
            for sid in fb.get("skill_ids", []):
                if fb["value"] == 1:
                    up[sid] = up.get(sid, 0) + 1
                else:
                    down[sid] = down.get(sid, 0) + 1

        ids = sorted(set(up) | set(down))
        usk_ids = [i for i in ids if i.startswith("usk_")]
        tool_ids = [i for i in ids if i.startswith("tool_")]

        result: dict[str, dict] = {}
        now = utc_now().isoformat()
        for sid in usk_ids:
            stats = {"up": up.get(sid, 0), "down": down.get(sid, 0)}
            await UserSkillService._col().update_one(
                {"_id": sid},
                {"$set": {"stats.up": stats["up"], "stats.down": stats["down"], "updated_at": now}},
            )
            result[sid] = stats
        if tool_ids:
            from app.services.tool_service import ToolService

            for sid in tool_ids:
                stats = {"up": up.get(sid, 0), "down": down.get(sid, 0)}
                await ToolService._collection().update_one(
                    {"_id": sid},
                    {"$set": {"stats.up": stats["up"], "stats.down": stats["down"]}},
                )
                result[sid] = stats
        return result

    @staticmethod
    async def session_message_feedback(user_id: str, session_id: str) -> list[dict]:
        """会话内各轮的反馈态与使用技能（前端渲染消息级 👍/👎 与技能标签）。

        返回按轮（request_id）：value（0=未投）+ skills（本轮 load 的技能，
        已删除的技能跳过）。
        """
        # 本轮技能（从 load 日志聚合，只含有 request_id 的新记录）
        rounds: dict[str, list[str]] = {}
        names: dict[str, str] = {}
        async for log in UserSkillService._log_col().find(
            {"user_id": user_id, "session_id": session_id, "kind": "load", "ok": True,
             "request_id": {"$ne": ""}},
        ):
            rid = log.get("request_id", "")
            sid = log.get("skill_id", "")
            if rid and sid:
                rounds.setdefault(rid, [])
                if sid not in rounds[rid]:
                    rounds[rid].append(sid)
                names[sid] = log.get("name", "")

        # 我的反馈（一轮一票）
        votes: dict[str, int] = {}
        async for fb in UserSkillService._msg_feedback_col().find(
            {"user_id": user_id, "session_id": session_id},
        ):
            votes[fb["request_id"]] = int(fb.get("value", 0) or 0)

        if not rounds and not votes:
            return []

        # 技能元数据（官方/个人分库批量；已删除的跳过）
        usk_ids = [sid for sids in rounds.values() for sid in sids if sid.startswith("usk_")]
        tool_ids = [sid for sids in rounds.values() for sid in sids if sid.startswith("tool_")]
        meta: dict[str, dict] = {}
        if usk_ids:
            async for d in UserSkillService._col().find({"_id": {"$in": usk_ids}}):
                meta[d["_id"]] = d
        if tool_ids:
            from app.services.tool_service import ToolService

            async for d in ToolService._collection().find({"_id": {"$in": tool_ids}}):
                meta[d["_id"]] = d

        result = []
        for rid, sids in rounds.items():
            skills = [
                {
                    "skill_id": sid,
                    "name": meta.get(sid, {}).get("name") or names.get(sid, ""),
                    "kind": "official" if sid.startswith("tool_") else "user",
                }
                for sid in sids if sid in meta
            ]
            result.append({"request_id": rid, "value": votes.get(rid, 0), "skills": skills})
        # 已投但本轮无 load 记录的轮次（无技能可派生，反馈仍记录）
        for rid, v in votes.items():
            if rid not in rounds and v:
                result.append({"request_id": rid, "value": v, "skills": []})
        return result


__all__ = ["UserSkillService", "UserSkillError"]
