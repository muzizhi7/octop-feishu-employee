"""Offline tests using the installed LangChain/LangGraph interfaces.

Run with portable/runtime/bin/python3 -B and portable/packages on PYTHONPATH.
Only in-memory counters/messages are used; no Octop application is booted.
"""

import asyncio
import importlib.util
from pathlib import Path
import sys
import unittest

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import NodeCancelledError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt.tool_node import ToolCallRequest, ToolRuntime
from langgraph.runtime import Runtime

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "controls_pause_under_test",
    HERE / "staged/octop_gateway/channels/feishu_pause.py",
)
P = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = P
spec.loader.exec_module(P)


class OfflineModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        self.calls += 1
        return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        return self._generate(*args, **kwargs)


class PauseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        self.bindings = []

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for agent, thread, gate in self.bindings:
            await P.registry.release(agent, thread, gate)

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def bind(self, agent="main", thread="backend"):
        gate = P.registry.register(agent, thread)
        self.bindings.append((agent, thread, gate))
        return gate

    async def wait_status(self, gate, predicate):
        async with asyncio.timeout(3):
            while True:
                status = await gate.status()
                if predicate(status):
                    return status
                await asyncio.sleep(0)

    def request(self, thread="backend", ident="call"):
        runtime = ToolRuntime(
            state={}, context=None,
            config={"configurable": {"thread_id": thread}},
            stream_writer=lambda event: None,
            tool_call_id=ident, store=None,
        )
        return ToolCallRequest(
            tool_call={"name": "write_counter", "args": {}, "id": ident, "type": "tool_call"},
            tool=None, state={}, runtime=runtime,
        )

    async def test_parallel_drain_blocks_new_calls_and_resume_does_not_replay(self):
        gate = self.bind()
        middleware = P.middleware_factory("main")
        entered = [asyncio.Event(), asyncio.Event()]
        finish = [asyncio.Event(), asyncio.Event()]
        calls = []

        async def handler(request):
            ident = request.tool_call["id"]
            calls.append(ident)
            if ident in {"0", "1"}:
                index = int(ident)
                entered[index].set()
                await finish[index].wait()
            return ToolMessage(content=ident, tool_call_id=ident)

        first = [self.spawn(middleware.awrap_tool_call(self.request(ident=str(i)), handler)) for i in range(2)]
        await asyncio.gather(*(event.wait() for event in entered))
        self.assertEqual((await gate.request_pause()).in_flight, 2)
        blocked = self.spawn(middleware.awrap_tool_call(self.request(ident="2"), handler))
        await self.wait_status(gate, lambda s: s.waiting == 1)
        finish[0].set()
        self.assertEqual((await first[0]).content, "0")  # Result not trapped behind pause.
        self.assertEqual((await gate.status()).state, "pausing")
        self.assertNotIn("2", calls)
        finish[1].set()
        await first[1]
        self.assertEqual((await gate.wait_paused()).state, "paused")
        self.assertFalse(blocked.done())
        await gate.resume()
        await blocked
        await gate.resume()  # Idempotent continue.
        self.assertCountEqual(calls, ["0", "1", "2"])
        self.assertEqual((await gate.status()).in_flight, 0)

    async def test_thread_and_agent_isolation_with_real_tool_runtime(self):
        gate = self.bind()
        self.bind(thread="other")
        self.bind(agent="other-agent")
        await gate.request_pause()
        calls = []

        async def handler(request):
            calls.append(request.tool_call["id"])
            return ToolMessage(content="ok", tool_call_id=request.tool_call["id"])

        blocked = self.spawn(P.middleware_factory("main").awrap_tool_call(self.request(ident="blocked"), handler))
        await self.wait_status(gate, lambda s: s.waiting == 1)
        await P.middleware_factory("main").awrap_tool_call(self.request("other", "thread"), handler)
        await P.middleware_factory("other-agent").awrap_tool_call(self.request(ident="agent"), handler)
        await P.middleware_factory("main").awrap_tool_call(self.request("unbound", "unbound"), handler)
        self.assertCountEqual(calls, ["thread", "agent", "unbound"])
        self.assertFalse(blocked.done())
        await gate.resume()
        await blocked

    async def test_real_graph_before_model_blocks_then_uses_same_execution(self):
        gate = self.bind()
        await gate.request_pause()
        model = OfflineModel(responses=[AIMessage(content="finished")])
        middleware = P.middleware_factory("main")
        self.assertIsInstance(middleware, AgentMiddleware)
        graph = create_agent(model, middleware=[middleware])
        run = self.spawn(graph.ainvoke({"messages": [HumanMessage(content="go")]}, {"configurable": {"thread_id": "backend"}}))
        await self.wait_status(gate, lambda s: s.waiting == 1)
        self.assertEqual(model.calls, 0)
        await gate.resume()
        self.assertEqual((await run)["messages"][-1].content, "finished")
        self.assertEqual(model.calls, 1)

    async def test_real_graph_preserves_written_result_and_checkpoint_on_continue(self):
        gate = self.bind()
        writes = []

        @tool
        async def write_counter() -> str:
            """Record a single in-memory write for an offline test."""
            writes.append("written")
            await gate.request_pause()
            return "written once"

        model = OfflineModel(responses=[
            AIMessage(content="", tool_calls=[{"name": "write_counter", "args": {}, "id": "write-1", "type": "tool_call"}]),
            AIMessage(content="finished"),
        ])
        graph = create_agent(model, tools=[write_counter], middleware=[P.middleware_factory("main")], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "backend"}}
        run = self.spawn(graph.ainvoke({"messages": [HumanMessage(content="go")]}, config))
        await self.wait_status(gate, lambda s: s.state == "paused" and s.waiting == 1)
        self.assertEqual(writes, ["written"])
        self.assertEqual(model.calls, 1)
        checkpoint = await graph.aget_state(config)
        self.assertTrue(any(isinstance(m, ToolMessage) and m.tool_call_id == "write-1" for m in checkpoint.values["messages"]))
        await gate.resume()
        result = await run
        self.assertEqual(result["messages"][-1].content, "finished")
        self.assertEqual(writes, ["written"])
        self.assertEqual(model.calls, 2)
        self.assertEqual(sum(isinstance(m, ToolMessage) for m in result["messages"]), 1)

    async def test_cancel_waiter_does_not_leak_admission_or_wait_count(self):
        gate = P.PauseGate()
        await gate.request_pause()

        async def blocked():
            async with gate.tool_call():
                self.fail("Paused tool entered")

        task = self.spawn(blocked())
        await self.wait_status(gate, lambda s: s.waiting == 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        status = await gate.status()
        self.assertEqual((status.in_flight, status.waiting), (0, 0))
        await gate.resume()
        async with gate.tool_call():
            self.assertEqual((await gate.status()).in_flight, 1)

    async def test_cancel_admitted_tool_runs_finally_and_drains(self):
        gate = self.bind()
        started = asyncio.Event()
        finalized = []

        async def handler(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.append(True)

        task = self.spawn(P.middleware_factory("main").awrap_tool_call(self.request(), handler))
        await started.wait()
        await gate.request_pause()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual((await gate.wait_paused()).in_flight, 0)
        self.assertEqual(finalized, [True])

    async def test_terminate_wakes_model_and_tool_waiters_without_cancelling_active(self):
        gate = self.bind()
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request.tool_call["id"])
            entered.set()
            await finish.wait()
            return ToolMessage(content="saved", tool_call_id=request.tool_call["id"])

        middleware = P.middleware_factory("main")
        active = self.spawn(middleware.awrap_tool_call(self.request(ident="active"), handler))
        await entered.wait()
        await gate.request_pause()
        tool_waiter = self.spawn(middleware.awrap_tool_call(self.request(ident="blocked"), handler))
        model_waiter = self.spawn(gate.checkpoint())
        drain_waiter = self.spawn(gate.wait_paused())
        await self.wait_status(gate, lambda s: s.waiting == 2)
        self.assertEqual((await gate.terminate()).state, "terminating")
        with self.assertRaises(P.PauseTerminated):
            await drain_waiter
        self.assertFalse(tool_waiter.done())
        self.assertFalse(model_waiter.done())
        self.assertFalse(active.done())
        finish.set()
        self.assertEqual((await active).content, "saved")
        for task in [tool_waiter, model_waiter]:
            with self.assertRaises(P.PauseTerminated):
                await task
        self.assertEqual((await gate.wait_idle()).state, "terminated")
        self.assertEqual(calls, ["active"])
        with self.assertRaises(P.PauseTerminated):
            await gate.resume()
        with self.assertRaises(P.PauseTerminated):
            await gate.checkpoint()
        self.assertEqual((await gate.request_pause()).state, "terminated")

    async def test_real_graph_termination_is_not_swallowed_as_tool_error(self):
        gate = self.bind()
        await gate.terminate()
        calls = []

        @tool
        async def write_counter() -> str:
            """In-memory operation that a terminated gate must reject."""
            calls.append(True)
            return "unexpected"

        from langgraph.prebuilt import ToolNode
        node = ToolNode([write_counter], awrap_tool_call=P.middleware_factory("main").awrap_tool_call, handle_tool_errors=True)
        builder = StateGraph(MessagesState)
        builder.add_node("tools", node)
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        graph = builder.compile()
        with self.assertRaises(NodeCancelledError) as raised:
            await graph.ainvoke({"messages": [AIMessage(content="", tool_calls=[{"name": "write_counter", "args": {}, "id": "1", "type": "tool_call"}])]}, {"configurable": {"thread_id": "backend"}})
        self.assertIsInstance(raised.exception.__cause__, P.PauseTerminated)
        self.assertEqual(calls, [])

    async def test_real_parallel_graph_terminate_drains_before_failing_sibling(self):
        gate = self.bind()
        entered, finish = asyncio.Event(), asyncio.Event()
        writes = []
        finalized = []

        class ArrivalBarrier(AgentMiddleware):
            async def awrap_tool_call(self, request, handler):
                # Force the second real graph task to arrive AFTER pause,
                # rather than depending on LangChain's callback scheduling.
                if request.tool_call["id"] == "blocked":
                    await entered.wait()
                return await handler(request)

        @tool
        async def write_counter(slot: int) -> str:
            """Offline parallel operation with a controlled completion barrier."""
            writes.append(slot)
            if slot == 1:
                await gate.request_pause()
                entered.set()
                try:
                    await finish.wait()
                    writes.append("saved")
                finally:
                    finalized.append(True)
            return "saved"

        model = OfflineModel(responses=[AIMessage(content="", tool_calls=[
            {"name": "write_counter", "args": {"slot": 1}, "id": "active", "type": "tool_call"},
            {"name": "write_counter", "args": {"slot": 2}, "id": "blocked", "type": "tool_call"},
        ])])
        graph = create_agent(model, tools=[write_counter], middleware=[ArrivalBarrier(), P.middleware_factory("main")])
        run = self.spawn(graph.ainvoke({"messages": [HumanMessage(content="go")]}, {"configurable": {"thread_id": "backend"}}))
        await entered.wait()
        await self.wait_status(gate, lambda s: s.waiting == 1 and s.in_flight == 1)
        self.assertEqual((await gate.terminate()).state, "terminating")
        self.assertFalse(run.done())
        self.assertEqual(finalized, [])
        finish.set()
        with self.assertRaises(NodeCancelledError) as raised:
            await run
        self.assertIsInstance(raised.exception.__cause__, P.PauseTerminated)
        self.assertEqual(writes, [1, "saved"])
        self.assertEqual(finalized, [True])
        self.assertEqual((await gate.status()).in_flight, 0)

    async def test_binding_cleanup_on_owner_cancellation_and_stale_release(self):
        local = P.PauseRegistry()
        entered = asyncio.Event()
        captured = []

        async def owner():
            async with local.bind("main", "backend") as gate:
                captured.append(gate)
                await gate.request_pause()
                entered.set()
                await gate.checkpoint()

        task = self.spawn(owner())
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(local.get("main", "backend"))
        self.assertEqual((await captured[0].status()).state, "terminated")
        replacement = local.register("main", "backend")
        self.assertFalse(await local.release("main", "backend", captured[0]))
        self.assertIs(local.get("main", "backend"), replacement)
        self.assertTrue(await local.release("main", "backend", replacement))

    async def test_duplicate_binding_rejected_and_release_waits_for_active_tools(self):
        local = P.PauseRegistry()
        gate = local.register("main", "backend")
        with self.assertRaises(RuntimeError):
            local.register("main", "backend")
        entered, finish = asyncio.Event(), asyncio.Event()

        async def active():
            async with gate.tool_call():
                entered.set()
                await finish.wait()

        task = self.spawn(active())
        await entered.wait()
        cleanup = self.spawn(local.release("main", "backend", gate))
        await self.wait_status(gate, lambda s: s.state == "terminating")
        self.assertFalse(cleanup.done())
        self.assertIs(local.get("main", "backend"), gate)
        finish.set()
        await task
        self.assertTrue(await cleanup)
        self.assertIsNone(local.get("main", "backend"))

    async def test_tool_exception_releases_count_and_keeps_exception(self):
        gate = self.bind()

        async def handler(request):
            await gate.request_pause()
            raise LookupError("offline failure")

        with self.assertRaisesRegex(LookupError, "offline failure"):
            await P.middleware_factory("main").awrap_tool_call(self.request(), handler)
        self.assertEqual((await gate.wait_paused()).in_flight, 0)

    async def test_missing_backend_id_is_rejected_and_model_runtime_has_no_config(self):
        self.assertFalse(hasattr(Runtime(), "config"))
        request = self.request()
        request.runtime.config = {"configurable": {}}

        async def handler(request):
            self.fail("Missing backend thread must not silently bypass control")

        with self.assertRaises(ValueError):
            await P.middleware_factory("main").awrap_tool_call(request, handler)
        with self.assertRaises(ValueError):
            P.registry.register("main", "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
