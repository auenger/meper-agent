"""Application Pydantic schemas for API request/response."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# login_config 允许的 HTTP method
_ALLOWED_METHODS = {"GET", "POST", "PUT"}

# login_url 必须是 http/https URL
_URL_PATTERN = re.compile(r"^https?://[^\s]+$")

# 字段名（请求体的 key）合法字符：字母数字下划线，1-50 字符
_FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,50}$")

# jsonpath 简单校验：非空点分路径（字母数字下划线 + 点），1-200 字符
_JSONPATH_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,50}(\.[A-Za-z0-9_]{1,50})*$")


class ApplicationBase(BaseModel):
    """Base fields for application create/update."""

    name: str = Field(..., min_length=1, max_length=100, description="应用名称")
    description: str = Field(default="", max_length=500, description="应用描述")
    mcp_connection_ids: list[str] = Field(
        default_factory=list,
        description="绑定的 MCP 连接 ID 列表",
    )
    login_config: dict = Field(
        default_factory=dict,
        description=(
            "账密验证配置。空 dict = 未配置（用户不可授权）。"
            "非空时必须含合法的 login_url（http/https）。"
            "可选：method(POST/GET/PUT，默认POST)/username_field(默认username)/"
            "password_field(默认password)/token_jsonpath(默认data.token)/"
            "session_ttl(60-86400秒，默认3600)。"
        ),
    )

    @field_validator("mcp_connection_ids")
    @classmethod
    def _validate_mcp_ids(cls, v: list[str]) -> list[str]:
        """去重 + 单项格式检查（存在性在 service 层查库验证）。"""
        seen: set[str] = set()
        result: list[str] = []
        for item in v:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("mcp_connection_ids 含空值")
            item = item.strip()
            if item in seen:
                continue  # 静默去重
            seen.add(item)
            result.append(item)
        return result

    @field_validator("login_config")
    @classmethod
    def _validate_login_config(cls, v: dict) -> dict:
        """空 dict 合法（未配置）；非空时校验内部结构。"""
        if not v:
            return v

        # login_url：必填，http/https
        login_url = v.get("login_url")
        if not isinstance(login_url, str) or not _URL_PATTERN.match(login_url):
            raise ValueError(
                "login_config.login_url 必填，且必须是合法的 http/https URL"
            )
        if len(login_url) > 500:
            raise ValueError("login_config.login_url 长度不能超过 500")

        # method：可选，默认 POST
        method = v.get("method", "POST")
        if not isinstance(method, str) or method.upper() not in _ALLOWED_METHODS:
            raise ValueError(f"login_config.method 只允许 {sorted(_ALLOWED_METHODS)}")

        # 字段名：可选，默认 username/password
        for field_key, default in (("username_field", "username"), ("password_field", "password")):
            val = v.get(field_key, default)
            if not isinstance(val, str) or not _FIELD_NAME_PATTERN.match(val):
                raise ValueError(
                    f"login_config.{field_key} 只允许字母数字下划线，1-50 字符"
                )

        # token_jsonpath：可选，默认 data.token
        jsonpath = v.get("token_jsonpath", "data.token")
        if not isinstance(jsonpath, str) or not _JSONPATH_PATTERN.match(jsonpath):
            raise ValueError(
                "login_config.token_jsonpath 必须是点分路径（如 data.token）"
            )

        # session_ttl：可选，默认 3600，范围 60-86400
        ttl = v.get("session_ttl", 3600)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not (60 <= ttl <= 86400):
            raise ValueError("login_config.session_ttl 必须是 60-86400 之间的整数（秒）")

        return v


class ApplicationCreate(ApplicationBase):
    """Schema for creating a new application."""


class ApplicationUpdate(ApplicationBase):
    """Schema for updating an existing application (full PUT)."""


class ApplicationResponse(BaseModel):
    """Application data returned in API responses."""

    id: str
    name: str
    description: str
    mcp_connection_ids: list[str] = Field(default_factory=list)
    login_config: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ApplicationListResponse(BaseModel):
    """Application list response (unpaginated — applications are few)."""

    items: list[ApplicationResponse]
    total: int
