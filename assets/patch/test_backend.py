import asyncio
from types import SimpleNamespace as NS
import unittest
from test_controls import C,request
from langchain_core.messages import HumanMessage,AIMessage
from langgraph.graph import StateGraph,MessagesState,START,END
from langgraph.checkpoint.memory import InMemorySaver

class Registry:
    def __init__(self):
        self.bound='original'
        self.rows={'original':NS(agent_id='main',user_id=1,title='原对话',model_ref=None,reasoning_mode=None,reasoning_effort=None,conversation_mode='craft')}
    async def get_or_create_by_key(self,**kw):return self.bound
    def get_thread(self,t):return self.rows.get(t)
    def create_thread(self,**kw):
        t='branch'+str(len(self.rows));self.rows[t]=NS(**kw);return t
    def update_composer(self,t,**kw):
        for k,v in kw.items():setattr(self.rows[t],k,v)
    async def rebind(self,**kw):self.bound=kw['thread_id']

class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_confirmation_resumes_checkpoint_exactly_once(self):
        from langgraph.types import interrupt,Command
        from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator,HitlSlashOutcome
        from octop.infra.gateway.slash.parser import parse_slash
        from test_controls import FakeBridge,FakeChannel
        from pathlib import Path
        import tempfile
        executions=[]
        def operation(state):
            decision=interrupt({'operation':'readonly_test'})
            self.assertEqual(decision['decisions'],[{'type':'approve'}])
            executions.append('read-only operation')
            return {'messages':[AIMessage(content='已确认并完成')]}
        graph=StateGraph(MessagesState);graph.add_node('operation',operation);graph.add_edge(START,'operation');graph.add_edge('operation',END)
        graph=graph.compile(checkpointer=InMemorySaver());cfg={'configurable':{'thread_id':'approval'}}
        await graph.ainvoke({'messages':[HumanMessage(content='只读验证')]},cfg)
        hitl=HitlChannelCoordinator()
        record=hitl.store.register(thread_id='approval',agent_id='main',user_id=1,session_key='session',channel_type='feishu',action_requests=[{'name':'read_file','args':{'path':'test.md'}}],review_configs=[{'action_name':'read_file','allowed_decisions':['approve','reject']}])
        async def resume(agent,thread,decisions):
            self.assertEqual(thread,'approval')
            result=await graph.ainvoke(Command(resume={'decisions':decisions}),cfg)
            yield {'type':'token','content':result['messages'][-1].content}
        manager=NS(resume_hitl=resume,get_agent=lambda a:NS(workspace=None))
        from octop_gateway.models import MessageEvent as E
        async def initial(bridge,msg):
            bridge.pending=record;yield E.text('确认');yield E.completed()
        async def resolution(bridge,msg):
            self.assertEqual(msg.text,'/approve '+record.pending_id)
            outcome=HitlSlashOutcome()
            ctx=NS(agent_id='main',user_id=1,session_key='session',channel_type='feishu')
            async for event in hitl.iter_slash_resolution(parse_slash(msg.text),ctx,agent_manager=manager,locale='zh',outcome=outcome):
                yield event
            self.assertTrue(outcome.completed_turn)
            yield E.completed()
        bridge=FakeBridge([initial,resolution]);channel=FakeChannel()
        with tempfile.TemporaryDirectory() as temporary:
            controls=C.FeishuControls(channel,bridge=bridge,state_path=Path(temporary)/'state.json');controls.api_interval=0
            try:
                msg,subject=request();await controls.process(msg,subject)
                turn=next(iter(controls.turns.values()));value=turn.value('approve')
                self.assertEqual(turn.state,'waiting_approval');self.assertEqual(executions,[])
                await controls.dispatch(value,owner=turn.owner,chat=turn.chat,message_id=turn.card_message,event_id='confirm')
                await turn.job
                self.assertEqual(executions,['read-only operation']);self.assertEqual(record.status,'approved')
                self.assertEqual(turn.state,'completed')
                with self.assertRaises(ValueError):
                    await controls.dispatch(value,owner=turn.owner,chat=turn.chat,message_id=turn.card_message,event_id='duplicate')
                self.assertEqual(len(executions),1)
            finally:
                await controls.close()

    async def test_real_checkpoint_fork_uses_before_turn_and_keeps_old_transcript(self):
        graph=StateGraph(MessagesState);graph.add_node('done',lambda state:{});graph.add_edge(START,'done');graph.add_edge('done',END)
        graph=graph.compile(checkpointer=InMemorySaver())
        cfg={'configurable':{'thread_id':'original'}}
        await graph.aupdate_state(cfg,{'messages':[HumanMessage(content='之前的问题',id='h1'),AIMessage(content='之前的上下文',id='a1')]})
        registry=Registry();lock=asyncio.Lock()
        manager=NS(get_agent=lambda a:NS(graph=graph),_thread_execution_lock=lambda a,t:lock)
        processor=NS(_thread_registry=registry,_agent_manager=manager,_agent_repo=NS(get=lambda a:NS(user_id=1)))
        bridge=C.OctopBridge(processor,None,None)
        m,s=request();t=C.Turn(id='test',message=m,subject=s,owner='ou_owner',chat='oc_chat',session='session')
        t.snapshot=await bridge.capture(t)
        await graph.aupdate_state(cfg,{'messages':[HumanMessage(content='原始请求',id='h2'),AIMessage(content='不满意的回答',id='a2')]},as_node='done')
        await bridge.fork_before(t)
        state=await graph.aget_state({'configurable':{'thread_id':registry.bound}})
        self.assertEqual([m.content for m in state.values['messages']],['之前的问题','之前的上下文'])
        old=await graph.aget_state(cfg)
        self.assertEqual(len(old.values['messages']),4)
        self.assertIsNotNone(t.snapshot['checkpoint_id'])
        await bridge.fork_before(t)
        again=await graph.aget_state({'configurable':{'thread_id':registry.bound}})
        self.assertEqual([m.content for m in again.values['messages']],['之前的问题','之前的上下文'])

    async def test_real_pending_store_is_scoped_to_session_and_agent(self):
        from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator
        hitl=HitlChannelCoordinator()
        m,s=request();t=C.Turn(id='test',message=m,subject=s,owner='ou_owner',chat='oc_chat',session='session')
        processor=NS(_hitl=hitl,_agent_repo=NS(get=lambda a:NS(user_id=1)))
        bridge=C.OctopBridge(processor,None,None)
        record=hitl.store.register(thread_id='thread',agent_id='main',user_id=1,session_key='session',channel_type='feishu',action_requests=[{'name':'read_file','args':{}}],review_configs=None)
        self.assertIs(bridge.pending_by_id(t,record.pending_id),record)
        t.pending=record
        bridge.expire(t)
        self.assertIsNone(bridge.pending_by_id(t,record.pending_id))
        self.assertEqual(record.status,'expired')

if __name__=='__main__':unittest.main(verbosity=2)
