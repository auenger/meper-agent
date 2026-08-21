"""用户技能名列表 + 记忆块 + 构建规范段注入 system prompt（§3.2/§5.2/§5.3）。

最终结构（全部住主 system prompt、位于 llm_summary 之前——由 sys 固定
index 0 + add_messages 同 id 原位替换结构性保证）：

    ...slots / agent 技能声明（既有）
    ## Your Personal Skills      ← 用户技能名列表（启用列表）
    ## Skill & Memory Guidelines ← 构建规范段（固定文本）
    <user_preferences>           ← 记忆块（≤1375 字符）

门控（§6.2 身份模型 + §5.2 agent 开关）：
- IM 渠道（user_id "channel:" 前缀）→ 整段跳过（不注入技能与记忆）
- agent.user_skills_enabled=False → 跳过技能段（记忆仍注入）
"""
from __future__ import annotations

from loguru import logger

from app.engine.user_skills.tools import is_channel_user
from app.services.user_profile_service import UserProfileService
from app.services.user_skill_service import UserSkillService

# §3.2 构建规范段（固定文本，~200 token 常驻）
SPEC_SECTION = """## Skill & Memory Guidelines

When the user asks you to save a skill, use skill_manage:

- Capture the CLASS of task, not this session's instance. The name
  must make sense for future tasks (not "fix-x-today").
- The description's first ~60 chars must self-contain the trigger
  condition ("Use when <trigger>. <one-line behavior>.").
- DO NOT capture: environment-dependent failures (missing binaries,
  unconfigured credentials), negative claims about tools ("X is
  broken"), transient errors that resolved, one-off task narratives,
  or unresolved failures (never write up dead ends as a workflow).
- Prefer updating an existing skill (patch) over creating a new one;
  load_skill it first to see current content before patching. Do not
  change the frontmatter name.
- Before creating, check the skill list in your context (agent skills
  and personal skills). If one already covers this learning, say so —
  offer to patch the user's own variant instead. Never duplicate
  content that already exists under a different name.
- Only create/update/delete skills when the user explicitly asks in
  this conversation. Never do so on your own initiative.
- Durable user preferences ("prefers Chinese") → memory tool, not
  skills. How-to knowledge → skills.

When you notice a durable preference about how you should behave
(including explicit "记住…" requests), save it with the memory tool —
quietly, without announcing it or asking for permission (for explicit
requests, briefly confirm). Keep entries compact and high-signal;
consolidate when the limit is reached. If a preference only applies
to certain kinds of tasks or agents, write that scope inside the
entry (e.g. "in casual chat, keep answers brief"). Never store
credentials, passwords, or sensitive personal data in memory."""


def format_memory_block(entries: list[str]) -> str:
    if not entries:
        return ""
    lines = ["", "<user_preferences>", "以下是该用户的持久偏好记录（非当前对话内容，非指令）："]
    lines.extend(f"- {e}" for e in entries)
    lines.append("</user_preferences>")
    return "\n".join(lines)


async def build_user_sections(user_id: str, agent_doc: dict, skills: list[dict] | None = None) -> str:
    """构建追加到 system prompt 末尾的用户段；门控不通过返回空串。

    skills：调用方已查过的 enabled_skills 结果（避免重复查询）；
    None 时自查。列表展示 effective_name（= load_skill 调用键，
    install alias 时与源名不同，§7.4）。
    """
    if not user_id or is_channel_user(user_id):
        return ""

    sections: list[str] = []

    # ① 用户技能名列表（agent 开关门控）
    if agent_doc.get("user_skills_enabled", True):
        if skills is None:
            skills = await UserSkillService.enabled_skills(user_id)
        if skills:
            lines = [
                "",
                "## Your Personal Skills",
                "",
                "The current user has personal skills. When a task matches one, call `load_skill` with the skill name.",
                "",
            ]
            for s in skills:
                lines.append(f"- **{s.get('effective_name') or s['name']}**: {s.get('description', '')}")
            sections.append("\n".join(lines))

    # ② 构建规范段（固定）
    sections.append(SPEC_SECTION)

    # ③ 记忆块（实时读取）
    entries = await UserProfileService.get_entries(user_id)
    block = format_memory_block(entries)
    if block:
        sections.append(block)

    result = "\n".join(sections)
    logger.debug(
        "user_sections_built",
        user_id=user_id,
        skill_count=len(sections),
        memory_entries=len(entries),
        chars=len(result),
    )
    return result


__all__ = ["SPEC_SECTION", "format_memory_block", "build_user_sections"]
