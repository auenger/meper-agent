# AGENTS.md

AI Agent 编排与自动化平台（Agent Flow）。通用行为准则见根目录 `CLAUDE.md`（先思考再写码、最小改动、目标驱动），同样适用。

## 目录结构

```
backend/                  FastAPI + LangGraph + MongoDB(Motor) + Celery
  app/api/v1/             REST 路由（ext/ 子目录为外部终端用户 API）
  app/services/           业务逻辑层（api 层不直接操作 db）
  app/engine/             Agent 引擎：agent/(prompt+工具) workflow/(自研 DAG) kb/ tool/
  app/engine/harness_integration/  app ↔ harness 适配层（上下文装配/执行适配）
  app/workers/            Celery 任务（工作流执行、human 节点超时扫描）
  packages/harness/       agent-flow-harness：可独立发布的 LangGraph 引擎库
                          （editable 安装，backend 改它即时生效；有自己的 tests/）
frontend/                 管理端前端（原始）
frontend-studio/          Studio 前端——当前活跃开发（工作流编辑器
                          src/features/workflow-editor/ 在此维护，React Flow 画布）
frontend-client/          终端用户对话客户端（antd + @ant-design/x）
deploy/                   docker-compose + Caddy + sandbox 镜像
docs/                     项目文档（见下方"先读文档"）
```

## 常用命令

```bash
make install              # 全部依赖（backend 用 uv，前端用 npm）
make dev-local            # 本地起全套：FastAPI:8000 + Celery + 前端
cd backend && uv run pytest tests/engine/workflow -p no:warnings   # 后端测试（按目录聚焦）
cd backend && uv run ruff check . && uv run mypy app               # 后端 lint/类型
cd frontend-studio && npm run lint   # = tsc --noEmit（client 同理，build 也是 tsc 前置）
make build-sandbox        # 构建沙盒镜像；不构建则 bash 静默回退 subprocess 执行
make generate-api         # 从 OpenAPI 生成前端类型（改 API 后跑）
```

注意：`Makefile`/根 `README` 中部分 `frontend` 引用早于 `frontend-studio` 的引入，以三个包各自的 `package.json` 为准。

## 架构边界与关键规则

- **分层**：`api → services → engine/db`。engine 内 `harness_integration/context.py` 是工具解析单一事实源（`_INJECTED_BUILTIN_TOOL_NAMES` 与端点 `/api/v1/tools/builtin` 共用，防双路径漂移）。
- **双引擎**：工作流是自研 DAG（`app/engine/workflow/engine.py`，节点执行器在 `node_executor.py`）；每个 agent 节点内部跑 harness 的 LangGraph REACT 循环。
- **执行上下文**：`execution_context="chat" | "workflow"`（`invoke`/`resolve_harness_context`/`build_tool_declaration` 全链路参数）。workflow = 无人值守：剥离 `ask_clarification` 与整组 `_TASK_TOOLS`，注入 `abort_workflow` 诚实终止；prompt 用 5 卡槽渲染（`slot_renderer.py`），工具声明必须与运行时工具集一致。
- **暂停语义**：human 审核节点是工作流唯一合法暂停点（`WorkflowPausedError` → WAITING_HUMAN → intervene 恢复）。LangGraph interrupt 在 agent 节点中仅 `{"reason":"cancelled"}` 视为可恢复取消，其他 HITL interrupt 按节点失败处理。
- **harness 包改动**：跑 `backend/packages/harness/tests/`，它是可独立发布库，不要在 harness 里 import `app.*`。

## 约定

- 后端：ruff line-length 88、mypy、pytest `asyncio_mode=auto`（无需 `@pytest.mark.asyncio`）；日志用 loguru 结构化事件名（如 `node_agent_start`）；代码注释中英混合，跟随所在文件风格。
- 后端测试不依赖真实 MongoDB/Redis（conftest 设 env + Celery eager），但部分路径会真连 Mongo（motor）——新测试 mock 掉 DB 访问。
- 前端：lint 即 `tsc --noEmit`；studio 工作流编辑器节点配置面板在 `node-config-panels/`，新节点类型需同步 `utils/node-type-configs.ts` + `utils/node-defaults.ts`。

## 改敏感区域前先读

- `docs/PERMISSIONS.md` — 前后端权限对接唯一规范（**新增任何模块/页面前必读**；判 permission 不判 role）
- `docs/workflow-validator.md` — 工作流校验规则
- `backend/packages/harness/README.md` — harness 库结构
