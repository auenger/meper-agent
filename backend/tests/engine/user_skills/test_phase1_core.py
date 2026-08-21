"""用户级技能与记忆 — Phase 1 核心逻辑测试（不依赖真实 DB）。

覆盖：
- UserSkillService 的纯逻辑部分：名字校验、描述提取、patch 语义（离线函数）
- SkillManager 双根 / 个人优先 / 白名单 / load 回调（harness 扩展）
- 注入格式：规范段、记忆块、channel 门控
- 工具 schema 跨用户字节稳定（缓存前提）
- memory 条目纯校验逻辑（单条/总量上限）
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 纯逻辑：名字校验 + 描述提取 + patch 语义
# ---------------------------------------------------------------------------


class TestNameAndDescription:
    def test_valid_names(self):
        from app.services.user_skill_service import UserSkillService

        for name in ("abc", "a-b_c9", "Currency123", "x" * 64):
            assert UserSkillService._validate_name(name) == name

    @pytest.mark.parametrize("bad", [
        "", "../etc", "a/b", "a\\b", ".hidden", "-lead", "_lead", "a" * 65, "名 字",
    ])
    def test_invalid_names_rejected(self, bad):
        from app.services.user_skill_service import UserSkillError, UserSkillService

        with pytest.raises(UserSkillError):
            UserSkillService._validate_name(bad)

    def test_description_extracted_from_frontmatter(self):
        from app.services.user_skill_service import UserSkillService

        content = "---\nname: x\ndescription: Use when summing amounts. Convert first.\n---\n# X"
        assert "Use when summing" in UserSkillService._extract_description(content)
        assert UserSkillService._extract_description("no frontmatter") == ""


# ---------------------------------------------------------------------------
# SkillManager 双根（harness 扩展）
# ---------------------------------------------------------------------------


class TestSkillManagerDualRoot:
    def _setup(self, root: Path):
        (root / "global_skill").mkdir()
        (root / "global_skill" / "SKILL.md").write_text(
            "---\nname: global_skill\ndescription: g\n---\nGLOBAL BODY"
        )
        personal = root / "users" / "user_a"
        (personal / "my_skill").mkdir(parents=True)
        (personal / "my_skill" / "SKILL.md").write_text(
            "---\nname: my_skill\ndescription: m\n---\nMINE BODY"
        )
        # 同名冲突：个人版应优先
        (personal / "global_skill").mkdir()
        (personal / "global_skill" / "SKILL.md").write_text("PERSONAL OVERRIDE")
        return personal

    def test_dual_root_priority_and_callback(self):
        from agent_flow_harness.skills import SkillManager

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            personal = self._setup(root)
            loaded: list[str] = []
            mgr = SkillManager(
                skills_dir=root,
                extra_skill_files={
                    "my_skill": personal / "my_skill" / "SKILL.md",
                    "global_skill": personal / "global_skill" / "SKILL.md",
                },
                on_skill_loaded=lambda n: loaded.append(n),
            )
            mgr.set_allowed({"global_skill", "my_skill"})

            assert "MINE BODY" in mgr.load_skill("my_skill")
            assert "PERSONAL OVERRIDE" in mgr.load_skill("global_skill")  # 个人优先
            assert [s.name for s in mgr.list_skills()] == ["global_skill", "my_skill"]
            assert loaded == ["my_skill", "global_skill"]  # 回调触发

            denied = mgr.load_skill("not_allowed")
            assert "not available" in denied

    def test_callback_exception_swallowed(self):
        from agent_flow_harness.skills import SkillManager

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "s1").mkdir()
            (root / "s1" / "SKILL.md").write_text("BODY")
            mgr = SkillManager(
                skills_dir=root,
                on_skill_loaded=lambda n: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            assert "BODY" in mgr.load_skill("s1")  # 埋点失败不影响加载


# ---------------------------------------------------------------------------
# 注入格式与门控
# ---------------------------------------------------------------------------


class TestInjection:
    def test_spec_section_covers_key_rules(self):
        from app.engine.user_skills.injection import SPEC_SECTION

        assert "skill_manage" in SPEC_SECTION
        assert "memory tool" in SPEC_SECTION
        assert "Never store" in SPEC_SECTION  # 敏感信息禁令
        assert "explicitly asks" in SPEC_SECTION  # 仅显式触发

    def test_memory_block_format(self):
        from app.engine.user_skills.injection import format_memory_block

        blk = format_memory_block(["偏好中文", "周报要极简"])
        assert "<user_preferences>" in blk and "</user_preferences>" in blk
        assert "- 偏好中文" in blk
        assert "持久偏好记录" in blk  # 语义隔离声明
        assert format_memory_block([]) == ""

    def test_channel_user_gated(self):
        from app.engine.user_skills.tools import is_channel_user

        assert is_channel_user("channel:42:lark_123")
        assert not is_channel_user("user_zhangsan")
        assert not is_channel_user("")


# ---------------------------------------------------------------------------
# 工具 schema 静态性（tools[] 字节稳定 = 缓存前提，§5.4）
# ---------------------------------------------------------------------------


class TestToolSchemaStability:
    def test_schema_identical_across_users(self):
        from app.engine.user_skills.tools import make_user_skill_tools

        t1 = make_user_skill_tools("user_a", "sess_1", set())
        t2 = make_user_skill_tools("user_b", "sess_2", set())
        assert [t.name for t in t1] == ["skill_manage", "memory"]
        s1 = json.dumps(t1[0].args_schema.model_json_schema(), sort_keys=True)
        s2 = json.dumps(t2[0].args_schema.model_json_schema(), sort_keys=True)
        assert s1 == s2


# ---------------------------------------------------------------------------
# memory 条目校验（纯逻辑，走 _check_entry / _total_chars）
# ---------------------------------------------------------------------------


class TestMemoryEntryValidation:
    def test_entry_limit(self):
        from app.models.user_skill import MEMORY_ENTRY_CHAR_LIMIT
        from app.services.user_profile_service import MemoryError, UserProfileService

        assert UserProfileService._check_entry("偏好中文") == "偏好中文"
        with pytest.raises(MemoryError):
            UserProfileService._check_entry("x" * (MEMORY_ENTRY_CHAR_LIMIT + 1))
        with pytest.raises(MemoryError):
            UserProfileService._check_entry("   ")

    def test_total_chars(self):
        from app.services.user_profile_service import UserProfileService

        assert UserProfileService._total_chars(["ab", "cde"]) == 5


# ---------------------------------------------------------------------------
# fork 官方技能（tool_*，§7.4：任何 agent 技能可 fork）
# ---------------------------------------------------------------------------


class TestForkOfficialSkill:
    def _patch(self, monkeypatch, *, name_taken=False):
        """在服务接缝上打桩：不连 DB/磁盘，验证分支与字段语义。"""
        from app.services import user_skill_service as uss

        calls: dict = {}

        async def _no_user_skill(_sid):
            return None

        async def _file_content(_tid, _path):
            return {"path": "SKILL.md", "content": "---\nname: official_x\n---\nOFFICIAL BODY"}

        async def _tool_doc(_tid):
            return {"_id": "tool_1", "name": "official_x", "description": "官方描述"}

        async def _find_by_name(_uid, name):
            return {"exists": True} if (name_taken and name == "official_x") else None

        async def _create_skill(uid, name, content):
            calls["create"] = (uid, name, content)
            return {"_id": "usk_new", "owner_user_id": uid, "name": name}

        class _Col:
            async def update_one(self, filt, update, **_kw):
                calls["update"] = (filt, update)

        monkeypatch.setattr(uss.UserSkillService, "get_skill", staticmethod(_no_user_skill))
        monkeypatch.setattr(
            __import__("app.services.tool_service", fromlist=["ToolService"]).ToolService,
            "get_tool_file_content", staticmethod(_file_content),
        )
        monkeypatch.setattr(
            __import__("app.services.tool_service", fromlist=["ToolService"]).ToolService,
            "get_tool", staticmethod(_tool_doc),
        )
        monkeypatch.setattr(uss.UserSkillService, "find_by_name", staticmethod(_find_by_name))
        monkeypatch.setattr(uss.UserSkillService, "create_skill", staticmethod(_create_skill))
        monkeypatch.setattr(uss.UserSkillService, "_col", staticmethod(lambda: _Col()))
        # 审计日志打到真实 Motor——跨文件全量跑时复用已关闭的 loop
        # （"Event loop is closed"）；本测试只关心收录语义，打桩隔离
        async def _no_log(**_kw):
            pass
        monkeypatch.setattr(uss.UserSkillService, "append_log", staticmethod(_no_log))
        return calls

    @pytest.mark.asyncio
    def test_fork_official_copies_content_and_provenance(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        calls = self._patch(monkeypatch)
        result = asyncio.run(
            UserSkillService.fork("user_a", "tool_1")
        )
        uid, name, content = calls["create"]
        assert uid == "user_a" and name == "official_x"  # 原名可用
        assert "OFFICIAL BODY" in content                 # 官方内容被复制
        filt, update = calls["update"]
        assert filt == {"_id": "usk_new"}
        assert update["$set"]["derived_from"] == "tool_1"  # 溯源到官方 tool id
        assert update["$set"]["description"] == "官方描述"  # 描述一并复制
        assert result["forked_as"] == "official_x"

    @pytest.mark.asyncio
    def test_fork_official_name_taken_gets_suffix(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        calls = self._patch(monkeypatch, name_taken=True)
        result = asyncio.run(
            UserSkillService.fork("user_a", "tool_1")
        )
        _, name, _ = calls["create"]
        assert name == "official_x-fork1"
        assert result["forked_as"] == "official_x-fork1"


# ---------------------------------------------------------------------------
# 管理员收录：fork(as_official=True) → tools 表（§7.6）
# ---------------------------------------------------------------------------


class TestForkAsOfficial:
    def _patch(self, monkeypatch):
        """打桩：user_skills 源 + ToolService 插入与名字查询，验证收录语义。"""
        from app.services import tool_service as ts
        from app.services import user_skill_service as uss

        calls: dict = {}

        async def _src(_sid):
            return {
                "_id": "usk_9", "owner_user_id": "user_b", "name": "weekly_report",
                "description": "Use when writing weekly reports.", "status": "published",
            }

        async def _read_content(doc):
            return "---\nname: weekly_report\n---\nSOURCE BODY"

        async def _tool_find_by_name(name):
            calls.setdefault("tool_names", []).append(name)
            return {"_id": "tool_x"} if name == "weekly_report" else None

        async def _insert(name, doc):
            calls["insert"] = (name, doc)
            return doc

        def _materialize(name, files):
            calls["materialize"] = (name, [f.path for f in files])

        monkeypatch.setattr(uss.UserSkillService, "get_skill", staticmethod(_src))
        monkeypatch.setattr(uss.UserSkillService, "read_content", staticmethod(_read_content))
        monkeypatch.setattr(ts.ToolService, "find_by_name", staticmethod(_tool_find_by_name))
        monkeypatch.setattr(ts.ToolService, "_insert_skill_doc", staticmethod(_insert))
        # 同上：审计日志隔离，避免真实 Motor 跨 loop 污染全量跑
        async def _no_log(**_kw):
            pass
        monkeypatch.setattr(uss.UserSkillService, "append_log", staticmethod(_no_log))
        import app.engine.tool.skill_fs as sfs

        monkeypatch.setattr(sfs, "materialize_skill", _materialize)
        return calls

    @pytest.mark.asyncio
    def test_admin_fork_promotes_to_official(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        calls = self._patch(monkeypatch)
        result = asyncio.run(
            UserSkillService.fork("admin_1", "usk_9", as_official=True)
        )
        # 原名被官方占用 → 自动 -official 后缀
        name, doc = calls["insert"]
        assert name == "weekly_report-official1"
        assert doc["source"] == "markdown" and doc["created_by"] == "admin_1"
        assert doc["stats"] == {"load_count": 0, "up": 0, "down": 0}
        assert "SOURCE BODY" in doc["instructions"]
        assert calls["materialize"][0] == "weekly_report-official1"
        assert result["kind"] == "official"

    @pytest.mark.asyncio
    def test_admin_fork_official_source_rejected(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillError, UserSkillService

        self._patch(monkeypatch)
        with pytest.raises(UserSkillError, match="edited directly"):
            asyncio.run(
                UserSkillService.fork("admin_1", "tool_1", as_official=True)
            )


# ---------------------------------------------------------------------------
# 管理员无个人技能：会话内 create 直接产出官方（§7.6）
# ---------------------------------------------------------------------------


class TestAdminCreateRoutesToOfficial:
    @pytest.mark.asyncio
    def test_admin_create_via_tool_goes_official(self, monkeypatch):
        """is_admin=True 时 skill_manage 的 create 走 create_official，不建个人技能。"""
        import asyncio
        import json

        from app.services import user_skill_service as uss

        calls: dict = {}

        async def _create_official(uid, name, content):
            calls["official"] = (uid, name, content)
            return {"_id": "tool_new", "name": name}

        async def _create_personal(uid, name, content):
            calls["personal"] = (uid, name, content)
            return {"_id": "usk_new", "name": name}

        async def _log(**kw):
            pass

        monkeypatch.setattr(uss.UserSkillService, "create_official", staticmethod(_create_official))
        monkeypatch.setattr(uss.UserSkillService, "create_skill", staticmethod(_create_personal))
        monkeypatch.setattr(uss.UserSkillService, "append_log", staticmethod(_log))

        from app.engine.user_skills.tools import make_skill_manage_tool

        tool = make_skill_manage_tool("admin_1", "sess_1", set(), is_admin=True)
        result = asyncio.run(
            tool.ainvoke({"action": "create", "name": "x", "content": "BODY"})
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert "OFFICIAL" in payload["note"]
        assert "official" in calls and "personal" not in calls

        # 普通用户仍走个人
        tool2 = make_skill_manage_tool("user_1", "sess_1", set(), is_admin=False)
        asyncio.run(
            tool2.ainvoke({"action": "create", "name": "x", "content": "BODY"})
        )
        assert "personal" in calls


# ---------------------------------------------------------------------------
# 官方/个人重名遮蔽（§7.4）：注入层官方声明剔除被个人遮蔽的同名技能
# ---------------------------------------------------------------------------


class TestSkillShadowing:
    def test_excluded_official_not_in_declaration(self, monkeypatch):
        import asyncio

        from app.engine.agent import builder
        from app.services import tool_service as ts

        async def _get_tools_by_ids(_ids):
            return [
                {"_id": "tool_1", "name": "pdf_merge", "description": "official pdf"},
                {"_id": "tool_2", "name": "csv_clean", "description": "official csv"},
            ]

        monkeypatch.setattr(ts.ToolService, "get_tools_by_ids", staticmethod(_get_tools_by_ids))
        text = asyncio.run(
            builder.build_skill_declaration(["tool_1", "tool_2"], exclude_names={"pdf_merge"})
        )
        assert "pdf_merge" not in text  # 被个人同名技能遮蔽——不进官方声明
        assert "csv_clean" in text

    def test_no_exclude_keeps_all(self, monkeypatch):
        import asyncio

        from app.engine.agent import builder
        from app.services import tool_service as ts

        async def _get_tools_by_ids(_ids):
            return [{"_id": "tool_1", "name": "pdf_merge", "description": "official"}]

        monkeypatch.setattr(ts.ToolService, "get_tools_by_ids", staticmethod(_get_tools_by_ids))
        text = asyncio.run(builder.build_skill_declaration(["tool_1"]))
        assert "pdf_merge" in text

    def test_injection_uses_effective_name(self, monkeypatch):
        """install alias 后 load 键是 effective_name——注入列表必须同键（否则显示名≠调用名）。"""
        import asyncio

        from app.engine.user_skills import injection
        from app.services import user_skill_service as uss

        async def _enabled(_uid):
            return [
                {"name": "pdf_merge", "effective_name": "pdf_merge-pro", "description": "aliased"},
            ]

        async def _entries(_uid):
            return []

        monkeypatch.setattr(uss.UserSkillService, "enabled_skills", staticmethod(_enabled))
        monkeypatch.setattr(
            "app.services.user_profile_service.UserProfileService.get_entries", staticmethod(_entries)
        )
        text = asyncio.run(injection.build_user_sections("user_a", {"user_skills_enabled": True}))
        assert "**pdf_merge-pro**" in text
        assert "**pdf_merge**:" not in text


# ---------------------------------------------------------------------------
# 会话技能反馈条数据源（§8.2：投票入口在对话中）
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()


class _FakeCol:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    @staticmethod
    def _match(d: dict, q: dict) -> bool:
        for k, v in q.items():
            dv = d.get(k)
            if isinstance(v, dict) and "$in" in v:
                if dv not in v["$in"]:
                    return False
            elif isinstance(v, dict) and "$ne" in v:
                if dv == v["$ne"]:
                    return False
            elif dv != v:
                return False
        return True

    def find(self, q, *_a, **_k):
        return _FakeCursor([d for d in self.docs if self._match(d, q)])

    async def find_one(self, q, *_a, **_k):
        for d in self.docs:
            if self._match(d, q):
                return d
        return None


# ---------------------------------------------------------------------------
# 消息级反馈（§8.2 v2：赞回复 → 本轮技能派生加减分）
# ---------------------------------------------------------------------------


def _apply_update(doc: dict, update: dict, *, on_insert: bool = False) -> None:
    """简化 Mongo update 操作符：$inc（支持嵌套一层）/ $set / $setOnInsert。"""
    def _set(path: str, v) -> None:
        if "." in path:
            parent, key = path.split(".", 1)
            doc.setdefault(parent, {})[key] = v
        else:
            doc[path] = v

    def _inc(path: str, v: float) -> None:
        if "." in path:
            parent, key = path.split(".", 1)
            sub = doc.setdefault(parent, {})
            sub[key] = (sub.get(key) or 0) + v
        else:
            doc[path] = (doc.get(path) or 0) + v

    for path, v in (update.get("$set") or {}).items():
        _set(path, v)
    for path, v in (update.get("$setOnInsert") or {}).items():
        if on_insert:
            _set(path, v)
    for path, v in (update.get("$inc") or {}).items():
        _inc(path, v)


class _WriteCol(_FakeCol):
    """FakeCol + update_one（upsert）/ distinct——vote_message 依赖。"""

    def __init__(self, docs: list[dict] | None = None):
        super().__init__(docs or [])

    async def update_one(self, filt: dict, update: dict, upsert: bool = False) -> None:
        doc = next((d for d in self.docs if self._match(d, filt)), None)
        if doc is None:
            if not upsert:
                return
            doc = {k: v for k, v in filt.items() if not isinstance(v, dict)}
            self.docs.append(doc)
            _apply_update(doc, update, on_insert=True)
            return
        _apply_update(doc, update)

    async def find_one(self, q, *a, **k):
        for d in self.docs:
            if self._match(d, q):
                return d
        return None

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(doc)

    async def distinct(self, field: str, q: dict) -> list:
        # Motor 语义：协程返回 list
        return sorted({d[field] for d in self.docs if self._match(d, q) and d.get(field)})

    @property
    def by_id(self) -> dict:
        return {d["_id"]: d for d in self.docs}


class TestVoteMessage:
    def _patch(self, monkeypatch, *, logs, skills, tools, msg_feedbacks):
        import app.services.tool_service as ts_mod
        from app.services import user_skill_service as uss

        cols = {
            "log": _WriteCol(logs),
            "skill": _WriteCol(skills),
            "tool": _WriteCol(tools),
            "msgfb": _WriteCol(msg_feedbacks),
        }
        monkeypatch.setattr(uss.UserSkillService, "_log_col", staticmethod(lambda: cols["log"]))
        monkeypatch.setattr(uss.UserSkillService, "_col", staticmethod(lambda: cols["skill"]))
        monkeypatch.setattr(uss.UserSkillService, "_msg_feedback_col", staticmethod(lambda: cols["msgfb"]))
        monkeypatch.setattr(ts_mod.ToolService, "_collection", staticmethod(lambda: cols["tool"]))
        return cols

    def test_first_vote_distributes_to_round_skills(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            logs=[
                {"user_id": "u1", "session_id": "s1", "request_id": "r1", "kind": "load",
                 "ok": True, "skill_id": "usk_1", "name": "w"},
                {"user_id": "u1", "session_id": "s1", "request_id": "r1", "kind": "load",
                 "ok": True, "skill_id": "tool_1", "name": "p"},
            ],
            skills=[{"_id": "usk_1", "name": "w", "stats": {"up": 0, "down": 0}}],
            tools=[{"_id": "tool_1", "name": "p", "stats": {}}],
            msg_feedbacks=[],
        )
        result = asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", 1))
        assert result["changed"] is True
        assert set(result["skill_ids"]) == {"usk_1", "tool_1"}
        assert cols["skill"].by_id["usk_1"]["stats"]["up"] == 1
        assert cols["tool"].by_id["tool_1"]["stats"]["up"] == 1
        fb = cols["msgfb"].docs[0]
        assert fb["value"] == 1 and set(fb["skill_ids"]) == {"usk_1", "tool_1"}

    def test_revote_rolls_back_snapshot_then_applies(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            logs=[{"user_id": "u1", "session_id": "s1", "request_id": "r1", "kind": "load",
                   "ok": True, "skill_id": "usk_1", "name": "w"}],
            skills=[{"_id": "usk_1", "name": "w", "stats": {"up": 1, "down": 0}}],
            tools=[],
            # 已投 👍（up 已含上次发放的 +1；旧快照含 usk_1）
            msg_feedbacks=[{"user_id": "u1", "session_id": "s1", "request_id": "r1",
                            "value": 1, "skill_ids": ["usk_1"]}],
        )
        asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", -1))  # 改 👎
        stats = cols["skill"].by_id["usk_1"]["stats"]
        assert stats["up"] == 0 and stats["down"] == 1  # 回滚 +1 → 发放 -1
        assert cols["msgfb"].docs[0]["value"] == -1

    def test_same_direction_is_noop(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            logs=[{"user_id": "u1", "session_id": "s1", "request_id": "r1", "kind": "load",
                   "ok": True, "skill_id": "usk_1", "name": "w"}],
            skills=[{"_id": "usk_1", "name": "w", "stats": {"up": 5, "down": 0}}],
            tools=[],
            msg_feedbacks=[{"user_id": "u1", "session_id": "s1", "request_id": "r1",
                            "value": 1, "skill_ids": ["usk_1"]}],
        )
        result = asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", 1))
        assert result["changed"] is False
        assert cols["skill"].by_id["usk_1"]["stats"]["up"] == 5  # 未变

    def test_auto_hide_at_score_minus_3(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            logs=[{"user_id": "u1", "session_id": "s1", "request_id": "r1", "kind": "load",
                   "ok": True, "skill_id": "usk_1", "name": "w"}],
            skills=[{"_id": "usk_1", "name": "w", "status": "published",
                     "stats": {"up": 0, "down": 2}}],
            tools=[],
            msg_feedbacks=[],
        )
        asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", -1))  # down→3, score=-3
        assert cols["skill"].by_id["usk_1"]["status"] == "hidden"

    def test_round_without_skills_still_records(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch, logs=[], skills=[], tools=[], msg_feedbacks=[],
        )
        result = asyncio.run(UserSkillService.vote_message("u1", "s1", "r_none", 1))
        assert result["changed"] is True and result["skill_ids"] == []
        assert cols["msgfb"].docs[0]["value"] == 1


# ---------------------------------------------------------------------------
# 评审修复（P0/L-1/P1-4）：resume 轮次键 / 反馈审计 / 全量重算
# ---------------------------------------------------------------------------


class TestResumeRoundKey:
    def test_append_updates_request_id(self, monkeypatch):
        """P0：resume 追加必须把消息 request_id 更新为本轮——否则 resume 段 load 与反馈断链。"""
        import asyncio

        from app.services import session_service as ss

        last_msg = {"_id": "msg_1", "session_id": "s1", "role": "agent",
                    "request_id": "old_req", "timeline_entries": []}
        updates: list[tuple] = []

        class _MsgCol:
            async def find_one(self, *a, **k):
                return dict(last_msg)

            async def update_one(self, filt, update, **k):
                updates.append((filt, update))

        monkeypatch.setattr(ss.MessageService, "_collection", staticmethod(lambda: _MsgCol()))
        asyncio.run(ss.MessageService.append_to_last_agent_message(
            "s1", [{"type": "text"}], token_usage={"total_tokens": 1}, request_id="new_req",
        ))
        assert updates and updates[0][1]["$set"]["request_id"] == "new_req"
        assert updates[0][1]["$set"]["token_usage"] == {"total_tokens": 1}


class TestVoteAuditAndRebuild:
    def _patch(self, monkeypatch, *, msg_feedbacks, skills, tools):
        import app.services.tool_service as ts_mod
        from app.services import user_skill_service as uss

        cols = {
            "log": _WriteCol([]),
            "skill": _WriteCol(skills),
            "tool": _WriteCol(tools),
            "msgfb": _WriteCol(msg_feedbacks),
        }
        monkeypatch.setattr(uss.UserSkillService, "_log_col", staticmethod(lambda: cols["log"]))
        monkeypatch.setattr(uss.UserSkillService, "_col", staticmethod(lambda: cols["skill"]))
        monkeypatch.setattr(uss.UserSkillService, "_msg_feedback_col", staticmethod(lambda: cols["msgfb"]))
        monkeypatch.setattr(ts_mod.ToolService, "_collection", staticmethod(lambda: cols["tool"]))
        return cols

    def test_vote_writes_audit_log(self, monkeypatch):
        """L-1：投票必须进 skill_logs 审计（改票历史可追溯）。"""
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(monkeypatch, msg_feedbacks=[], skills=[], tools=[])
        asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", 1))
        audit = [d for d in cols["log"].docs if d["kind"] == "feedback"]
        assert audit and audit[0]["action"] == "vote"
        assert "0 -> 1" in audit[0]["detail"]

    def test_revote_audit_records_transition(self, monkeypatch):
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            msg_feedbacks=[{"user_id": "u1", "session_id": "s1", "request_id": "r1",
                            "value": 1, "skill_ids": []}],
            skills=[], tools=[],
        )
        asyncio.run(UserSkillService.vote_message("u1", "s1", "r1", -1))
        audit = [d for d in cols["log"].docs if d["kind"] == "feedback"]
        assert audit[0]["action"] == "revote" and "1 -> -1" in audit[0]["detail"]

    def test_rebuild_overwrites_drifted_stats(self, monkeypatch):
        """P1-4：stats 漂移（如 up=5 但事实源只有 1 赞）时全量重算覆盖写回。"""
        import asyncio

        from app.services.user_skill_service import UserSkillService

        cols = self._patch(
            monkeypatch,
            msg_feedbacks=[
                {"skill_ids": ["usk_1", "tool_1"], "value": 1},
                {"skill_ids": ["usk_1"], "value": 1},
                {"skill_ids": ["usk_1"], "value": -1},
            ],
            skills=[{"_id": "usk_1", "stats": {"up": 5, "down": 0}}],  # 漂移值
            tools=[{"_id": "tool_1", "stats": {"up": 9, "down": 9}}],
        )
        result = asyncio.run(UserSkillService.rebuild_stats_from_feedback())
        # usk_1: 两赞一踩 → up=2 down=1；tool_1: 一赞 → up=1 down=0
        assert result["usk_1"] == {"up": 2, "down": 1}
        assert result["tool_1"] == {"up": 1, "down": 0}
        assert cols["skill"].by_id["usk_1"]["stats"]["up"] == 2
        assert cols["tool"].by_id["tool_1"]["stats"]["up"] == 1
