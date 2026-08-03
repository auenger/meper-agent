"""Tests for FileRef and FileUsage data models."""
import pytest
from app.models.file_library import FileConsumerKind, FileRef, FileUsage


class TestFileConsumerKind:
    """FileConsumerKind 枚举测试。"""

    def test_enum_has_all_expected_values(self) -> None:
        """枚举包含所有预期的值。"""
        assert FileConsumerKind.USER_LIBRARY == "user_library"
        assert FileConsumerKind.SESSION_MESSAGE == "session_message"
        assert FileConsumerKind.WORKFLOW_RUN == "workflow_run"
        assert FileConsumerKind.CRON_JOB == "cron_job"
        assert FileConsumerKind.KNOWLEDGE_BASE == "knowledge_base"

    def test_enum_has_exactly_five_values(self) -> None:
        """枚举恰好有 5 个值。"""
        assert len(FileConsumerKind) == 5

    def test_enum_values_are_strings(self) -> None:
        """枚举值都是字符串。"""
        for kind in FileConsumerKind:
            assert isinstance(kind.value, str)


class TestFileRef:
    """FileRef 模型测试。"""

    def test_file_ref_with_all_required_fields(self) -> None:
        """提供所有必填字段时创建成功。"""
        file_ref = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="report.xlsx",
            size=102400,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            sha256="abc123def456",
            origin_kind=FileConsumerKind.SESSION_MESSAGE,
            origin_id="msg_01DEF",
        )
        assert file_ref.owner_user_id == "user_01HXYZ"
        assert file_ref.storage_key == "user_01HXYZ/files/file_01ABC"
        assert file_ref.name == "report.xlsx"
        assert file_ref.size == 102400
        assert file_ref.sha256 == "abc123def456"
        assert file_ref.origin_kind == FileConsumerKind.SESSION_MESSAGE
        assert file_ref.origin_id == "msg_01DEF"

    def test_file_ref_generates_id_automatically(self) -> None:
        """ID 自动生成。"""
        file_ref = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="test.pdf",
            size=5000,
            sha256="sha256hash",
            origin_kind=FileConsumerKind.USER_LIBRARY,
            origin_id="user_01HXYZ",
        )
        assert file_ref.id.startswith("file_")
        assert len(file_ref.id) > 10

    def test_file_ref_alias_mapping(self) -> None:
        """id 字段映射到 _id（MongoDB 兼容）。"""
        file_ref = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="test.txt",
            size=100,
            sha256="hash",
            origin_kind=FileConsumerKind.WORKFLOW_RUN,
            origin_id="run_01GHI",
        )
        data = file_ref.model_dump(by_alias=True)
        assert "_id" in data
        assert data["_id"] == file_ref.id

    def test_file_ref_default_values(self) -> None:
        """默认值正确设置。"""
        file_ref = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="doc.pdf",
            size=2000,
            sha256="sha256",
            origin_kind=FileConsumerKind.CRON_JOB,
            origin_id="cron_01JKL",
        )
        assert file_ref.mime_type == "application/octet-stream"
        assert file_ref.status == "active"
        assert file_ref.created_at  # 非空
        assert file_ref.updated_at  # 非空

    def test_file_ref_size_must_be_non_negative(self) -> None:
        """size 必须非负。"""
        with pytest.raises(ValueError):
            FileRef(
                owner_user_id="user_01HXYZ",
                storage_key="user_01HXYZ/files/file_01ABC",
                name="test.txt",
                size=-1,
                sha256="hash",
                origin_kind=FileConsumerKind.USER_LIBRARY,
                origin_id="user_01HXYZ",
            )

    def test_file_ref_status_can_be_trashed(self) -> None:
        """status 可以设置为 trashed。"""
        file_ref = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="old.txt",
            size=500,
            sha256="hash",
            origin_kind=FileConsumerKind.SESSION_MESSAGE,
            origin_id="msg_01MNO",
            status="trashed",
        )
        assert file_ref.status == "trashed"

    def test_file_ref_serialization_roundtrip(self) -> None:
        """序列化/反序列化往返正确。"""
        original = FileRef(
            owner_user_id="user_01HXYZ",
            storage_key="user_01HXYZ/files/file_01ABC",
            name="data.csv",
            size=10000,
            mime_type="text/csv",
            sha256="csvhash",
            origin_kind=FileConsumerKind.WORKFLOW_RUN,
            origin_id="run_01PQR",
        )
        data = original.model_dump(by_alias=True)
        restored = FileRef(**data)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.size == original.size


class TestFileUsage:
    """FileUsage 模型测试。"""

    def test_file_usage_with_required_fields(self) -> None:
        """提供必填字段时创建成功。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.SESSION_MESSAGE,
            consumer_id="msg_01DEF",
        )
        assert usage.file_id == "file_01ABC"
        assert usage.consumer_kind == FileConsumerKind.SESSION_MESSAGE
        assert usage.consumer_id == "msg_01DEF"

    def test_file_usage_generates_id_automatically(self) -> None:
        """ID 自动生成。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.WORKFLOW_RUN,
            consumer_id="run_01GHI",
        )
        assert usage.id.startswith("fu_")
        assert len(usage.id) > 10

    def test_file_usage_default_granted_at(self) -> None:
        """granted_at 自动生成。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.CRON_JOB,
            consumer_id="cron_01JKL",
        )
        assert usage.granted_at  # 非空

    def test_file_usage_expires_at_optional(self) -> None:
        """expires_at 可选（None 表示长期持有）。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.USER_LIBRARY,
            consumer_id="user_01HXYZ",
            expires_at=None,
        )
        assert usage.expires_at is None

    def test_file_usage_with_expiration(self) -> None:
        """可以设置过期时间。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.WORKFLOW_RUN,
            consumer_id="run_01MNO",
            expires_at="2026-12-31T23:59:59+00:00",
        )
        assert usage.expires_at == "2026-12-31T23:59:59+00:00"

    def test_file_usage_alias_mapping(self) -> None:
        """id 字段映射到 _id。"""
        usage = FileUsage(
            file_id="file_01ABC",
            consumer_kind=FileConsumerKind.SESSION_MESSAGE,
            consumer_id="msg_01PQR",
        )
        data = usage.model_dump(by_alias=True)
        assert "_id" in data
        assert data["_id"] == usage.id
