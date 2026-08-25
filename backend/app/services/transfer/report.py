"""ImportReport 收集器 — dry_run 与真导入共用。"""
from __future__ import annotations

from app.schemas.transfer import (
    ImportErrorItem,
    ImportItem,
    ImportReport,
    ImportSummary,
    ImportWarningItem,
)


class ReportCollector:
    """收集导入过程中的 created / reused / skipped / warnings / errors。

    用法::

        report = ReportCollector(dry_run=True)
        report.add_created(kind="agent", name="客服助手", original_id=..., new_id=...)
        report.warning(kind="model", name="GLM-4.7", field="api_key", message="...")
        return report.to_report()
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.created: list[ImportItem] = []
        self.reused: list[ImportItem] = []
        self.skipped: list[ImportItem] = []
        self.warnings: list[ImportWarningItem] = []
        self.errors: list[ImportErrorItem] = []

    def add_created(
        self,
        *,
        kind: str,
        name: str,
        original_id: str,
        new_id: str = "",
        renamed_from: str = "",
    ) -> None:
        self.created.append(
            ImportItem(
                kind=kind,
                name=name,
                original_id=original_id,
                new_id=new_id,
                renamed_from=renamed_from,
            )
        )

    def add_reused(
        self, *, kind: str, name: str, original_id: str, existing_id: str
    ) -> None:
        self.reused.append(
            ImportItem(
                kind=kind,
                name=name,
                original_id=original_id,
                existing_id=existing_id,
            )
        )

    def add_skipped(self, *, kind: str, name: str, original_id: str = "") -> None:
        self.skipped.append(
            ImportItem(kind=kind, name=name, original_id=original_id)
        )

    def warning(
        self, *, kind: str = "", name: str = "", field: str = "", message: str
    ) -> None:
        self.warnings.append(
            ImportWarningItem(kind=kind, name=name, field=field, message=message)
        )

    def error(
        self, *, kind: str = "", name: str = "", message: str
    ) -> None:
        self.errors.append(
            ImportErrorItem(kind=kind, name=name, message=message)
        )

    def to_report(self) -> ImportReport:
        return ImportReport(
            dry_run=self.dry_run,
            summary=ImportSummary(
                created=len(self.created),
                reused=len(self.reused),
                skipped=len(self.skipped),
                warnings=len(self.warnings),
                errors=len(self.errors),
            ),
            created=self.created,
            reused=self.reused,
            skipped=self.skipped,
            warnings=self.warnings,
            errors=self.errors,
        )
