"""WorkflowEngine — DAG-based workflow executor.

Handles:
- Loading workflow definition (nodes + edges)
- DAG topological sort and execution order
- Node executor dispatch (Strategy pattern)
- Gateway conditional branching
- Parallel branch execution
- Variable pool management
- Task state transitions and timeline events
- Checkpoint save/restore for Human node pause/resume
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from loguru import logger

from app.engine.workflow.expression import ExpressionEngine
from app.engine.workflow.node_executor import NodeResult, get_node_executor
from app.engine.workflow.variable_pool import VariablePool
from app.models.task import Checkpoint, TaskStatus, utc_now
from app.services.task_service import TaskService


class WorkflowEngine:
    """Core workflow execution engine.

    Usage::

        engine = WorkflowEngine()
        result = await engine.execute_task(task_doc, workflow_doc)

    Story 4-15: Task-level context (task_id, user_id, workflow_id) is
    injected into the :class:`VariablePool` as system variables at task
    creation time, so all nodes can access them via
    ``variables["system"]["task_id"]`` etc. This also ensures that
    ``resume_from_checkpoint`` works without needing to separately
    track ``user_id`` on the engine instance.
    """

    def __init__(self) -> None:
        self._task_id: str = ""
        self._pool: VariablePool | None = None
        self._nodes: list[dict[str, Any]] = []
        self._node_map: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []
        self._out_edges: dict[str, list[dict[str, Any]]] = {}  # source -> edges
        self._in_edges: dict[str, list[dict[str, Any]]] = {}  # target -> edges
        self._completed_nodes: set[str] = set()
        # 节点边界取消时记录当前将要执行（但尚未执行）的节点，供 except 保存 checkpoint
        self._pending_node_id: str = ""

    # ── Public API ──

    async def run_and_persist(self, task_id: str) -> dict[str, Any]:
        """Load task & workflow, execute, and persist results back to Task.

        This is the high-level entry point meant to be called from TaskService
        after creating a new Task.

        Distinguishes between first-time execution and resume from checkpoint:
        - If task has a checkpoint → calls `resume_from_checkpoint`
        - Otherwise → calls `execute_task` (full workflow from start)

        Args:
            task_id: The Task ID to execute.

        Returns:
            Final variable pool snapshot.
        """
        from app.db.mongodb import get_database

        db = get_database()

        # Fetch task
        task_doc = await db["tasks"].find_one({"_id": task_id})
        if task_doc is None:
            logger.error("run_and_persist_task_not_found", task_id=task_id)
            return {}

        # Zombie guard: if the task is CANCELLED (not RUNNING), refuse to
        # execute. This handles the Celery revoke+terminate edge case where a
        # terminated task's unacked message is restored after visibility_timeout
        # (7 days) and re-delivered. At that point the task is already cancelled
        # and should NOT be re-run. Only RUNNING tasks are legitimate here
        # (the dispatchers set RUNNING before calling run_and_persist, or the
        # engine itself marks RUNNING in execute_task for fresh runs).
        # NOTE: compare against the raw string "cancelled" (not
        # TaskStatus.CANCELLED.value) to avoid shadowing the module-level
        # TaskStatus import — a local ``from app.models.task import TaskStatus``
        # in the validation-fail branch below would turn TaskStatus into a
        # local variable for the entire function scope.
        task_status = task_doc.get("status", "")
        if task_status == "cancelled":
            logger.warning(
                "run_and_persist_task_cancelled_skip",
                task_id=task_id,
                status=task_status,
            )
            return {}

        workflow_id = task_doc.get("workflow_id", "")
        if not workflow_id:
            logger.error("run_and_persist_no_workflow", task_id=task_id)
            return {}

        # Fetch workflow definition
        workflow_doc = await db["workflows"].find_one({"_id": workflow_id})
        if workflow_doc is None:
            logger.error("run_and_persist_workflow_not_found", task_id=task_id, workflow_id=workflow_id)
            return {}

        # ── Static validation before execution ──
        # Quick structural check to catch obvious issues early
        from app.engine.workflow.validator import WorkflowValidator

        validator = WorkflowValidator(workflow_doc)
        validation_result = validator.validate()
        if not validation_result.is_valid:
            error_msgs = [str(issue) for issue in validation_result.errors]
            logger.error(
                "workflow_validation_failed_before_execution",
                task_id=task_id,
                workflow_id=workflow_id,
                errors=error_msgs,
            )
            # Mark task as failed immediately — don't waste resources
            # Note: only import TaskError/TaskStatus here (NOT utc_now) —
            # utc_now is already imported at module scope (line 25). Importing
            # it locally inside this `if` branch would shadow the top-level
            # name for the ENTIRE function scope, causing UnboundLocalError
            # on every code path that skips this branch (e.g. valid workflows).
            from app.models.task import TaskError, TaskStatus

            await db["tasks"].update_one(
                {"_id": task_id},
                {
                    "$set": {
                        "status": TaskStatus.FAILED.value,
                        "error": TaskError(
                            error_message=f"Workflow 结构验证失败: {'; '.join(error_msgs)}",
                            error_code="WORKFLOW_VALIDATION_FAILED",
                        ).model_dump(),
                        "updated_at": utc_now(),
                    },
                    "$push": {
                        "timeline": {
                            "timestamp": utc_now(),
                            "event_type": "failed",
                            "data": {"errors": error_msgs},
                            "actor": "system",
                        },
                    },
                },
            )
            return {}

        # Log warnings if any
        if validation_result.warnings:
            logger.warning(
                "workflow_validation_warnings_before_execution",
                task_id=task_id,
                warnings=[str(w) for w in validation_result.warnings],
            )

        # Check if resuming from checkpoint
        if task_doc.get("checkpoint"):
            logger.info("run_and_persist_resuming", task_id=task_id)
            final_output = await self.resume_from_checkpoint(task_id)
        else:
            # Execute from scratch
            final_output = await self.execute_task(task_doc, workflow_doc)

        # Persist final output to Task.output
        # Strip file content (up to 50K chars per file) before storing —
        # the content already lives in file_library and is accessible via
        # storage_key.  Keeping it here would bloat the task document.
        # Use JSON roundtrip to guarantee BSON-safe types for MongoDB storage.
        try:
            stripped_output = _strip_file_content(final_output)
            safe_output = json.loads(
                json.dumps(stripped_output, default=str, ensure_ascii=False)
            )
            await db["tasks"].update_one(
                {"_id": task_id},
                {"$set": {"output": safe_output, "updated_at": utc_now()}},
            )
        except Exception as exc:
            logger.error(
                "run_and_persist_output_save_failed",
                task_id=task_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )

        return final_output

    async def execute_task(
        self,
        task_doc: dict[str, Any],
        workflow_doc: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a Task against a Workflow definition.

        Args:
            task_doc: Task MongoDB document.
            workflow_doc: Workflow definition with ``nodes`` and ``edges``.

        Returns:
            Final variable pool snapshot (or partial snapshot on failure).
        """
        self._task_id = task_doc["_id"]
        self._nodes = workflow_doc.get("nodes", [])
        self._node_map = {n["node_id"]: n for n in self._nodes}
        self._edges = workflow_doc.get("edges", [])

        # Build edge index
        self._out_edges.clear()
        self._in_edges.clear()
        for edge in self._edges:
            src = edge["source"]
            tgt = edge["target"]
            self._out_edges.setdefault(src, []).append(edge)
            self._in_edges.setdefault(tgt, []).append(edge)

        # Back-fill next_nodes from legacy edges (backward compat)
        self._migrate_edges_to_next_nodes()

        # Initialise variable pool
        # Story 4-15: Inject task-level system variables so that all nodes
        # can access task_id / user_id / workflow_id via variables['system'].
        # This replaces the old per-entry-point constructor injection via
        # BaseNodeExecutor(task_id=..., user_id=...).
        self._pool = VariablePool(
            initial={
                "input": task_doc.get("input", {}),
                "system": {
                    "task_id": task_doc["_id"],
                    "user_id": task_doc.get("created_by", ""),
                    "workflow_id": workflow_doc.get("_id", ""),
                    # 终端用户通用 token（外部触发时透传，供 Agent 节点调
                    # MCP 时做凭证兑换；内部触发为空 → 用静态凭证）。
                    "user_token": task_doc.get("ext_user_token", ""),
                },
            }
        )
        self._completed_nodes.clear()

        # Mark Task as running (skip if already running — e.g. retry set it
        # directly to RUNNING before dispatching).
        from app.db.mongodb import get_database as _get_db
        _cur_doc = await _get_db()["tasks"].find_one({"_id": self._task_id}, {"status": 1})
        if _cur_doc and _cur_doc.get("status") != TaskStatus.RUNNING.value:
            await TaskService.transition_task(
                task_id=self._task_id,
                to_status=TaskStatus.RUNNING,
                triggered_by="system",
                triggered_by_type="system",
                timeline_event_type="started",
            )

        # Reset cancel-checkpoint tracking for this execution.
        self._cancel_checkpoint_saved = False
        self._pending_node_id = ""

        try:
            # Find start node(s) — nodes with no incoming edges
            start_nodes = self._find_start_nodes()
            if not start_nodes:
                raise ValueError("工作流没有 start 节点")

            # Execute from each start node
            for start_id in start_nodes:
                await self._execute_node(start_id)

            # Mark Task as completed
            final_output = self._pool.get_all()
            await TaskService.transition_task(
                task_id=self._task_id,
                to_status=TaskStatus.COMPLETED,
                triggered_by="system",
                triggered_by_type="system",
                timeline_event_type="completed",
                timeline_data={"output": _strip_file_content(final_output)},
            )

            return final_output

        except WorkflowPausedError:
            # Graceful pause — not an error, return current state.
            logger.info("workflow_paused", task_id=self._task_id)
            return self._pool.get_all() if self._pool else {}

        except WorkflowCancelledError:
            # Task was cancelled. Two cases:
            # 1. Agent node interrupted → checkpoint already saved by the
            #    AGENT_INTERRUPTED handler (with agent_thread_id).
            # 2. Node-boundary cancel → save checkpoint NOW so resume doesn't
            #    restart from scratch. paused_at_node = the node that was about
            #    to execute (NOT in completed_nodes), so resume re-executes it.
            if self._pending_node_id and not getattr(self, "_cancel_checkpoint_saved", False):
                await self._save_cancel_checkpoint(self._pending_node_id)
            logger.info("workflow_cancelled", task_id=self._task_id)
            return self._pool.get_all() if self._pool else {}

        except Exception as exc:
            logger.error("workflow_execution_failed", task_id=self._task_id, error=str(exc))

            # Mark Task as failed — wrap in try/except so even if the
            # transition itself raises (e.g. version conflict due to
            # concurrent access), the task doesn't get stuck in RUNNING.
            try:
                await TaskService.transition_task(
                    task_id=self._task_id,
                    to_status=TaskStatus.FAILED,
                    triggered_by="system",
                    triggered_by_type="system",
                    timeline_event_type="failed",
                    timeline_data={"error": str(exc)},
                    error_info={
                        "node_id": getattr(exc, "node_id", None),
                        "node_type": getattr(exc, "node_type", None),
                        "error_message": str(exc),
                        "error_code": "WORKFLOW_EXECUTION_ERROR",
                    },
                )
            except Exception as transition_exc:
                # If transition_task itself fails, at least append a
                # timeline event so there is a record in the DB.
                logger.error(
                    "workflow_failed_transition_failed",
                    task_id=self._task_id,
                    transition_error=str(transition_exc),
                    original_error=str(exc),
                )
                try:
                    from app.db.mongodb import get_database
                    from app.models.task import TimelineEvent
                    from app.models.task import utc_now as _utc_now

                    await get_database()["tasks"].update_one(
                        {"_id": self._task_id},
                        {
                            "$push": {
                                "timeline": TimelineEvent(
                                    timestamp=_utc_now(),
                                    event_type="failed_transition_error",
                                    data={
                                        "original_error": str(exc),
                                        "transition_error": str(transition_exc),
                                        "error_code": "WORKFLOW_EXECUTION_ERROR",
                                    },
                                    actor="system",
                                ).model_dump()
                            },
                            "$set": {"updated_at": _utc_now()},
                        },
                    )
                except Exception:
                    logger.exception(
                        "workflow_failed_timeline_append_failed",
                        task_id=self._task_id,
                    )

            return self._pool.get_all() if self._pool else {}

    async def resume_from_checkpoint(self, task_id: str) -> dict[str, Any]:
        """Resume workflow execution from a saved checkpoint.

        Called after a Human node intervention (approve/skip) or after
        server restart when a task is still in waiting_human status.

        Steps:
        1. Load task + workflow from DB
        2. Restore _completed_nodes and _pool from checkpoint
        3. Find downstream nodes of the paused Human node
        4. Continue execution from those downstream nodes
        5. On completion → COMPLETED + clear checkpoint
        6. On encountering another human → WorkflowPausedError (new checkpoint saved)
        7. On error → FAILED
        """
        from app.db.mongodb import get_database

        db = get_database()

        # Load task
        task_doc = await db["tasks"].find_one({"_id": task_id})
        if task_doc is None:
            logger.error("resume_checkpoint_task_not_found", task_id=task_id)
            return {}

        checkpoint_data = task_doc.get("checkpoint")
        if not checkpoint_data:
            logger.error("resume_checkpoint_not_found", task_id=task_id)
            return {}

        # Load workflow
        workflow_id = task_doc.get("workflow_id", "")
        workflow_doc = await db["workflows"].find_one({"_id": workflow_id})
        if workflow_doc is None:
            logger.error("resume_checkpoint_workflow_not_found", task_id=task_id, workflow_id=workflow_id)
            return {}

        # Initialize engine state
        self._task_id = task_id
        self._nodes = workflow_doc.get("nodes", [])
        self._node_map = {n["node_id"]: n for n in self._nodes}
        self._edges = workflow_doc.get("edges", [])

        # Build edge index
        self._out_edges.clear()
        self._in_edges.clear()
        for edge in self._edges:
            src = edge["source"]
            tgt = edge["target"]
            self._out_edges.setdefault(src, []).append(edge)
            self._in_edges.setdefault(tgt, []).append(edge)

        # Back-fill next_nodes from legacy edges
        self._migrate_edges_to_next_nodes()

        # Restore state from checkpoint
        paused_node_id = checkpoint_data.get("paused_at_node", "")
        self._completed_nodes = set(checkpoint_data.get("completed_nodes", []))
        agent_thread_id = checkpoint_data.get("agent_thread_id", "")
        human_context = checkpoint_data.get("human_context", {})
        # 判断 checkpoint 类型：
        # - agent_thread_id 非空 → agent 中途取消（需 Command(resume) 续接）
        # - human_context 非空 → Human 节点暂停（节点已完成，恢复下游）
        # - 两者皆空 → 节点边界取消（节点未执行，需重新执行它）
        is_human_pause = bool(human_context)

        if agent_thread_id:
            # Cancel-during-agent case: the agent node was interrupted
            # mid-REACT-loop via LangGraph interrupt(). We must RE-EXECUTE
            # it (it is NOT in completed_nodes), but with resume_agent_thread_id
            # so the executor calls Command(resume) to continue the agent's
            # conversation with full context continuity. Then run downstream.
            # Do NOT add paused_node_id to completed — it hasn't finished yet.
            pass
        elif is_human_pause:
            # Human-node pause case: the paused node was approved/skipped,
            # so mark it complete and resume from its downstream nodes.
            self._completed_nodes.add(paused_node_id)
        else:
            # Node-boundary cancel case: cancellation was detected BEFORE
            # paused_node_id started executing. Do NOT add it to completed —
            # it needs to be re-executed. Resume by running it + downstream.
            pass

        # Restore variable pool
        # Story 4-15: Ensure system variables are present in the pool even
        # for checkpoints saved before system variables were introduced.
        # This also fixes the bug where resume_from_checkpoint didn't set
        # self._user_id, causing AgentNodeExecutor to fail.
        variable_snapshot = checkpoint_data.get("variable_snapshot", {})
        variable_snapshot.setdefault("system", {})
        variable_snapshot["system"].setdefault("task_id", task_id)
        variable_snapshot["system"].setdefault("user_id", task_doc.get("created_by", ""))
        variable_snapshot["system"].setdefault("workflow_id", workflow_id)
        self._pool = VariablePool(initial=variable_snapshot)

        logger.info(
            "resume_from_checkpoint",
            task_id=task_id,
            paused_node=paused_node_id,
            completed_count=len(self._completed_nodes),
        )

        try:
            if agent_thread_id:
                # ── Resume after agent cancellation ──
                # Inject the agent_thread_id into system variables so
                # AgentNodeExecutor detects it and calls resume_agent()
                # (Command(resume)) to continue the REACT loop with full
                # context, instead of starting fresh.
                self._pool.set(
                    "system",
                    {
                        **(self._pool.get("system") or {}),
                        "resume_agent_thread_id": agent_thread_id,
                    },
                )
                # Re-execute the interrupted agent node (NOT in completed_nodes)
                await self._execute_node(paused_node_id)
                # Clear the resume flag + checkpoint now that the agent finished
                await db["tasks"].update_one(
                    {"_id": task_id},
                    {"$set": {"checkpoint": None, "updated_at": utc_now()}},
                )
            elif not is_human_pause:
                # ── Resume after node-boundary cancellation ──
                # The paused_node_id was about to execute when cancellation
                # was detected. It is NOT in completed_nodes, so re-execute
                # it, then continue to downstream nodes.
                await self._execute_node(paused_node_id)

            # Find downstream nodes of the paused node
            downstream = self._get_downstream_nodes(paused_node_id)

            if not downstream:
                # No downstream nodes — workflow is complete
                logger.info("resume_checkpoint_no_downstream", task_id=task_id)
                final_output = self._pool.get_all()
                await TaskService.transition_task(
                    task_id=task_id,
                    to_status=TaskStatus.COMPLETED,
                    triggered_by="system",
                    triggered_by_type="system",
                    timeline_event_type="completed",
                    timeline_data={"output": _strip_file_content(final_output), "resumed_from_checkpoint": True},
                )
                # Clear checkpoint
                await db["tasks"].update_one(
                    {"_id": task_id},
                    {"$set": {"checkpoint": None, "updated_at": utc_now()}},
                )
                return final_output

            # Execute from downstream nodes
            for next_node_id in downstream:
                await self._execute_node(next_node_id)

            # Workflow completed successfully
            final_output = self._pool.get_all()
            await TaskService.transition_task(
                task_id=task_id,
                to_status=TaskStatus.COMPLETED,
                triggered_by="system",
                triggered_by_type="system",
                timeline_event_type="completed",
                timeline_data={"output": _strip_file_content(final_output), "resumed_from_checkpoint": True},
            )
            # Clear checkpoint
            await db["tasks"].update_one(
                {"_id": task_id},
                {"$set": {"checkpoint": None, "updated_at": utc_now()}},
            )
            return final_output

        except WorkflowPausedError:
            # Encountered another human node — new checkpoint already saved
            logger.info("resume_checkpoint_paused_again", task_id=task_id)
            return self._pool.get_all() if self._pool else {}

        except WorkflowCancelledError:
            # Re-cancelled during resume. Save a checkpoint (if not already
            # saved by the agent-interrupt handler) so a subsequent resume
            # doesn't lose progress.
            if self._pending_node_id and not getattr(self, "_cancel_checkpoint_saved", False):
                await self._save_cancel_checkpoint(self._pending_node_id)
            logger.info("resume_checkpoint_cancelled", task_id=task_id)
            return self._pool.get_all() if self._pool else {}

        except Exception as exc:
            logger.error("resume_checkpoint_failed", task_id=task_id, error=str(exc))
            try:
                await TaskService.transition_task(
                    task_id=task_id,
                    to_status=TaskStatus.FAILED,
                    triggered_by="system",
                    triggered_by_type="system",
                    timeline_event_type="failed",
                    timeline_data={"error": str(exc), "resumed_from_checkpoint": True},
                    error_info={
                        "node_id": getattr(exc, "node_id", None),
                        "node_type": getattr(exc, "node_type", None),
                        "error_message": str(exc),
                        "error_code": "WORKFLOW_RESUME_ERROR",
                    },
                )
            except Exception as transition_exc:
                logger.error(
                    "resume_checkpoint_failed_transition_failed",
                    task_id=task_id,
                    transition_error=str(transition_exc),
                    original_error=str(exc),
                )
            return self._pool.get_all() if self._pool else {}

    def _get_downstream_nodes(self, node_id: str) -> list[str]:
        """Get downstream node IDs of a given node.

        Prefers config.next_nodes, falls back to legacy edges.
        """
        node = self._node_map.get(node_id)
        if node is None:
            return []

        config = node.get("config", {})
        next_nodes = config.get("next_nodes")
        if next_nodes:
            return [nxt["target"] for nxt in next_nodes if nxt.get("target")]

        # Fallback to legacy edges
        outgoing = self._out_edges.get(node_id, [])
        return [edge["target"] for edge in outgoing if edge.get("target")]

    # ── Internal: cancellation ──

    async def _check_cancelled(self, node_id: str = "") -> None:
        """Raise :class:`WorkflowCancelledError` if the task was cancelled.

        Re-reads the task status from MongoDB at each node boundary so that an
        external cancel request (API ``intervene action=cancel``) is noticed
        promptly. The DB flag is the single source of truth; the running engine
        cooperatively checks it between nodes.

        Args:
            node_id: The node that is *about to* execute (passed so the
                ``except`` handler can save a checkpoint recording which node
                to resume from). Defaults to ``self._pending_node_id``.
        """
        from app.db.mongodb import get_database

        if node_id:
            self._pending_node_id = node_id

        doc = await get_database()["tasks"].find_one(
            {"_id": self._task_id}, {"status": 1},
        )
        if doc and doc.get("status") == TaskStatus.CANCELLED.value:
            raise WorkflowCancelledError()

    async def _save_cancel_checkpoint(self, node_id: str, agent_thread_id: str = "") -> None:
        """Persist a checkpoint so the workflow can resume after cancellation.

        Records the node that was executing when cancellation was detected,
        the set of already-completed nodes, and a snapshot of the variable
        pool. If the cancellation interrupted an Agent node mid-REACT-loop,
        ``agent_thread_id`` stores the LangGraph thread id so that on resume
        the agent's full conversation context is restored via ``Command(resume)``.
        """
        from app.db.mongodb import get_database

        checkpoint = Checkpoint(
            paused_at_node=node_id,
            completed_nodes=list(self._completed_nodes),
            variable_snapshot=self._pool.snapshot() if self._pool else {},
            agent_thread_id=agent_thread_id,
        )
        safe_snapshot = json.loads(
            json.dumps(
                self._pool.snapshot() if self._pool else {},
                default=str, ensure_ascii=False,
            )
        )
        await get_database()["tasks"].update_one(
            {"_id": self._task_id},
            {
                "$set": {
                    "checkpoint": checkpoint.model_dump(mode="json"),
                    "variables": safe_snapshot,
                    "updated_at": utc_now(),
                }
            },
        )
        logger.info(
            "cancel_checkpoint_saved",
            task_id=self._task_id,
            node_id=node_id,
            agent_thread_id=agent_thread_id,
        )

    # ── Internal: DAG execution ──

    def _find_start_nodes(self) -> list[str]:
        """Find start nodes — nodes with type == 'start'.

        Previously relied on "no incoming edges" heuristic; now uses the
        explicit node type so that even isolated / mis-wired start nodes
        are correctly identified.
        """
        return [n["node_id"] for n in self._nodes if n.get("type") == "start"]

    def _find_end_nodes(self) -> list[str]:
        """Find end nodes — nodes with type == 'end'."""
        return [n["node_id"] for n in self._nodes if n.get("type") == "end"]

    def _migrate_edges_to_next_nodes(self) -> None:
        """Back-fill ``config.next_nodes`` from legacy ``edges``.

        For each ordinary node (start / agent / tool / end / human / subflow)
        that does *not* yet have ``next_nodes`` in its config, derive the list
        from matching outgoing edges in ``self._edges``.

        Gateway and parallel nodes manage their own routing internally
        (``conditions[].target`` / ``branches[].start_node``) and are skipped.
        """
        _skip_types = {"gateway", "parallel"}

        for node in self._nodes:
            if node.get("type") in _skip_types:
                continue
            config = node.setdefault("config", {})
            if config.get("next_nodes"):
                # Already has next_nodes — nothing to do
                continue
            node_id = node["node_id"]
            next_nodes: list[dict[str, Any]] = []
            for edge in self._out_edges.get(node_id, []):
                next_nodes.append({
                    "target": edge["target"],
                    "label": edge.get("label", ""),
                    "condition": edge.get("condition"),
                })
            if next_nodes:
                config["next_nodes"] = next_nodes

    async def _execute_node(self, node_id: str) -> NodeResult | None:
        """Execute a single node and its downstream dependencies.

        Returns the node result, or ``None`` if already executed / skipped.
        """
        # Cooperative cancellation: check DB flag at every node boundary.
        await self._check_cancelled(node_id)

        if node_id in self._completed_nodes:
            return None

        node = self._node_map.get(node_id)
        if node is None:
            logger.warning("node_not_found", node_id=node_id)
            return None

        node_type = node.get("type", "")
        node_config = node.get("config", {})

        # Record node start in timeline
        await TaskService.append_timeline_event(
            task_id=self._task_id,
            event_type="node_start",
            data={"node_id": node_id, "node_type": node_type},
        )

        # Get executor — system variables (task_id, user_id, etc.) are
        # available via the variable pool; no need to thread them through
        # the executor constructor.
        executor = get_node_executor(
            node_type, node_id, node_config,
        )

        # Resolve config expressions before execution
        variables = self._pool.get_all() if self._pool else {}
        engine = ExpressionEngine(variables)

        # Execute
        result = await executor.execute(variables)

        # ── Agent node interrupted (cancelled via LangGraph interrupt) ──
        # The agent's REACT loop was suspended; its full context is in the
        # checkpointer. Save a checkpoint with the agent_thread_id so resume
        # can use Command(resume) to continue the agent with full continuity.
        if result.error_code == "AGENT_INTERRUPTED":
            agent_thread_id = getattr(executor, "agent_thread_id", "")
            await self._save_cancel_checkpoint(node_id, agent_thread_id)
            self._cancel_checkpoint_saved = True  # prevent double-save in except
            raise WorkflowCancelledError(node_id=node_id, node_type=node_type)

        if result.success:
            # Store output in variable pool
            if self._pool is not None:
                self._pool.set(node_id, result.output)

            self._completed_nodes.add(node_id)

            # Record node complete (include agent token usage for timeline display)
            node_usage = result.output.get("usage") or {}
            await TaskService.append_timeline_event(
                task_id=self._task_id,
                event_type="node_complete",
                data={"node_id": node_id, "node_type": node_type, "output_summary": _summarise_output(result.output), "usage": node_usage},
            )
            # Accumulate task-level token total onto the Task document immediately
            # ($inc per agent node, so pause/resume doesn't lose pre-pause usage).
            _node_total = node_usage.get("total_tokens")
            if isinstance(_node_total, (int, float)) and _node_total > 0:
                await TaskService.add_total_tokens(self._task_id, int(_node_total))

            # Handle human: transition to waiting_human and pause execution
            if node_type == "human" and result.output.get("status") == "waiting_human":
                await TaskService.transition_task(
                    task_id=self._task_id,
                    to_status=TaskStatus.WAITING_HUMAN,
                    triggered_by="system",
                    triggered_by_type="system",
                    timeline_event_type="waiting_human",
                    timeline_data={
                        "node_id": node_id,
                        "title": result.output.get("title", ""),
                        "description": result.output.get("description", ""),
                    },
                )

                # Save checkpoint to DB for later resume
                timeout_ms = result.output.get("timeout_ms", 0)
                timeout_action = result.output.get("timeout_action", "fail")
                timeout_deadline = None
                if timeout_ms > 0:
                    timeout_deadline = utc_now() + timedelta(milliseconds=timeout_ms)

                checkpoint = Checkpoint(
                    paused_at_node=node_id,
                    completed_nodes=list(self._completed_nodes),
                    variable_snapshot=self._pool.snapshot() if self._pool else {},
                    human_context={
                        "node_id": node_id,
                        "title": result.output.get("title", ""),
                        "description": result.output.get("description", ""),
                        "view": result.output.get("view"),
                        "options": result.output.get("options", []),
                        "timeout_ms": timeout_ms,
                        "timeout_action": timeout_action,
                    },
                    timeout_deadline=timeout_deadline,
                    timeout_action=timeout_action,
                )

                from app.db.mongodb import get_database
                db = get_database()

                # JSON roundtrip to guarantee BSON-safe types for MongoDB
                safe_snapshot = json.loads(
                    json.dumps(
                        self._pool.snapshot() if self._pool else {},
                        default=str, ensure_ascii=False,
                    )
                )

                await db["tasks"].update_one(
                    {"_id": self._task_id},
                    {
                        "$set": {
                            "checkpoint": checkpoint.model_dump(mode="json"),
                            "variables": safe_snapshot,
                            "updated_at": utc_now(),
                        }
                    },
                )
                logger.info(
                    "checkpoint_saved",
                    task_id=self._task_id,
                    node_id=node_id,
                    timeout_deadline=str(timeout_deadline),
                )

                # 超时由 Celery beat 周期任务 sweep_timed_out_human_tasks 扫描
                # checkpoint.timeout_deadline 触发，不再在此启动进程内 monitor
                # （worker 事件循环在 run_until_complete 返回后不再被驱动，
                # asyncio.sleep 永不执行 → 超时永不触发）。

                # Stop execution — wait for external intervention
                raise WorkflowPausedError(
                    node_id=node_id,
                    node_type=node_type,
                    message=f"等待人工审批: {result.output.get('title', '')}",
                )

            # Handle gateway: follow selected branch only
            if node_type == "gateway" and result.selected_branch:
                await self._execute_node(result.selected_branch)
                return result

            # Handle parallel: execute all branch start nodes
            if node_type == "parallel":
                start_nodes = result.output.get("start_nodes", {})
                join_strategy = result.output.get("join_strategy", "all")
                join_count = result.output.get("join_count")
                branch_ids = result.output.get("branches", [])

                coros = [self._execute_node(sn) for sn in start_nodes.values() if sn]

                # race 是 any 的别名（取最快结果 = 任一完成即可）
                effective_strategy = "any" if join_strategy == "race" else join_strategy

                if effective_strategy == "any":
                    # any: wait for first completion, cancel the rest
                    tasks = [asyncio.ensure_future(c) for c in coros]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    list(done)  # consume results
                elif effective_strategy == "n-of-m" and join_count and join_count > 0:
                    # n-of-m: wait until join_count branches complete
                    tasks = [asyncio.ensure_future(c) for c in coros]
                    done_tasks: set[asyncio.Task] = set()
                    pending_tasks = set(tasks)
                    while len(done_tasks) < join_count and pending_tasks:
                        newly_done, pending_tasks = await asyncio.wait(
                            pending_tasks, return_when=asyncio.FIRST_COMPLETED,
                        )
                        done_tasks.update(newly_done)
                    for t in pending_tasks:
                        t.cancel()
                    list(done_tasks)
                else:
                    # all (default): wait for all branches
                    await asyncio.gather(*coros, return_exceptions=True)

                logger.debug(
                    "parallel_complete",
                    node_id=node_id,
                    branches=len(branch_ids),
                    join_strategy=join_strategy,
                )
                return result

            # Follow outgoing edges — prefer config.next_nodes, fallback to legacy edges
            next_nodes = node_config.get("next_nodes")
            if next_nodes:
                # New path: read from node config
                for nxt in next_nodes:
                    target_id = nxt["target"]
                    condition = nxt.get("condition")
                    if condition:
                        cond_result = engine.resolve_bool(condition)
                        if not cond_result:
                            continue
                    await self._execute_node(target_id)
            else:
                # Legacy fallback: read from workflow-level edges
                outgoing = self._out_edges.get(node_id, [])
                for edge in outgoing:
                    target_id = edge["target"]
                    condition = edge.get("condition")
                    if condition:
                        cond_result = engine.resolve_bool(condition)
                        if not cond_result:
                            continue
                    await self._execute_node(target_id)

        else:
            # Node failed — propagate error
            self._completed_nodes.add(node_id)
            await TaskService.append_timeline_event(
                task_id=self._task_id,
                event_type="node_failed",
                data={
                    "node_id": node_id,
                    "node_type": node_type,
                    "error": result.error_message,
                },
            )
            raise WorkflowNodeError(
                node_id=node_id,
                node_type=node_type,
                message=result.error_message,
            )

        return result


def _summarise_output(output: dict[str, Any], max_len: int = 200) -> str:
    """Create a short summary of a node's output for timeline logging.

    优先提取常见文本字段（agent 的 response / tool 的 result 等），避免
    str(dict) 产生带单引号的 Python repr（前端既无法解析也不美观）。
    """
    for key in ("response", "answer", "result", "output", "content", "message", "text"):
        v = output.get(key)
        if isinstance(v, str) and v.strip():
            return v[:max_len] + ("..." if len(v) > max_len else "")
    # 次选：dict 里第一个有意义的字符串值
    for v in output.values():
        if isinstance(v, str) and v.strip():
            return v[:max_len] + ("..." if len(v) > max_len else "")
    # 回退：JSON 字符串（双引号，前端可结构化解析），而非 str() 的单引号 repr
    try:
        import json
        text = json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        text = str(output)
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _strip_file_content(obj: Any) -> Any:
    """Recursively remove ``content`` from FileVariableValue-like dicts.

    FileVariableValue dicts carry up to 50K chars of file text which bloats
    the persisted task output.  The content already lives in file_library
    and can be fetched via ``storage_key``, so we strip it before storing.
    """
    if isinstance(obj, dict):
        # Detect FileVariableValue: has both "file_id" and "content" keys
        if "file_id" in obj and "content" in obj:
            return {k: v for k, v in obj.items() if k != "content"}
        return {k: _strip_file_content(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_file_content(item) for item in obj]
    return obj


class WorkflowNodeError(Exception):
    """Raised when a workflow node execution fails."""

    def __init__(self, node_id: str, node_type: str, message: str) -> None:
        self.node_id = node_id
        self.node_type = node_type
        super().__init__(message)


class WorkflowPausedError(Exception):
    """Raised when a workflow pauses for human intervention (not an error)."""

    def __init__(self, node_id: str, node_type: str, message: str) -> None:
        self.node_id = node_id
        self.node_type = node_type
        self.pause_message = message
        super().__init__(message)


class WorkflowCancelledError(Exception):
    """Raised when a workflow is cancelled (paused for potential resume).

    Triggered when the task's DB status is ``cancelled`` — either detected at
    a node boundary (``_check_cancelled``) or when an Agent node returns
    ``AGENT_INTERRUPTED`` (its REACT loop was suspended via LangGraph
    ``interrupt()``).
    """

    def __init__(self, node_id: str = "", node_type: str = "", message: str = "") -> None:
        self.node_id = node_id
        self.node_type = node_type
        super().__init__(message or "工作流已被取消")
