import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

HERE=Path(__file__).resolve().parent

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,HERE/'staged'/path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result)
    return result

projection=module('controls_owned_projection','octop/infra/gateway/process/stream_project.py')
agents=module('controls_owned_agents','octop/infra/agents/manager.py')

@asynccontextmanager
async def gate(*args):
    yield

class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def manager(self,inner):
        runtime=NS(
            _harness_manager=NS(stream=inner),
            _thread_execution_lock=gate,_track_invocation=gate,
            _apply_pending_bootstrap_graph_refresh=lambda *a:None,
            _prepare_stream_request=lambda a,r:r,
            _stream_model_label=lambda *a:'test',
            get_agent=lambda a:NS(workspace=None),
        )
        runtime.stream=lambda a,r:agents.AgentManager.stream(runtime,a,r)
        return runtime

    async def test_pause_closes_nested_scopes_in_original_context(self):
        scope=ContextVar('pause_test',default='idle');closed=[]
        async def inner(agent,request):
            token=scope.set('active')
            try:
                yield {'type':'hitl_required','request':{'action_requests':[{'name':'read_file','args':{'path':'test.md'}}]}}
                self.fail('Paused stream must not run more work')
            finally:
                scope.reset(token);closed.append(True)
        state=projection.StreamProjectionState()
        from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator,HitlStreamContext
        hitl=HitlChannelCoordinator();ctx=HitlStreamContext(thread_id='test',agent_id='main',user_id=1,session_key='test',channel_type='feishu')
        events=[e async for e in projection.project_stream(self.manager(inner),'main',{'thread_id':'test'},projection_state=state,hitl_coordinator=hitl,hitl_ctx=ctx)]
        self.assertTrue(events);self.assertTrue(state.hitl_paused);self.assertEqual(closed,[True]);self.assertEqual(scope.get(),'idle')

    async def test_consumer_close_releases_underlying_runtime_scope(self):
        scope=ContextVar('close_test',default='idle');closed=[]
        async def inner(agent,request):
            token=scope.set('active')
            try:
                yield {'type':'token','content':'partial'}
                await asyncio.Event().wait()
            finally:
                scope.reset(token);closed.append(True)
        events=projection.project_stream(self.manager(inner),'main',{'thread_id':'test'})
        await anext(events);await events.aclose()
        self.assertEqual(closed,[True]);self.assertEqual(scope.get(),'idle')

if __name__=='__main__':unittest.main(verbosity=2)
