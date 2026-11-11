"""Real API-shape routing, native topic adoption and isolation regressions."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch
import test_adaptive as adaptive
from test_controls import C,E,request

class TopicTests(adaptive.AdaptiveTests):
    # Reuse fixtures only, without rerunning the inherited adaptive test suite.
    def setup(self, plans):
        c,ch,b=super().setup(plans)
        original=ch._card_api
        async def api(method,path,payload):
            result=await original(method,path,payload)
            if method=='POST' and path.startswith('/im/v1/messages/') and payload.get('reply_in_thread'):
                source=path.split('/')[4]
                result=copy.deepcopy(result)
                result['data']['thread_id']='omt_'+source
            return result
        ch._card_api=api
        return c,ch,b

    def group(self,text='请开一个话题，读取当前时间',source='om_root',thread=''):
        m,s=request(text)
        s.chat_type='group';s.subject_id=thread or 'oc_group'
        m.metadata.update(chat_type='group',chat_id='oc_group',to_handle='oc_group',message_id=source)
        if thread:m.metadata['thread_id']=thread
        s.metadata=m.metadata.copy()
        return m,s

    async def test_ordinary_group_question_quotes_without_creating_a_topic(self):
        c,ch,b=self.setup([[E.delta('今天完成三件事。'),E.completed()],[E.delta('补充说明。'),E.completed()]])
        m,s=self.group('不错，今天做了什么',source='om_question');await c.process(m,s)
        first=list(c.turns.values())[-1]
        self.assertFalse(ch.native_requests[0][1]['reply_in_thread'])
        self.assertNotIn('thread_id',first.message.metadata)
        m,s=self.group('再简单一点',source='om_follow');await c.process(m,s)
        last=list(c.turns.values())[-1]
        self.assertEqual(first.session,last.session)
        self.assertEqual(b.forks,0)

    async def test_long_or_tool_based_group_answer_does_not_force_topic(self):
        c,ch,b=self.setup([[E.tool_start('read_file',tool_call_id='one'),E.tool_end('read_file',tool_call_id='one'),E.delta('完整说明。'*60),E.completed()]])
        m,s=self.group('总结今天的工作',source='om_summary');await c.process(m,s)
        self.assertFalse(ch.native_requests[0][1]['reply_in_thread'])
        self.assertTrue(list(c.turns.values())[-1].card)

    async def test_explicit_no_topic_beats_topic_words_and_members_are_isolated(self):
        c,ch,b=self.setup([[E.delta('明白'),E.completed()],[E.delta('好的'),E.completed()]])
        m,s=self.group('不用开话题，直接在群里回复：今天做什么',source='om_a')
        self.assertFalse(C.wants_new_topic(m));await c.process(m,s);first=list(c.turns.values())[-1]
        m,s=self.group('今天做什么',source='om_b');m.metadata['sender_id']='ou_other';s.metadata['sender_id']='ou_other'
        await c.process(m,s);second=list(c.turns.values())[-1]
        self.assertNotEqual(first.session,second.session);self.assertFalse(first.superseded)

    async def test_private_short_reply_quotes_source_without_starting_topic(self):
        c,ch,b=self.setup([[E.delta('在。'),E.completed()]])
        t=await self.start(c,'你好')
        path,payload=ch.native_requests[0]
        self.assertEqual(path,'/im/v1/messages/om_original/reply')
        self.assertFalse(payload['reply_in_thread'])
        self.assertTrue(t.native_message)

    async def test_group_root_adopts_native_thread_before_capture_and_processing(self):
        c,ch,b=self.setup([[E.delta('已读取。'),E.completed()]])
        captured=[]
        original=b.capture
        async def capture(t):
            captured.append((t.session,t.message.metadata.copy(),t.message.group_context))
            return await original(t)
        b.capture=capture
        m,s=self.group();m.group_context='unrelated group chatter'
        await c.process(m,s)
        t=list(c.turns.values())[-1]
        self.assertEqual(t.session,'main:omt_om_root')
        self.assertEqual(t.subject.subject_id,'omt_om_root')
        self.assertEqual(captured[0][1]['thread_id'],'omt_om_root')
        self.assertIsNone(captured[0][2])
        self.assertEqual(b.calls[0].channel_subject.subject_id,'omt_om_root')
        self.assertNotIn('main:feishu-root:om_root',c.latest)
        self.assertEqual(ch.native_requests[0][0],'/im/v1/messages/om_root/reply')
        self.assertTrue(ch.native_requests[0][1]['reply_in_thread'])

    async def test_followup_uses_same_topic_but_other_roots_stay_independent(self):
        c,ch,b=self.setup([[E.delta('第一条'),E.completed()],[E.delta('另一个任务'),E.completed()],[E.delta('继续第一条'),E.completed()]])
        m,s=self.group(source='om_one');await c.process(m,s);first=list(c.turns.values())[-1]
        m,s=self.group(source='om_two');await c.process(m,s);second=list(c.turns.values())[-1]
        self.assertFalse(first.superseded)
        self.assertNotEqual(first.session,second.session)
        m,s=self.group(source='om_followup',thread='omt_om_one');await c.process(m,s)
        last=list(c.turns.values())[-1]
        self.assertEqual(last.session,first.session)
        self.assertTrue(first.superseded);self.assertFalse(second.superseded)

    async def test_missing_thread_id_does_not_execute_with_group_global_history(self):
        c,ch,b=adaptive.AdaptiveTests.setup(self,[[E.delta('不得执行'),E.completed()]])
        m,s=self.group();await c.process(m,s)
        self.assertEqual(len(b.calls),0)
        self.assertEqual(list(c.turns.values())[-1].state,'failed')

    async def test_media_reply_failure_never_falls_back_into_main_group(self):
        from octop_gateway.models import ChannelSubject
        from test_media import F
        class Channel(F.FeishuChannel):
            async def _reply_message(self,*args,**kwargs):return {'code':400,'msg':'failed'}
            async def _send_message(self,**kwargs):raise AssertionError('topic leaked into main group')
        with self.assertRaises(RuntimeError):
            await Channel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'))._deliver(ChannelSubject(subject_id='omt_topic',metadata={'message_id':'om_source','thread_id':'omt_topic','chat_type':'group'}),msg_type='file',content='{}')

    async def test_pause_continue_buttons_control_original_execution_without_replay(self):
        from test_pause import P
        gate=P.PauseGate();entered=asyncio.Event();release=asyncio.Event();writes=[]
        async def stream(bridge,msg):
            async with gate.tool_call():
                writes.append('first');entered.set();await release.wait()
            await gate.checkpoint()
            async with gate.tool_call():writes.append('second')
            yield E.delta('完成');yield E.completed()
        c,ch,b=self.setup([stream]);b.start_pause=lambda turn:gate
        async def cleanup(turn):await gate.terminate()
        b.end_pause=cleanup
        m,s=request();job=asyncio.create_task(c.process(m,s));await entered.wait()
        t=list(c.turns.values())[-1]
        await self.ready(lambda:bool(t.card_message))
        value=t.value('pause');await self.click(c,t,'pause')
        self.assertEqual(t.state,'pausing');self.assertEqual(writes,['first'])
        with self.assertRaises(ValueError):
            await c.dispatch(value,owner=t.owner,chat=t.chat,message_id=t.card_message,event_id='duplicate-pause')
        release.set();await self.ready(lambda:t.state=='paused')
        self.assertEqual(writes,['first']);self.assertFalse(ch.find(ch.cards[t.card],'working_panel')['expanded'])
        await self.click(c,t,'continue');await job
        self.assertEqual(writes,['first','second']);self.assertEqual(len(b.calls),1)
        self.assertEqual(t.state,'completed');self.assertIsNone(t.gate)

    async def test_stop_while_paused_releases_waiter_and_never_executes_next_write(self):
        from test_pause import P
        gate=P.PauseGate();entered=asyncio.Event();release=asyncio.Event();writes=[]
        async def stream(bridge,msg):
            async with gate.tool_call():entered.set();await release.wait()
            await gate.checkpoint();writes.append('forbidden');yield E.completed()
        c,ch,b=self.setup([stream]);b.start_pause=lambda turn:gate
        async def cleanup(turn):await gate.terminate()
        b.end_pause=cleanup
        m,s=request();job=asyncio.create_task(c.process(m,s));await entered.wait()
        t=list(c.turns.values())[-1];await self.ready(lambda:bool(t.card_message))
        await self.click(c,t,'pause');release.set();await self.ready(lambda:t.state=='paused')
        await self.click(c,t,'stop');await job
        self.assertEqual(writes,[]);self.assertEqual(t.state,'stopped');self.assertIsNone(t.gate)

    async def test_topic_followup_activation_only_for_known_initiator_and_chat(self):
        from test_media import F
        from types import SimpleNamespace
        channel=F.FeishuChannel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'))
        m,s=self.group(source='om_root',thread='omt_known')
        channel._controls=SimpleNamespace(turns={'turn':SimpleNamespace(chat='oc_group',owner=m.metadata['sender_id'],message=m)})
        raw={'sender':{'sender_id':m.metadata['sender_id']},'chat_id':'oc_group','chat_type':'group','thread_id':'omt_known','message_id':'om_follow','message_type':'text','content':json.dumps({'text':'继续'}),'mentions':[]}
        from octop_gateway.channels.feishu_preferences import DEFAULTS
        with patch('octop_gateway.channels.feishu_preferences.load_preferences',return_value={**DEFAULTS,'group_trigger':'topic'}):
            self.assertTrue(channel.parse_inbound(raw).metadata['bot_mentioned'])
        self.assertFalse(channel.parse_inbound(raw).metadata['bot_mentioned'])
        self.assertFalse(channel.parse_inbound({**raw,'sender':{'sender_id':'ou_other'}}).metadata['bot_mentioned'])
        self.assertFalse(channel.parse_inbound({**raw,'chat_id':'oc_other'}).metadata['bot_mentioned'])
        self.assertFalse(channel.parse_inbound({**raw,'thread_id':'omt_other'}).metadata['bot_mentioned'])

    async def test_stop_during_capture_cannot_start_backend_after_capture_returns(self):
        c,ch,b=self.setup([[E.delta('不得执行'),E.completed()]])
        entered=asyncio.Event();release=asyncio.Event();original=b.capture
        async def capture(t):entered.set();await release.wait();return await original(t)
        b.capture=capture
        m,s=self.group();job=asyncio.create_task(c.process(m,s));await entered.wait()
        t=list(c.turns.values())[-1];self.assertTrue(t.card_message)
        await self.click(c,t,'stop');self.assertEqual(t.state,'stopped')
        release.set();await job
        self.assertEqual(b.calls,[]);self.assertEqual(t.state,'stopped')

# Avoid inherited tests being duplicated by unittest's method discovery.
for _name in dir(adaptive.AdaptiveTests):
    if _name.startswith('test_') and _name not in TopicTests.__dict__:
        setattr(TopicTests,_name,None)
