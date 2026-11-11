"""Process-local cooperative pause for an explicitly bound backend execution.

Install ``middleware_factory(agent_id)`` on the main agent; the execution owner
must ``async with registry.bind(agent_id, real_backend_thread_id)`` around the
existing stream (including a HITL resume stream). Controls use ``registry.get``
and call the gate directly, outside the execution/session locks.

Pause closes subsequent model/tool admission. Admitted tools may finish and
return their results immediately. ``paused`` means tool handlers have drained;
``abefore_model`` cannot track or stop an already admitted model generation.
It does not mean checkpoint flush, background peers, remote jobs, or subprocesses
have stopped. Resume wakes the SAME execution: never resubmit input, interrupt,
or call resume_hitl to implement it. No restart persistence or payload storage.

All calls belong on the backend event loop. Binding is exclusive per key and
must outlive the execution. Release only after the owner has unwound; termination
itself neither cancels admitted calls nor declares the entire execution finished.
LangGraph may wrap PauseTerminated as NodeCancelledError with that cause; the
owner must treat that as termination, never retry/replay the execution.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

__all__ = [
    "PauseGate", "PauseStatus", "PauseTerminated", "PauseRegistry",
    "registry", "middleware_factory",
]


class PauseTerminated(asyncio.CancelledError):
    """A closed gate rejects admission without becoming a tool error message."""


@dataclass(frozen=True)
class PauseStatus:
    state: Literal["running", "pausing", "paused", "terminating", "terminated"]
    in_flight: int
    waiting: int


class PauseGate:
    """Condition protects control flags, admission and tool drain accounting."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._pause_requested = False
        self._terminated = False
        self._in_flight = 0
        self._waiting = 0

    def _status_locked(self) -> PauseStatus:
        if self._terminated:
            state = "terminating" if self._in_flight else "terminated"
        elif self._pause_requested:
            state = "pausing" if self._in_flight else "paused"
        else:
            state = "running"
        return PauseStatus(state, self._in_flight, self._waiting)

    async def status(self) -> PauseStatus:
        async with self._condition:
            return self._status_locked()

    async def request_pause(self) -> PauseStatus:
        """Close admission immediately; terminal gates remain terminal."""
        async with self._condition:
            if not self._terminated:
                self._pause_requested = True
            self._condition.notify_all()
            return self._status_locked()

    async def resume(self) -> PauseStatus:
        """Continue live waiters; a terminated execution cannot be revived."""
        async with self._condition:
            if self._terminated:
                raise PauseTerminated("Execution gate is terminated")
            self._pause_requested = False
            self._condition.notify_all()
            return self._status_locked()

    async def terminate(self) -> PauseStatus:
        """Permanently reject new admission, wake waiters, allow tools to drain."""
        async with self._condition:
            self._terminated = True
            self._condition.notify_all()
            return self._status_locked()

    async def wait_paused(self) -> PauseStatus:
        """Wait for closed admission and zero tools; cancellation is harmless.

        Resume may supersede this request: callers wanting an immediate result
        should inspect request_pause/status rather than await this indefinitely.
        """
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._terminated or (self._pause_requested and not self._in_flight)
            )
            if self._terminated:
                raise PauseTerminated("Execution gate is terminated")
            return self._status_locked()

    async def wait_idle(self) -> PauseStatus:
        """Wait for admitted tool wrappers, independent of the pause flag."""
        async with self._condition:
            await self._condition.wait_for(lambda: not self._in_flight)
            return self._status_locked()

    async def _admit_locked(self) -> None:
        if self._pause_requested or self._terminated:
            self._waiting += 1
            self._condition.notify_all()
            try:
                await self._condition.wait_for(
                    # Do not fail a sibling graph node until admitted tools
                    # finish: LangGraph cancels siblings on node failure.
                    lambda: (self._terminated and not self._in_flight)
                    or (not self._terminated and not self._pause_requested)
                )
            finally:
                self._waiting -= 1
                self._condition.notify_all()
        if self._terminated:
            raise PauseTerminated("Execution gate is terminated")

    async def checkpoint(self) -> None:
        """Model admission boundary only; does not count model generation time."""
        async with self._condition:
            await self._admit_locked()

    @asynccontextmanager
    async def tool_call(self) -> AsyncIterator[None]:
        async with self._condition:
            await self._admit_locked()
            # No await between checking the flag and incrementing admission.
            self._in_flight += 1
        try:
            yield
        finally:
            async with self._condition:
                self._in_flight -= 1
                self._condition.notify_all()


class PauseRegistry:
    """Only backend identifiers and gates are retained, never requests/results."""

    def __init__(self) -> None:
        self._gates: dict[tuple[str, str], PauseGate] = {}

    @staticmethod
    def _key(agent_id: str, thread_id: str) -> tuple[str, str]:
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("A backend agent_id is required")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("A backend thread_id is required")
        return agent_id, thread_id

    def get(self, agent_id: str, thread_id: str) -> PauseGate | None:
        return self._gates.get(self._key(agent_id, thread_id))

    def register(self, agent_id: str, thread_id: str) -> PauseGate:
        key = self._key(agent_id, thread_id)
        if key in self._gates:
            raise RuntimeError("Backend execution already has a pause gate")
        gate = PauseGate()
        self._gates[key] = gate
        return gate

    async def release(self, agent_id: str, thread_id: str, gate: PauseGate) -> bool:
        """Owner cleanup: close old waiters and drain; identity fences stale cleanup."""
        key = self._key(agent_id, thread_id)
        if self._gates.get(key) is not gate:
            return False
        await gate.terminate()
        await gate.wait_idle()
        if self._gates.get(key) is gate:
            del self._gates[key]
            return True
        return False

    @asynccontextmanager
    async def bind(self, agent_id: str, thread_id: str) -> AsyncIterator[PauseGate]:
        gate = self.register(agent_id, thread_id)
        try:
            yield gate
        finally:
            await self.release(agent_id, thread_id, gate)


registry = PauseRegistry()


class _PauseMiddleware(AgentMiddleware):
    def __init__(self, agent_id: str) -> None:
        PauseRegistry._key(agent_id, "validation")
        self._agent_id = agent_id

    def _gate(self, config: dict[str, Any]) -> PauseGate | None:
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("Pause middleware requires the real backend thread_id")
        return registry.get(self._agent_id, thread_id)

    async def abefore_model(self, state: Any, runtime: Any) -> None:
        # Runtime here is NOT ToolRuntime and does not provide .config.
        gate = self._gate(get_config())
        if gate is not None:
            await gate.checkpoint()

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        gate = self._gate(request.runtime.config)
        if gate is None:
            return await handler(request)
        async with gate.tool_call():
            return await handler(request)


def middleware_factory(agent_id: str) -> AgentMiddleware:
    """Create a main-agent middleware using the exported process-local registry.

    Unbound threads pass through. Bind the exact backend configurable.thread_id
    before execution; mounting this factory alone does not enable pause controls.
    """
    return _PauseMiddleware(agent_id)
