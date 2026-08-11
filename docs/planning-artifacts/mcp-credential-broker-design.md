# MCP 凭证绑定与兑换（User-MCP Credential Broker）设计

> 对应能力：终端用户身份由 MEPER 统一托管；用户在各 MCP 服务的凭证绑定、运行时兑换。
> 日期：2026-08-07
> 状态：方案已确认，待实施
> 关联文档：[`external-user-auth-design.md`](./external-user-auth-design.md)（本方案实施后**归档废弃**）、[`implementation-artifacts/5-3-mcp-connection-management.md`](../implementation-artifacts/5-3-mcp-connection-management.md)

---

## 0. 文档目的与范围

本文档定义 **MEPER 平台如何统一托管终端用户在各个 MCP 服务的身份凭证**，以及运行时 agent 调用 MCP 时如何**按当前用户兑换出正确的凭证**。

**适用读者**：
- MEPER 后端开发者（实施兑换器、token 管理、认证改造）
- 平台 admin（理解通用 token 的创建与下发流程）

**不包含**：部门/多租户隔离、MCP server 端的改造（本方案要求 MCP server **零改造**）、平台用户自身的登录（已有 JWT 体系）。

---

## 1. 背景与核心问题

### 1.1 现状链路的缺口

agent-flow 当前的 MCP 调用链路（`backend/packages/harness/src/agent_flow_harness/mcp/loader.py:196-212` 的 `_user_token_interceptor`）：

```
client ──API Key──> MEPER ──resolve MCP tools──> StructuredTool[]
                                                      │
              调用时 interceptor 直接透传 user_token（覆盖静态 auth_config）
                                                      ▼
                                                  MCP server
```

**缺口**：
1. **每个 MCP 服务有独立的用户管理系统，凭证互不通**。现有 interceptor 把 `X-User-Token` 原样透传给所有 MCP（`loader.py:211` 硬编码 `Authorization: Bearer {user_token}`），这个 token 只对一个 MCP 有效，调别的 MCP 必失败。
2. **终端用户身份依赖外部 introspection 回调**（`external-user-auth-design.md` 的 RFC 7662 模式），MEPER 不是身份的源，无法自主签发与验证终端用户身份。
3. **用户无法把"自己在各 MCP 的身份"托管给 MEPER**，MEPER 也就无从兑换。

### 1.2 目标（用户确认的模型）

1. **admin 在 MEPER 创建通用 token**：填用户名称 + 绑定 N 个 MCP；绑每个 MCP 时填入该 MCP 的 **token** 或 **用户名+密码**。
2. **外部用户拿通用 token 通过 client 调 agent**（client 仍带 API Key 作为接入方凭证）。
3. **agent 调 MCP 前，MEPER 拿通用 token 查绑定凭证，替换 MCP 调用的凭证**。
4. **内部测试（studio 后台，平台 JWT 路径）不经过兑换**，直接用 MCP connection 的静态凭证调，方便测试。
5. **MCP server 零改造**——仍认自己原来的 token / 账密。

### 1.3 立场转变

| | 旧（external-user-auth-design.md） | 新（本方案） |
|---|---|---|
| 终端用户身份源 | 接入方系统（MEPER 回调 introspection） | **MEPER 自己**（签发 + 本地校验通用 token） |
| X-User-Token 语义 | 接入方系统的用户 token | **MEPER 派发的通用 token** |
| MCP 调用凭证 | 透传同一个 user_token | **按目标 MCP 兑换该用户绑定的凭证** |

---

## 2. 架构总览

### 2.1 两条路径天然分流（关键设计）

兑换逻辑的触发条件是 **`token_record_id` ContextVar 是否有值**。内部路径不设它，天然免兑换；外部路径才设它、才走兑换。

```
【内部路径 — studio 后台测试】（现状不变，方便测试）

admin/developer ──平台 JWT──> /v1/agents/*/invoke
   → get_current_user（平台用户，security.py:170）
   → resolve_harness_context：不设 token_record_id ContextVar
   → interceptor：ContextVar 为空 → 不调兑换器 → 用 MCP connection 静态 auth_config 调 ✓

【外部路径 — client 调用】（新机制）

admin ──studio──> 创建 mcp_token_credentials 记录
                   (name + mcp_bindings + 通用 token = meper_xxx)
                                          │
用户 <──通用 token── (admin 发给用户)      │
   │                                      │
client ──Authorization: Bearer af_live_xxx ──────┐
       ──X-User-Token:  meper_xxx ───────────────┴──> /api/v1/ext/*
                                                          │
                       get_api_key_principal:             │   (auth_apikey.py:115)
                         ① API Key 验证 + scope + bindings ✓（接入方维度，不变）
                         ② X-User-Token 必填 → 本地查 mcp_token_credentials
                            命中 active → principal.user_id = 记录_id
                                          principal.token_record_id = 记录_id
                            缺失/未命中/禁用 → 401           ← 不再回调外部
                                                          │
                       resolve_harness_context:           │   (context.py:265)
                         set token_record_id ContextVar = 记录_id
                                                          │
agent 调 mcp__{server}__{tool}                             │
  ↓                                                       │
harness _user_token_interceptor:                          │   (loader.py:196)
  record_id = get_token_record_id_context()  ← 有值（外部路径）
  cred = await resolver.resolve(record_id, connection_config)
    ↓ (app 层 UserCredentialResolver)
    查 mcp_token_credentials.mcp_bindings[conn_id]
      token 型  → 解密直返
      账密型    → Redis 查 session；miss 则 POST login_url 换 session，缓存
  → 按 auth_type 构造 header → request.override(headers=...)   ← 覆盖静态凭证
  未绑定 → 抛错（外部路径不允许降级）
  ↓                                                       │
真正 MCP server（拿到的是该用户绑定的本系统真实凭证）
```

### 2.2 设计要点

- **通用 token 是查绑定的 key**：token → 记录 id → mcp_bindings，无中间 user_id 映射。
- **一张表**：`mcp_token_credentials`，admin 每创建一个通用 token = 一条记录，直接挂该用户的全部 MCP 绑定。
- **两类凭证形态**：token 型（直传）、账密型（调 MCP 的 login_url 换 session，缓存复用）。
- **harness 保持可独立发布**：通过 `CredentialResolver` Protocol 注入实现，harness 不依赖 app 的 DB 层。

---

## 3. 详细设计

### 3.1 数据模型：`mcp_token_credentials`（新建）

**集合名**：`mcp_token_credentials`

admin 每创建一个通用 token = 一条记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| `_id` | str | `mcptok_xxx`（ULID） |
| `name` | str | admin 填的用户名称（如"张三"） |
| `token` | str | MEPER 生成的通用 token，随机不透明串，`meper_` 前缀，**全局唯一索引** |
| `api_key_id` | str | 归属接入方 API Key（可选，用于隔离/审计） |
| `status` | str | `active` / `disabled` |
| `mcp_bindings` | dict | key = `mcp_connection_id`，value = 绑定凭证对象（见下） |
| `created_at` / `updated_at` | str | ISO 时间 |
| `created_by` | str | 创建该记录的平台 admin user_id |

**mcp_bindings 的两种形态**：

```jsonc
// token 型：用户填的是目标 MCP 的 token，运行时直传
{
  "credential_type": "token",
  "auth_type": "bearer",          // bearer / api_key / basic —— 决定怎么进 header
  "token": "enc:xxxxxx"           // enc: 前缀 AES-256-GCM 加密
}

// 账密型：用户填的是账密，运行时先 POST login_url 换 session
{
  "credential_type": "password",
  "auth_type": "bearer",          // 换来的 session 用什么形态注入
  "username": "enc:xxxxxx",
  "password": "enc:xxxxxx"
}
```

**加密**：凭证值统一用 `enc:` 前缀 + AES-256-GCM（复用 `app/core/crypto.py` 的 `encrypt_secret` / `decrypt_secret`）。这套 `enc:` 前缀模式参考自 `backend/app/engine/harness_integration/context.py:46-67` 的 `_decrypt_user_args`，是项目里最成熟的"加密存 + 运行时解密注入"套路。

**`auth_type` 的作用**：决定兑换出的凭证怎么放进 HTTP 头，复用 `backend/app/engine/tool/mcp_client.py` 的 `_build_headers` 已有逻辑（bearer → `Authorization: Bearer`，api_key → `X-API-Key`，basic → `Authorization: Basic`）。

**唯一约束**：
- `token` 全局唯一（数据库索引）
- `(记录_id, mcp_connection_id)` 隐含在 dict key 唯一，即"一个用户对一个 MCP 只绑一份凭证"

### 3.2 MCP connection 扩展：`login_config`

**改动文件**：`backend/app/models/mcp_connection.py`

`McpConnection` 新增可选字段 `login_config`（仅账密型绑定用到，token 型不用）：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `login_url` | — | 账密型 MCP 的登录端点 URL |
| `method` | `"POST"` | 登录请求方法 |
| `body_template` | — | 请求体模板，支持 `{{username}}` / `{{password}}` 占位 |
| `token_jsonpath` | — | 从登录响应 JSON 取 session token 的路径，如 `data.access_token` |
| `session_ttl` | `3600` | session 缓存秒数 |

示例配置（某账密型 MCP）：
```jsonc
{
  "login_url": "https://oa.example.com/api/login",
  "method": "POST",
  "body_template": "{\"username\":\"{{username}}\",\"password\":\"{{password}}\"}",
  "token_jsonpath": "data.token",
  "session_ttl": 7200
}
```

### 3.3 终端用户认证改造（外部路径，删 + 改）

> **重要**：本节改动**只影响 `/api/v1/ext/*` 外部路径**。内部路径 `/v1/agents/*` 走 `get_current_user`（平台 JWT，`security.py:170`），完全不碰这套，自然不受影响。

#### (a) 废弃外部 introspection 回调

| 要废弃的代码 | 位置 | 处理 |
|---|---|---|
| `ApiKey.user_info_url` 字段 | `models/api_key.py:57-64` | 删除字段（破坏性，需数据迁移说明） |
| `UserAuthService.introspect` | `services/user_auth_service.py` | 整文件废弃 |
| introspection 缓存层 | `core/introspection_cache.py` | 整文件废弃 |
| `is_introspect_stale` / `user_auth_state` | `core/user_auth_state.py` | 废弃（`ext/__init__.py:187` 的 stale header 逻辑一并删） |
| `visitor_id` 兼容模式 | `ext/__init__.py:24-40` `resolve_user_id` 的 legacy 分支 | 删除 |
| 设计文档 | `external-user-auth-design.md` | 标记 deprecated 并归档 |

#### (b) 改造 `get_api_key_principal`（`backend/app/core/auth_apikey.py:115`）

**保留**（接入方维度不变）：
- API Key 验证（`af_live_` 前缀 + bcrypt 比对，`auth_apikey.py:141-152`）
- `scopes` / `bindings` 资源白名单（`ApiKeyPrincipal:47-83`）

**改造**（X-User-Token 处理，`auth_apikey.py:163-184`）：

```python
# 原：根据 user_info_url 决定走 legacy / 回调验证
# 新：X-User-Token 必填，本地校验通用 token
user_token = _extract_bearer_token(request.headers.get("X-User-Token"))
if not user_token:
    raise UnauthorizedError(
        code="EXT_USER_TOKEN_MISSING",
        message="X-User-Token header is required.",
    )

# 本地查 mcp_token_credentials（替代外部 introspection 回调）
record = await McpTokenCredentialService.verify_token(user_token)
if record is None:
    raise UnauthorizedError(
        code="EXT_USER_TOKEN_INVALID",
        message="User token is invalid, expired, or revoked.",
    )

principal.user_id = record["_id"]              # 记录 id 当 user_id
principal.token_record_id = record["_id"]      # 兑换器查绑定的 key
principal.user_token = user_token              # 保留（标识用途）
```

`ApiKeyPrincipal`（`auth_apikey.py:21`）新增字段：
```python
token_record_id: str | None = None   # MCP 兑换器查绑定的 key
```
并移除 `user_info_url` 字段（随 ApiKey 模型一起删）。

#### (c) `resolve_user_id` 简化（`ext/__init__.py:24-40`）

删掉 legacy 分支，统一返回 `principal.user_id`：
```python
def resolve_user_id(principal, visitor_id=None) -> str:
    return principal.user_id  # 即 mcp_token_credentials._id
```
`visitor_id` 参数保留签名但不再使用（过渡期），后续可彻底移除。

### 3.4 MCP 凭证兑换器（核心）

#### (a) harness 新建 `CredentialResolver` Protocol

**新文件**：`backend/packages/harness/src/agent_flow_harness/mcp/credential_resolver.py`

```python
from typing import Any, Protocol

class CredentialResolver(Protocol):
    """凭证解析器接口 —— 由 app 层实现并注入。

    harness 调 MCP 工具前调 resolve()，拿当前用户在该 MCP 的绑定凭证。
    返回 None 表示该用户未绑定该 MCP（由 interceptor 决定降级/报错）。
    """

    async def resolve(
        self,
        token_record_id: str,        # mcp_token_credentials._id
        connection_config: dict[str, Any],  # 目标 MCP connection 配置（含 name/url/login_config 等）
    ) -> dict[str, Any] | None:
        """返回 {"auth_type": ..., **凭证字段} 如：
        - {"auth_type":"bearer","token":"xxx"}
        - {"auth_type":"basic","username":"u","password":"p"}
        返回 None = 未绑定。"""
        ...
```

**harness 不 import 任何 app 模块**，保持可独立发布。app 层实现此接口并注入。

#### (b) harness 改造 `_user_token_interceptor`（`loader.py:196`）

**新增 ContextVar**：`backend/packages/harness/src/agent_flow_harness/mcp/user_token_context.py` 新增 `token_record_id`：

```python
_token_record_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_token_record_id", default=None
)
def set_token_record_id_context(record_id): return _token_record_id_ctx.set(record_id)
def reset_token_record_id_context(token): _token_record_id_ctx.reset(token)
def get_token_record_id_context() -> str | None: return _token_record_id_ctx.get()
```

**模块级注入点**（`loader.py`）：
```python
_resolver: CredentialResolver | None = None

def set_credential_resolver(r: CredentialResolver) -> None:
    global _resolver
    _resolver = r
```

**改造 interceptor**（`loader.py:196-212`）—— 关键的分流逻辑：

```python
async def _user_token_interceptor(request, handler):
    record_id = get_token_record_id_context()

    # ① 内部路径（record_id 为空）：不介入，透传，用 connection 静态 auth_config
    if not record_id or _resolver is None:
        return await handler(request)

    # ② 外部路径（record_id 有值）：兑换该用户绑定的凭证
    connection_config = _extract_connection_config(request)  # 从 request/工具名提取
    cred = await _resolver.resolve(record_id, connection_config)

    if cred is None:
        # 未绑定 → 报错（外部路径不允许降级，区别于内部路径）
        raise PermissionError(
            f"用户未绑定该 MCP 服务({connection_config.get('name')})，"
            f"请联系 admin 绑定凭证。"
        )

    # 按 auth_type 构造 header（复用 _build_auth_headers 逻辑）
    headers = _cred_to_headers(cred)
    overridden = request.override(headers=headers)
    return await handler(overridden)
```

`_cred_to_headers` 复用 `loader.py:160-178` 的 `_build_auth_headers` 已有逻辑（bearer/api_key/basic 三种）。

#### (c) app 新建 `UserCredentialResolver`

**新文件**：`backend/app/engine/mcp/user_credential_resolver.py`

```python
class UserCredentialResolver:
    """app 层实现：查 mcp_token_credentials，解密，账密型换 session。"""

    async def resolve(self, token_record_id, connection_config) -> dict | None:
        # 1. 查记录
        record = await McpTokenCredentialService.get_record(token_record_id)
        if not record:
            return None
        conn_id = connection_config["id"]
        binding = record["mcp_bindings"].get(conn_id)
        if not binding:
            return None  # 未绑定该 MCP

        # 2. 按形态兑换
        if binding["credential_type"] == "token":
            return self._resolve_token(binding)           # 解密直返
        elif binding["credential_type"] == "password":
            return await self._resolve_password(          # 换 session（带缓存）
                token_record_id, conn_id, binding, connection_config
            )
```

**`_resolve_password` 逻辑**（账密型，带 Redis 缓存）：
```
1. cache_key = f"mcp:session:{record_id}:{conn_id}"
2. cached = redis.get(cache_key)；命中 → 返回 {"auth_type":..., "token":cached}
3. miss → 取 connection.login_config + 解密后的 username/password
   → POST login_url（body_template 渲染占位）
   → 按 token_jsonpath 从响应取 session token
   → redis.setex(cache_key, session_ttl, session_token)
   → 返回 {"auth_type":..., "token":session_token}
4. 登录失败 → 透传错误给用户（"MCP 登录失败：<目标系统返回>"）
```

**启动注入**（`app/main.py` 或 `context.py` 初始化处）：
```python
from agent_flow_harness.mcp.loader import set_credential_resolver
from app.engine.mcp.user_credential_resolver import UserCredentialResolver

set_credential_resolver(UserCredentialResolver())  # 应用启动调用一次
```

#### (d) `resolve_harness_context` 改造（`context.py:265-`）

现状（`context.py:400-402`）：`set_user_token_context(user_token)`。

改造：外部路径额外 set `token_record_id`；内部路径（无 user_token）不 set：

```python
if user_token:
    set_user_token_context(user_token)
    # user_id 在外部路径 = principal.user_id = mcp_token_credentials._id
    set_token_record_id_context(state.get("user_id"))
# 内部路径：user_token 为 None，不 set record_id → interceptor 自动降级静态凭证
```

### 3.5 Workflow 路径打通（断点 A/B/C）

当前 Workflow 触发路径有三处断点（详见探索结论）：

| 断点 | 位置 | 问题 | 修复 |
|---|---|---|---|
| **A** | `ext/workflows.py:168` + `task_service.py` | 外部触发 Workflow 时 `created_by = owner_user_id`，丢弃了 user_token / 记录 id | 把 `principal.user_token` + `principal.token_record_id` 透传到 task 文档 → Celery worker |
| **B** | `node_executor.py:427` AgentNodeExecutor | 调 `invoke()` 时没传 user_token（对比 Agent 会话路径 `ext/agents.py:156` 有传） | 从 task 取出 user_token + record_id，传入 `invoke` / `stream` |
| **C** | `mcp_tool_cache.py` vs `loader.py` | app 版 `mcp_tool_cache.py`（Workflow 用）无 interceptor；harness 版 `loader.py`（Agent 会话用）有 | **废弃 `mcp_tool_cache.py`**，`context.py:_resolve_mcp_tools` 改走 harness `McpToolLoader`，统一两套实现 |

**内部触发的 Workflow**（studio 测试）：task 无 user_token → 不 set ContextVar → 走静态凭证，与内部 Agent 测试一致。

### 3.6 admin 管理 API（新建）

**路由**：`/api/v1/mcp-tokens`，平台 JWT 鉴权（`require_permission`，admin only）。

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/mcp-tokens` | 创建（name + mcp_bindings），**通用 token 仅此一次明文返回** |
| `GET` | `/mcp-tokens` | 列表（分页，凭证字段 mask `***`） |
| `GET` | `/mcp-tokens/{id}` | 详情（凭证 mask） |
| `PUT` | `/mcp-tokens/{id}` | 改 name / status / 增删改 mcp_bindings |
| `DELETE` | `/mcp-tokens/{id}` | 吊销删除 |
| `POST` | `/mcp-tokens/{id}/rotate` | 轮换 token 值（旧 token 立即失效） |

**凭证 mask**：响应里 `enc:` 字段返回 `***`（参考 `mcp_connection_service.py` 的 `_mask_auth_config`）。

**副作用**：改绑 / 轮换 token / 禁用记录时，清相关缓存：
- Redis `mcp:session:{record_id}:{conn_id}`（账密型 session）
- MCP 工具缓存（`loader.py` 的 `McpToolLoader.invalidate`）

---

## 4. 复用 / 新建 / 废弃 总表

| 能力 | 现状 | 处理 |
|---|---|---|
| 内部路径 `/v1/agents/*` + 平台 JWT | `get_current_user` | **完全不变**（studio 测试不受影响） |
| 外部路径 `/ext/*` + API Key + scope + bindings | `get_api_key_principal` | **复用**（接入方维度不变） |
| MCP header 构造（bearer/api_key/basic） | `mcp_client.py:_build_headers` | **复用** |
| 凭证加解密 | `app/core/crypto.py` + `enc:` 前缀模式 | **复用** |
| user_token → MCP 管道（ContextVar + interceptor） | `user_token_context.py` + `loader.py:196` | **复用管道，改 interceptor**（加 record_id 分流） |
| 外部 introspection 回调 | `user_info_url` / `UserAuthService` / `introspection_cache` | **废弃** |
| `visitor_id` 兼容模式 | `resolve_user_id` legacy 分支 | **废弃** |
| (用户 × MCP) 绑定存储 | 不存在 | **新建** `mcp_token_credentials` |
| 通用 token 签发 + 本地校验 | 不存在 | **新建** |
| 账密登录换 session | 不存在 | **新建**（`login_config` + Redis 缓存） |
| admin token 管理 API | 不存在 | **新建** `/v1/mcp-tokens` |
| 两套 MCP 加载实现 | app 版 `mcp_tool_cache.py` / harness 版 `loader.py` 分裂 | **统一到 harness 版** |

---

## 5. 分阶段任务拆解

### 阶段 1：终端用户认证改造 + token 型兑换（外部 `/ext` 路径）

1. **新建 `mcp_token_credentials` 模型 + service**
   - 模型 `models/mcp_token_credential.py`、service `services/mcp_token_credential_service.py`
   - 实现：CRUD、凭证加解密（`enc:`）、mask、通用 token 生成（`secrets.token_urlsafe` + `meper_` 前缀）、`verify_token(token) -> record | None`（本地校验）
2. **新建 admin API `/v1/mcp-tokens`**（含 mask、rotate）
3. **改造 `get_api_key_principal`**（`auth_apikey.py:163-184`）：删 introspection 回调分支，改本地查 token
4. **`resolve_user_id` 删 legacy 分支**（`ext/__init__.py:24-40`）
5. **删/废弃**：`user_info_url` 字段、`UserAuthService.introspect`、`introspection_cache`、`user_auth_state`、`visitor_id` 分支
6. **harness 新建 `CredentialResolver` Protocol**（`credential_resolver.py`）
7. **harness 改造 `_user_token_interceptor`**（`loader.py:196`）：加 `token_record_id` ContextVar + 分流逻辑 + `set_credential_resolver` 注入点
8. **harness 新增 `token_record_id` ContextVar**（`user_token_context.py`）
9. **app 新建 `UserCredentialResolver`（token 型）**（`engine/mcp/user_credential_resolver.py`）+ 启动注入
10. **`resolve_harness_context` 改造**（`context.py:400`）：外部路径 set `token_record_id`
11. **归档 `external-user-auth-design.md`**
12. **验收 1（内部）**：studio 后台测 agent 调 MCP → 仍用静态 auth_config，行为不变 ✓
13. **验收 2（外部）**：admin 建 token + 绑 token 型 MCP → 用户用 token 调 agent → MCP 收到的是绑定 token；调未绑定的 MCP → 报错提示绑定 ✓

### 阶段 2：账密型 + session 缓存

14. **`McpConnection` 加 `login_config` 字段**（`models/mcp_connection.py`）+ studio UI 配置入口
15. **`UserCredentialResolver` 加账密分支**：Redis session 缓存 + POST login_url + token_jsonpath 提取 + 错误透传
16. **验收**：admin 绑账密型 MCP → 用户调 agent → 自动登录换 session → MCP 调通；session 过期后自动重登 ✓

### 阶段 3：Workflow 路径 + 实现统一

17. **废弃 `mcp_tool_cache.py`**，`context.py:_resolve_mcp_tools` 改走 harness `McpToolLoader`（统一 + 让 Workflow 支持兑换）
18. **修断点 A**：`ext/workflows.py` + `task_service.py` 把 user_token + token_record_id 透传到 task → worker
19. **修断点 B**：`node_executor.py:427` AgentNodeExecutor 从 task 取 user_token 传入 `invoke`/`stream`
20. **验收**：外部触发 Workflow → 用绑定凭证调 MCP；内部触发 Workflow → 用静态凭证 ✓

### 阶段 4（前端，可选）

21. studio token 管理 UI（admin 创建/绑定/轮换）
22. client 端通用 token 填写

---

## 6. 默认决策（可调整）

| 决策点 | 选择 | 理由 |
|---|---|---|
| 通用 token 形态 | 随机不透明串 + 本地校验 | 符合"身份标识"定位；不走 JWT，无需密钥分发；DB 查询可接受（可加进程内短 TTL 缓存） |
| 兑换触发条件 | `token_record_id` ContextVar 有值 | 内部路径天然不触发，无需给 agent 打"内部/外部"标签 |
| 外部路径未绑定 | 报错（不降级） | 外部用户必须显式绑定；区别于内部路径的宽松 |
| 内部路径凭证 | 永远用 connection 静态 auth_config | 测试体验不变 |
| session 缓存 | Redis，key=`mcp:session:{record_id}:{conn_id}` | 避免每次调用都重登；TTL 从 `login_config.session_ttl` |
| 一张表 vs 两张表 | 一张表 `mcp_token_credentials` | 通用 token 直接挂绑定，无中间映射，符合需求 |
| 部门/租户 | 不涉及 | 本方案不含 |

---

## 7. 风险与权衡

### 7.1 破坏性变更与迁移
- **废弃 introspection 回调是破坏性的**。现有依赖 `user_info_url` 的接入方必须迁移到通用 token 模式（admin 为其终端用户创建 `mcp_token_credentials` 记录并下发 token）。
- **迁移说明**（写入实施文档）：对每个现有 `api_keys.user_info_url` 非空的接入方，admin 需为其终端用户在 MEPER 建通用 token 记录，绑定对应 MCP 凭证，把下发的 `meper_xxx` token 交给前端填入 `X-User-Token`。

### 7.2 安全
- 凭证 AES-256-GCM 加密存储（`enc:` 前缀），运行时仅在内存，**不入日志**（参考 `user_token_context.py` 的注释原则）。
- 通用 token 仅创建时明文返回一次，后续只存哈希/只比对原文（实施时决定：存 token 原文用于本地校验，还是存哈希 + 校验时比对——倾向存原文因为要当查询 key，但 DB 访问受限环境需评估）。
- 支持轮换（`/rotate`，旧 token 立即失效）与吊销（`DELETE` / `status=disabled`）。

### 7.3 向后兼容
- **现有 MCP connection 零改造**：内部路径仍用静态 `auth_config`。
- **现有外部 introspection 接入方**：需迁移（见 7.1），但有明确路径。

### 7.4 harness 独立性
- 通过 `CredentialResolver` Protocol 注入，harness 不 import 任何 app 模块，**保持可独立发布**。

### 7.5 session 缓存一致性
- 账密改绑、`login_config` 变更、token 轮换/禁用时，必须清相关 Redis session 缓存（service 层在 update/delete/rotate 时联动）。

### 7.6 interceptor 拿目标 connection 标识（实施时首步验证）
- `_user_token_interceptor` 需从 `request` 提取目标 MCP 的 connection 标识（server name / id）才能查绑定。
- **待验证**：`langchain-mcp-adapters` 的 interceptor `request` 对象能否暴露当前工具名 / 连接名。
- **降级方案**：若拿不到，则把 `connection_config`（含 id/name/login_config）在工具加载时绑定进 resolver 的调用上下文，或通过额外 ContextVar 传递当前 conn_id。

### 7.7 性能
- **外部路径**：每次 MCP 调用 +1 次 DB 查询（绑定）+ 可能 +1 次 Redis 查询（账密型 session）。
- **内部路径**：零额外开销。
- 可优化：进程内短 TTL 缓存绑定查询结果（key=`record_id:conn_id`，TTL 如 30s，改绑时主动清）。

---

## 8. 待实施时确认的技术细节（不阻塞本方案）

1. `langchain-mcp-adapters` interceptor request 结构（见 7.6）。
2. Workflow task 里存 `user_token` / `token_record_id` 的安全考量：task 文档是否需要加密该字段，还是只在执行期内存传递（倾向后者：存到 task 的 `ext_metadata`，执行完即用即弃）。
3. 通用 token 在 `mcp_token_credentials` 是存原文还是哈希：存原文便于当查询 key，但泄露风险更高；存哈希则需额外的"token → record_id"索引。实施时权衡。
4. `server_name` 与 `mcp_connection_id` 的关系：interceptor 拿到的是工具名 `mcp__{server}__{tool}` 里的 server（= connection name），而绑定 key 是 `mcp_connection_id`。需在 connection 加载时建立 name → id 的映射，或绑定也按 name 存（实施时定）。
