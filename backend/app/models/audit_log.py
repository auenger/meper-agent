"""Audit log model — append-only immutable event records."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.base import generate_id, utc_now


class AuditLog(BaseModel):
    """Append-only audit log entry for Task state changes and interventions."""

    id: str = Field(default_factory=lambda: generate_id("audit"), alias="_id")
    task_id: str = Field(..., max_length=100)
    event_type: str = Field(..., max_length=50)  # state_change, intervention, human_action
    from_status: str | None = None
    to_status: str | None = None
    action: str | None = None  # approve, reject, skip, cancel, pause, resume, etc.
    triggered_by: str = "system"
    triggered_by_type: str = Field(default="system", pattern=r"^(user|agent|system|api_key)$")
    version: int = 0
    details: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)

    model_config = {"populate_by_name": True}
