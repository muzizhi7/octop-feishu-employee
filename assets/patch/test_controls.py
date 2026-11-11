import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('octop_gateway.channels.feishu_controls',HERE/'staged/feishu_controls.py')
C=importlib.util.module_from_spec(spec);sys.modules[spec.name]=C;spec.loader.exec_module(C)
from octop_gateway.models import ChannelSubject,InboundMessage,MessageEvent as E

def request(text='原始请求',owner='ou_owner',chat='oc_chat'):
    meta={'sender_id':owner,'chat_id':chat,'chat_type':'p2p','to_handle':owner,'message_id':'om_original','bot_mentioned':True}
    subject=ChannelSubject(subject_id=owner,metadata=meta)
    msg=InboundMessage(channel_id='test-channel',channel_type='feishu',tenant_id='main',channel_subject=subject,content=[C.TextContent(text=text)],metadata=meta)
    return msg,subject

def pause(*,ask=True,options=True,decisions=None):
    actions=[{'name':'ask_user_question','args':{'questions':[{'question':'是否继续？','options':[{'label':'继续','description':'执行下一步'},{'label':'取消','description':'结束任务'}] if options else []}]}}] if ask else [{'name':'read_file','description':'读取测试文件','args':{'path':'test.md'}}]
    return NS(pending_id='abcd',ask_question_index=0,status='pending',action_requests=actions,review_configs=decisions,created_at=0)

class FakeChannel:
    channel_id='test-channel'
    adaptive_replies=False  # Baseline lifecycle tests explicitly exercise card mode.
    def __init__(self):
        self.api=[];self.cards={};self.messages=[];self._main_loop=asyncio.get_running_loop()
    _cut_partial_blocks=staticmethod(lambda x:x)
    _strip_think=staticmethod(lambda x:x)
    def get_debounce_key(self,m):return m.channel_subject.subject_id
    def _resolve_receive_id_type(self,*a):return 'open_id'
    async def _send_message(self,**kw):
        self.messages.append(kw);return {'code':0,'data':{'message_id':'om_card_'+str(len(self.messages))}}
    async def _reply_message(self,*a,**kw):return await self._send_message(**kw)
    async def _rate_limited_send(self,*a):self.messages.append(('text',a))
    async def reply_media(self,*a):pass
    def find(self,value,ident):
        if isinstance(value,dict):
            if value.get('element_id')==ident:return value
            for v in value.values():
                f=self.find(v,ident)
                if f is not None:return f
        elif isinstance(value,list):
            for v in value:
                f=self.find(v,ident)
                if f is not None:return f
    async def _card_api(self,method,path,payload):
        self.api.append((method,path,copy.deepcopy(payload)))
        if path=='/cardkit/v1/cards':
            cid=str(len(self.cards)+1);self.cards[cid]=json.loads(payload['data']);return {'code':0,'data':{'card_id':cid}}
        cid=path.split('/cards/')[1].split('/')[0];card=self.cards[cid]
        if method=='PUT' and '/elements' not in path:
            self.cards[cid]=json.loads(payload['card']['data'])
        elif method=='PATCH' and '/elements/' in path:
            element=self.find(card,path.rsplit('/',1)[1]);assert element is not None,path
            element.update(json.loads(payload['partial_element']))
        elif method=='POST' and path.endswith('/elements'):
            if any(self.find(card,e.get('element_id')) is not None for e in json.loads(payload['elements'])):
                return {'code':400,'msg':'duplicate element ID'}
            root=card['body']['elements'];idx=next(i for i,e in enumerate(root) if e.get('element_id')==payload['target_element_id'])
            if payload['type']=='insert_after':idx+=1
            root[idx:idx]=json.loads(payload['elements'])
        elif method=='DELETE':
            card['body']['elements']=[e for e in card['body']['elements'] if e.get('element_id')!=path.rsplit('/',1)[1]]
        elif method=='PATCH' and path.endswith('/settings'):
            card['config'].update(json.loads(payload['settings'])['config'])
        return {'code':0,'data':{}}

class FakeBridge:
    def __init__(self,plans):
        self.plans=list(plans);self.calls=[];self.pending=None;self.cancelled=[];self.forks=0;self.block=None
    def session_key(self,m):return m.tenant_id+':'+m.channel_subject.subject_id
    async def capture(self,t):return {'thread_id':'thread','checkpoint_id':'before','checkpoint_ns':'','user_id':1}
    def pending_for(self,t):return self.pending if self.pending and self.pending.status=='pending' else None
    def pending_by_id(self,t,p):return self.pending_for(t) if self.pending and self.pending.pending_id==p else None
    def expire(self,t):
        if self.pending:self.pending.status='expired'
    def cancel(self,t):self.cancelled.append(t.id)
    async def fork_before(self,t):
        self.forks+=1;self.pending=None;t.snapshot={**t.snapshot,'thread_id':'fork'+str(self.forks)}
    async def serialized(self,t,operation):
        if self.block:await self.block.wait()
        return await operation()
    async def stream(self,msg):
        self.calls.append(msg.model_copy(deep=True))
        plan=self.plans.pop(0)
        if callable(plan):
            async for event in plan(self,msg):yield event
        else:
            for event in plan:
                if isinstance(event,Exception):raise event
                yield event

class ControlsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.controllers=[]
    async def asyncTearDown(self):
        for c in self.controllers:await c.close()
        self.tmp.cleanup()
    def setup(self,plans,filename=None):
        filename=filename or f'state-{len(self.controllers)}.json'
        ch=FakeChannel();b=FakeBridge(plans);c=C.FeishuControls(ch,bridge=b,state_path=Path(self.tmp.name)/filename);c.api_interval=0
        c.cancel_grace=.02
        self.controllers.append(c);return c,ch,b
    async def start(self,c):
        m,s=request();await c.process(m,s);return c.turns[c.latest[c.bridge.session_key(m)]]
    async def click(self,c,t,action,**extra):
        return await c.dispatch(t.value(action,**extra),owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='event-'+str(len(c.seen)),form=extra.pop('form',None))
    async def finish(self,t):
        if t.job:await asyncio.wait_for(asyncio.shield(t.job),2)

    async def test_complete_has_real_callback_and_interactive_card(self):
        c,ch,b=self.setup([[E.tool_start('读取文件',tool_call_id='1'),E.tool_end('读取文件',tool_call_id='1',tool_input_summary='AGENTS.md'),E.delta('文档内容分析'),E.completed()]])
        t=await self.start(c)
        self.assertEqual(t.state,'completed');self.assertEqual(len(ch.messages),1)
        card=ch.cards[t.card];self.assertFalse(card['config']['streaming_mode'])
        self.assertIn('AGENTS.md',ch.find(card,'steps_md')['content'])
        self.assertIsNone(ch.find(card,'controls'))
        self.assertFalse(ch.find(card,'working_panel')['expanded'])

    async def test_completed_task_uses_conversation_and_rejects_legacy_replay(self):
        c,ch,b=self.setup([[E.delta('第一版'),E.completed()],[E.delta('第二版'),E.completed()]])
        t=await self.start(c)
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
        msg,subject=request('请修改第一版');await c.process(msg,subject)
        self.assertEqual([m.text for m in b.calls],['原始请求','请修改第一版'])
        self.assertEqual(b.forks,0);self.assertEqual(t.answer,'第一版')
        self.assertEqual(list(c.turns.values())[-1].answer,'第二版')

    async def test_retry_after_model_error_executes_again(self):
        c,ch,b=self.setup([[E.delta('部分'),E.error_event('服务暂不可用')],[E.delta('恢复完成'),E.completed()]])
        t=await self.start(c);self.assertEqual(t.state,'failed')
        await self.click(c,t,'retry');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'恢复完成');self.assertEqual(b.forks,1)

    async def test_stop_cancels_actual_task_and_does_not_cancel_worker(self):
        entered=asyncio.Event();exited=asyncio.Event()
        async def long(bridge,msg):
            try:
                yield E.tool_start('长任务',tool_call_id='wait')
                entered.set();await asyncio.Event().wait()
                yield E.delta('不应出现')
            finally:exited.set()
        c,ch,b=self.setup([long,[E.delta('下一条正常'),E.completed()]])
        msg,subject=request();worker=asyncio.create_task(c.process(msg,subject))
        await asyncio.wait_for(entered.wait(),2);t=c.turns[c.latest[b.session_key(msg)]]
        await self.click(c,t,'stop');self.assertFalse(await asyncio.wait_for(worker,2))
        self.assertTrue(exited.is_set());self.assertEqual(t.state,'stopped');self.assertEqual(len(b.cancelled),1)
        self.assertNotIn('不应出现',t.answer);self.assertEqual(b.forks,1)
        msg2,_=request('下一条');self.assertTrue(await c.process(msg2,subject))

    async def test_question_button_resumes_real_original_pause(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.text('旧的纯文本问题');yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'a');bridge.pending.status='approved'
            yield E.delta('继续执行完成');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        self.assertEqual(t.state,'waiting_answer');self.assertIsNotNone(ch.find(ch.cards[t.card],'answer_form'))
        self.assertNotIn('旧的纯文本问题',t.answer)
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'继续执行完成')
        self.assertIsNone(ch.find(ch.cards[t.card],'answer_form'));self.assertEqual(len(ch.messages),1)

    async def test_stopped_task_can_retry_original_request(self):
        entered=asyncio.Event()
        async def long(bridge,msg):
            yield E.delta('部分结果');entered.set();await asyncio.Event().wait()
        c,ch,b=self.setup([long,[E.delta('重试完成'),E.completed()]])
        msg,subject=request();worker=asyncio.create_task(c.process(msg,subject))
        await asyncio.wait_for(entered.wait(),2);t=c.turns[c.latest[b.session_key(msg)]]
        await self.click(c,t,'stop');await worker
        await self.click(c,t,'retry');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'重试完成')
        self.assertEqual(b.calls[-1].text,'原始请求');self.assertEqual(len(ch.messages),1)

    async def test_native_stop_drains_context_finalizer_in_same_task(self):
        from contextvars import ContextVar
        scope=ContextVar('test_scope',default='none');entered=asyncio.Event();cancelled=asyncio.Event();closed=[]
        async def long(bridge,msg):
            token=scope.set('active')
            try:
                entered.set();yield E.delta('部分结果')
                await cancelled.wait();yield E.delta('不展示')
                yield E.completed()
            finally:
                scope.reset(token);closed.append(True)
        c,ch,b=self.setup([long]);c.cancel_grace=.5
        b.cancel=lambda turn:cancelled.set()
        msg,subject=request();worker=asyncio.create_task(c.process(msg,subject))
        await entered.wait();t=c.turns[c.latest[b.session_key(msg)]]
        await self.click(c,t,'stop');await worker
        self.assertEqual(closed,[True]);self.assertEqual(t.answer,'部分结果');self.assertEqual(t.state,'stopped')

    async def test_question_tool_transitions_waiting_to_done_after_answer(self):
        async def initial(bridge,msg):
            yield E.tool_start('向你提问',tool_call_id='ask')
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        async def resumed(bridge,msg):
            bridge.pending.status='approved';yield E.delta('收到');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        self.assertEqual(t.calls[0]['state'],'waiting')
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.calls[0]['state'],'done')

    async def test_cancelled_question_can_retry(self):
        async def initial(bridge,msg):
            yield E.tool_start('向你提问',tool_call_id='ask')
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        c,ch,b=self.setup([initial,[E.delta('重新执行'),E.completed()]])
        t=await self.start(c);await self.click(c,t,'stop')
        self.assertEqual(t.state,'cancelled')
        self.assertEqual(t.calls[0]['state'],'cancelled')
        await self.click(c,t,'retry');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'重新执行')

    async def test_custom_answer_form_and_empty_validation(self):
        async def initial(bridge,msg):
            bridge.pending=pause(options=False);yield E.text('问题');yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'我的选择');bridge.pending.status='approved';yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        value=t.value('answer')
        with self.assertRaises(ValueError):
            await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='empty',form={})
        self.assertEqual(len(b.calls),1)
        await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='filled',form={'answer':'我的选择'});await self.finish(t)
        self.assertEqual(t.state,'completed')

    async def test_confirmation_uses_pending_id_not_model_command(self):
        async def initial(bridge,msg):
            bridge.pending=pause(ask=False);yield E.text('批准请求');yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'/approve abcd');bridge.pending.status='approved';yield E.delta('实际继续');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        self.assertEqual(t.state,'waiting_approval')
        await self.click(c,t,'approve');await self.finish(t)
        self.assertEqual(t.answer,'实际继续');self.assertEqual(t.state,'completed')

    async def test_cancelling_pause_expires_it_and_keeps_next_turn_usable(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        c,ch,b=self.setup([initial]);t=await self.start(c);record=b.pending
        await self.click(c,t,'stop')
        self.assertEqual(record.status,'expired');self.assertEqual(t.state,'cancelled');self.assertEqual(b.forks,1)

    async def test_disallowed_confirmation_cannot_be_forged(self):
        async def initial(bridge,msg):
            bridge.pending=pause(ask=False,decisions=[{'action_name':'read_file','allowed_decisions':['edit']}]);yield E.text('审批');yield E.completed()
        c,ch,b=self.setup([initial]);t=await self.start(c)
        with self.assertRaises(ValueError):await self.click(c,t,'approve')
        self.assertEqual(len(b.calls),1)

    async def test_wrong_owner_chat_message_or_capability_rejected(self):
        c,ch,b=self.setup([[E.completed()]]);t=await self.start(c);value=t.value('regenerate')
        for field,bad in [('owner','ou_other'),('chat','oc_other'),('message_id','om_other')]:
            kw={'owner':t.owner,'chat':t.chat,'message_id':t.card_message,'event_id':'x'};kw[field]=bad
            with self.assertRaises(ValueError):await c.dispatch(value,**kw)
        with self.assertRaises(ValueError):
            await c.dispatch({**value,'capability':'bad'},owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='x')
        self.assertEqual(len(b.calls),1)

    async def test_duplicate_click_cannot_execute_twice(self):
        c,ch,b=self.setup([[E.error_event('失败')],[E.delta('恢复正文'),E.completed()]]);t=await self.start(c);value=t.value('retry')
        await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='same')
        with self.assertRaises(ValueError):
            await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='same')
        await self.finish(t);self.assertEqual(len(b.calls),2)

    async def test_old_card_after_new_message_is_not_executable(self):
        c,ch,b=self.setup([[E.completed()],[E.completed()]]);t=await self.start(c)
        m,s=request('新请求');await c.process(m,s)
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
        self.assertEqual(len(b.calls),2)

    async def test_pending_index_stale_and_wrong_action_rejected(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        c,ch,b=self.setup([initial]);t=await self.start(c);value=t.value('answer',answer='a')
        b.pending.ask_question_index=1
        with self.assertRaises(ValueError):
            await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='old')
        with self.assertRaises(ValueError):await self.click(c,t,'approve')

    async def test_queued_stop_does_not_cancel_other_active_work(self):
        c,ch,b=self.setup([[E.error_event('失败')],[E.completed()]]);t=await self.start(c)
        b.block=asyncio.Event()
        await self.click(c,t,'retry');self.assertEqual(t.state,'queued')
        await self.click(c,t,'stop')
        if t.job:
            try:await t.job
            except asyncio.CancelledError:pass
        self.assertEqual(t.state,'stopped');self.assertEqual(len(b.calls),1);self.assertFalse(b.cancelled);self.assertEqual(b.forks,0)

    async def test_journal_restores_restart_as_interrupted_and_enables_retry(self):
        c,ch,b=self.setup([[E.completed()]]);t=await self.start(c)
        t.state='running';c.save();await c.close()
        c2=C.FeishuControls(ch,bridge=FakeBridge([[E.completed()]]),state_path=c.path);c2.api_interval=0;self.controllers.append(c2)
        await asyncio.sleep(0)
        restored=c2.turns[t.id];self.assertEqual(restored.state,'interrupted')
        self.assertEqual(restored.message.text,'原始请求');self.assertIsNotNone(restored.snapshot)

    async def test_callbacks_return_without_waiting_for_model(self):
        c,ch,b=self.setup([[E.error_event('失败')],[E.delta('恢复正文'),E.completed()]]);t=await self.start(c)
        data=NS(header=NS(event_id='cb1'),event=NS(operator=NS(open_id=t.owner),context=NS(open_chat_id=t.chat,open_message_id=t.card_message),action=NS(value=t.value('retry'),form_value={})))
        before=len(b.calls);response=c.callback(data)
        self.assertIsNotNone(response.toast);self.assertEqual(len(b.calls),before)
        for _ in range(20):
            await asyncio.sleep(0)
            if t.job:break
        await self.finish(t);self.assertEqual(len(b.calls),2)

    async def test_expired_question_disables_old_controls_and_offers_retry(self):
        async def initial(bridge,msg):
            yield E.tool_start('向你提问',tool_call_id='ask')
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        c,ch,b=self.setup([initial]);t=await self.start(c);old=t.value('answer',answer='a')
        b.pending.status='expired';await c.expire_waits()
        self.assertEqual(t.state,'expired');self.assertIsNone(ch.find(ch.cards[t.card],'answer_form'))
        self.assertEqual(t.calls[0]['state'],'expired')
        button=ch.find(ch.cards[t.card],'controls')['columns'][0]['elements'][0]
        self.assertEqual(button['behaviors'][0]['value']['action'],'retry')
        with self.assertRaises(ValueError):
            await c.dispatch(old,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='expired')

    async def test_real_multi_question_collection_resumes_only_after_last_answer(self):
        from octop.infra.gateway.hitl.coordinator import HitlChannelCoordinator
        hitl=HitlChannelCoordinator()
        questions=[{'question':'第一题','options':[{'label':'甲'}]},{'question':'第二题','options':[{'label':'乙'}]}]
        record=hitl.store.register(thread_id='thread',agent_id='main',user_id=1,session_key='session',channel_type='feishu',action_requests=[{'name':'ask_user_question','args':{'questions':questions}}],review_configs=None)
        async def initial(bridge,msg):
            bridge.pending=record;yield E.text('问题');yield E.completed()
        async def answer(bridge,msg):
            response,next_card=hitl.collect_ask_reply(record,msg.text,locale='zh')
            if next_card:
                yield E.text(next_card)
            else:
                self.assertIn('第一题',response);self.assertIn('第二题',response)
                record.status='approved';yield E.delta('两题都已收到')
            yield E.completed()
        c,ch,b=self.setup([initial,answer,answer]);t=await self.start(c)
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.state,'waiting_answer');self.assertEqual(record.ask_question_index,1)
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertIn('两题都已收到',t.answer);self.assertEqual(len(ch.messages),1)

if __name__=='__main__':unittest.main(verbosity=2)
