"""User-level skill & memory models.

用户级经验资产（v6 设计，docs/planning-artifacts/agent-self-learning-design.md）：

- UserSkill        用户技能元数据（内容在磁盘 users/{user_id}/{name}/SKILL.md）
- UserSkillBinding 启用列表（own=自建 / installed=广场安装，Phase 2）
- SkillLog         工具动作与 load 事件审计
- UserProfile      用户记忆条目（per-user，跨 agent）

身份模型（§6.2）：统一平台用户 user_id；IM 渠道（"channel:" 前缀）不生成
记忆/技能——注入层跳过、工具写入被拒。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.base import generate_id, utc_now

# 每用户技能软上限（含安装的，§4.3）
MAX_USER_SKILLS = 20
# 记忆容量上限（§6.3）
MEMORY_TOTAL_CHAR_LIMIT = 1375
MEMORY_ENTRY_CHAR_LIMIT = 200

# 技能名约束：防路径穿越 + 人类可读（§4.1）
SKILL_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$"


class UserSkill(BaseModel):
    """用户技能元数据。磁盘内容为事实源，DB 只存元数据（§5.1）。"""

    id: str = Field(default_factory=lambda: generate_id("usk"), alias="_id")
    owner_user_id: str = Field(..., description="属主平台用户 id")
    name: str = Field(..., description="技能名（用户命名空间内唯一，同时约束目录名）")
    description: str = Field(default="", description="前 ~60 字符须自包含触发条件")
    status: str = Field(
        default="private",
        description="private | submitted | published | hidden（Phase 2 发布流）",
    )
    derived_from: str | None = Field(default=None, description="fork 来源 skill_id（§7.4）")
    stats: dict[str, Any] = Field(
        default_factory=lambda: {
            "load_count": 0,
            "up": 0,
            "down": 0,
        },
    )
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())
    published_at: str | None = Field(default=None)
    approved_by: str | None = Field(default=None)


class UserSkillBinding(BaseModel):
    """用户技能启用列表：own=自建，installed=广场安装（引用 skill_id）。

    alias：安装时的本机加载名（启用列表内重名时改名安装，§7.4）——
    影响展示与 load_skill 键，绑定仍按 skill_id 引用。
    """

    id: str = Field(default_factory=lambda: generate_id("bnd"), alias="_id")
    user_id: str = Field(..., description="启用者（安装场景可与 owner 不同）")
    skill_id: str = Field(..., description="指向 user_skills._id")
    source: str = Field(default="own", description="own | installed")
    alias: str = Field(default="", description="安装别名（空=用原名）")
    enabled: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


class SkillLog(BaseModel):
    """skill/memory 工具动作与 load 事件审计（§9.2）。"""

    id: str = Field(default_factory=lambda: generate_id("slog"), alias="_id")
    user_id: str = Field(default="")
    session_id: str = Field(default="")
    kind: str = Field(default="skill", description="skill | memory | load")
    action: str = Field(default="", description="create/edit/patch/delete/add/replace/remove/load/...")
    name: str = Field(default="", description="技能名（memory 动作为空）")
    ok: bool = Field(default=True)
    detail: str = Field(default="", description="失败原因等摘要（不存全文）")
    created_at: str = Field(default_factory=lambda: utc_now().isoformat())


class UserProfile(BaseModel):
    """用户记忆（per-user，跨 agent，entries 为字符串列表，§6.2）。"""

    id: str = Field(default_factory=lambda: generate_id("prof"), alias="_id")
    user_id: str = Field(..., description="统一平台用户 id")
    entries: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: utc_now().isoformat())


__all__ = [
    "MAX_USER_SKILLS",
    "MEMORY_TOTAL_CHAR_LIMIT",
    "MEMORY_ENTRY_CHAR_LIMIT",
    "SKILL_NAME_PATTERN",
    "UserSkill",
    "UserSkillBinding",
    "SkillLog",
    "UserProfile",
]
