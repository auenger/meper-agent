"""Guard protocol and result type.

A :class:`Guard` is a coarse-grained gate spliced around the react node as
LangGraph nodes (``[guard_in] -> react -> [guard_out] -> END``). It decides
whether a step may run (``check_in``) and whether the step's output is
accepted (``check_out``), returning a :class:`GuardResult`.

Guard vs Middleware: a Guard *blocks* the flow; a Middleware
*observes/rewrites* without blocking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

if TYPE_CHECKING:
    from agent_flow_harness.state import AgentState


class GuardResult(BaseModel):
    """Outcome of a guard check.

    Attributes:
        decision: ``allow`` (proceed), ``block`` (short-circuit, set
            ``state["error"]``) or ``warn`` (append to ``state["warnings"]``
            but proceed).
        reason: Human-readable explanation; empty for a plain allow.
    """

    model_config = {"extra": "forbid"}

    decision: Literal["allow", "block", "warn"]
    reason: str = ""


# Convenience factories — keep call sites readable.
def Allow() -> GuardResult:
    return GuardResult(decision="allow")


def Block(reason: str) -> GuardResult:
    return GuardResult(decision="block", reason=reason)


def Warn(reason: str) -> GuardResult:
    return GuardResult(decision="warn", reason=reason)


@runtime_checkable
class Guard(Protocol):
    """Coarse-grained gate executed before/after the react node.

    Implementations expose a stable ``name`` (used as the graph node suffix)
    and async ``check_in`` / ``check_out`` methods. Built-in guards live in
    this package; applications may subclass to extend them.
    """

    name: str

    async def check_in(self, state: AgentState) -> GuardResult:
        """Decide whether the react node may run (pre-check)."""
        ...

    async def check_out(self, state: AgentState, output: dict[str, Any]) -> GuardResult:
        """Decide whether the react node's output is accepted (post-check)."""
        ...


__all__ = ["Allow", "Block", "Guard", "GuardResult", "Warn"]
