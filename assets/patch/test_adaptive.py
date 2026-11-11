"""Adaptive native bubbles, delayed task cards and real contextual document jobs."""
import asyncio,json
import unittest
import test_controls as fixture
from test_controls import C,E,request,pause

DRAFT='# 请假报告\n\n申请人：【姓名】\n部门：【部门】\n\n'+('请假期间完成工作交接，具体事项由指定同事协助处理。\n'*8)

class AdaptiveTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ControlsTests.asyncSetUp
    asyncTearDown=fixture.ControlsTests.asyncTearDown
    click=fixture.ControlsTests.click
    finish=fixture.ControlsTests.finish

    def setup(self,plans):
        c,ch,b=fixture.ControlsTests.setup(self,plans)
        c.adaptive=True;c.presentation_delay=.03
        ch.native_uuids={};ch.native_requests=[]
        original=ch._card_api
        async def api(method,path,payload):
            if path.startswith('/im/v1/messages') and method=='POST':
                ch.native_requests.append((path,payload.copy()))
                key=payload['uuid']
                if key not in ch.native_uuids:ch.native_uuids[key]=await ch._send_message(**payload)
                return ch.native_uuids[key]
            return await original(method,path,payload)
        ch._card_api=api
        return c,ch,b

    async def start(self,c,text='随便问一个普通问题'):
        m,s=request(text);await c.process(m,s);return c.turns[c.latest[c.bridge.session_key(m)]]

    async def ready(self,predicate):
        async with asyncio.timeout(1):
            while not predicate():await asyncio.sleep(.002)

    async def test_short_answer_uses_native_bubble_based_on_output(self):
        for prompt,answer in [('你好','在。'),('1+1是多少','2'),('请简单解释什么是缓存','缓存把常用数据临时存起来，减少重复读取。')]:
            with self.subTest(prompt=prompt):
                c,ch,b=self.setup([[E.delta(answer),E.completed()]])
                t=await self.start(c,prompt)
                self.assertEqual(t.state,'completed');self.assertFalse(t.card);self.assertTrue(t.native_message)
                self.assertEqual(len(ch.cards),0);self.assertEqual(ch.messages[0]['msg_type'],'text')
                self.assertEqual(json.loads(ch.messages[0]['content'])['text'],answer)
                self.assertFalse(c.can_regenerate(t))
                with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
                await asyncio.sleep(.04);self.assertEqual(len(ch.cards),0)

    async def test_native_does_not_leak_hidden_thinking(self):
        c,ch,b=self.setup([[E.delta('<think>secret reasoning</think>在。'),E.completed()]])
        ch._strip_think=lambda s:s.split('</think>')[-1]
        t=await self.start(c)
        # A raw think tag may promote early to a card; either output must be clean.
        if t.native_message:self.assertEqual(json.loads(ch.messages[0]['content'])['text'],'在。')
        else:self.assertNotIn('secret reasoning',ch.find(ch.cards[t.card],'md1')['content'])

    async def test_formatted_content_keeps_card_and_accepts_followup(self):
        c,ch,b=self.setup([[E.delta('1. 会前准备议程\n2. 会后确认负责人'),E.completed()],[E.delta('1. 精简议程\n2. 明确责任'),E.completed()]])
        t=await self.start(c,'写两条会议建议')
        self.assertTrue(t.card);self.assertFalse(t.native_message)
        self.assertEqual(ch.cards[t.card]['config']['width_mode'],'default')
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
        m,s=request('请再简洁一点');await c.process(m,s)
        self.assertNotEqual(list(c.turns.values())[-1].card,t.card)
        self.assertEqual(b.forks,0);self.assertEqual(len(ch.messages),2)

    async def test_long_single_paragraph_is_not_forced_into_tiny_bubble(self):
        c,ch,b=self.setup([[E.delta('正文内容'*50),E.completed()]])
        t=await self.start(c)
        self.assertTrue(t.card);self.assertEqual(ch.cards[t.card]['config']['width_mode'],'default')

    async def test_slow_short_answer_shows_stoppable_card_then_compacts(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def slow(bridge,msg):
            entered.set();await release.wait();yield E.delta('现在可以了。');yield E.completed()
        c,ch,b=self.setup([slow]);m,s=request('慢速请求');job=asyncio.create_task(c.process(m,s))
        await entered.wait();t=c.turns[c.latest[b.session_key(m)]]
        await self.ready(lambda:bool(t.card_message))
        self.assertEqual(ch.find(ch.cards[t.card],'controls')['columns'][0]['elements'][0]['text']['content'],'停止')
        release.set();await job
        self.assertEqual(t.state,'completed');self.assertEqual(ch.cards[t.card]['config']['width_mode'],'compact')
        self.assertFalse(t.native_message);self.assertEqual(len(ch.messages),1)

    async def test_slow_task_stop_actually_cancels_backend(self):
        entered=asyncio.Event();closed=[]
        async def slow(bridge,msg):
            try:entered.set();await asyncio.Event().wait();yield E.completed()
            finally:closed.append(True)
        c,ch,b=self.setup([slow]);m,s=request();job=asyncio.create_task(c.process(m,s))
        await entered.wait();t=c.turns[c.latest[b.session_key(m)]]
        await self.ready(lambda:bool(t.card_message))
        await self.click(c,t,'stop');await job
        self.assertEqual(t.state,'stopped');self.assertEqual(closed,[True]);self.assertEqual(b.cancelled,[t.id])

    async def test_tools_and_questions_promote_immediately_without_delay(self):
        async def asking(bridge,msg):
            yield E.tool_start('ask_user_question',tool_call_id='ask')
            bridge.pending=pause();yield E.completed()
        async def resumed(bridge,msg):
            bridge.pending.status='approved';yield E.delta('完整回答');yield E.completed()
        c,ch,b=self.setup([asking,resumed]);c.presentation_delay=100
        t=await self.start(c)
        self.assertEqual(t.state,'waiting_answer');self.assertTrue(t.card_message)
        self.assertIsNotNone(ch.find(ch.cards[t.card],'answer_form'))
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(len(ch.messages),1)

    async def test_lost_native_response_is_idempotent_on_retry(self):
        c,ch,b=self.setup([[E.delta('原生短回复'),E.completed()]])
        original=ch._card_api;dropped=[]
        async def api(method,path,payload):
            result=await original(method,path,payload)
            if '/im/v1/messages' in path and not dropped:dropped.append(True);return {'code':-1}
            return result
        ch._card_api=api
        with self.assertLogs(C.LOG.name,level='WARNING'):t=await self.start(c)
        self.assertTrue(t.render_dirty);self.assertFalse(t.native_message)
        await c.refresh(t,force=True)
        self.assertTrue(t.native_message);self.assertFalse(t.render_dirty)
        self.assertEqual(len(ch.messages),1);self.assertEqual(len(ch.native_requests),2)
        self.assertEqual(ch.native_requests[0][1]['uuid'],ch.native_requests[1][1]['uuid'])

    async def test_topic_native_reply_preserves_thread(self):
        c,ch,b=self.setup([[E.delta('话题内的短回复'),E.completed()]])
        m,s=request();m.metadata['thread_id']='omt_topic';s.metadata=m.metadata
        await c.process(m,s)
        path,payload=ch.native_requests[0]
        self.assertEqual(path,'/im/v1/messages/om_original/reply');self.assertTrue(payload['reply_in_thread'])

    async def test_document_export_creates_new_turn_in_current_context(self):
        for fmt in ['docx','pdf']:
            with self.subTest(fmt=fmt):
                c,ch,b=self.setup([[E.delta(DRAFT),E.completed()],[E.tool_start('send_file_to_user',tool_call_id='send'),E.tool_end('send_file_to_user',tool_call_id='send'),E.delta('文件已发。'),E.completed()]])
                source=await self.start(c,'请写一份请假报告模板');source_card=source.card
                self.assertTrue(c.can_export(source))
                await self.click(c,source,'export',format=fmt);await self.finish(source)
                child=c.turns[c.latest[source.session]]
                self.assertNotEqual(source.id,child.id);self.assertEqual(source.answer,DRAFT)
                self.assertTrue(source.superseded);self.assertIsNone(ch.find(ch.cards[source_card],'controls'))
                self.assertEqual(child.state,'completed');self.assertEqual(len(b.calls),2)
                self.assertIn('.'+fmt,b.calls[-1].text);self.assertEqual(b.forks,0)
                self.assertTrue(all(isinstance(p,C.TextContent) for p in b.calls[-1].content))
                with self.assertRaises(ValueError):await self.click(c,source,'export',format=fmt)

    async def test_document_export_queued_task_can_be_stopped(self):
        c,ch,b=self.setup([[E.delta(DRAFT),E.completed()]])
        source=await self.start(c,'写一份请假报告');b.block=asyncio.Event()
        await self.click(c,source,'export',format='docx');child=c.turns[c.latest[source.session]]
        await self.ready(lambda:bool(child.card_message));self.assertEqual(child.state,'queued')
        await self.click(c,child,'stop')
        await self.finish(child)
        self.assertEqual(child.state,'stopped');self.assertEqual(len(b.calls),1)

    async def test_export_rejects_unknown_format_and_non_document(self):
        c,ch,b=self.setup([[E.delta(DRAFT),E.completed()]])
        t=await self.start(c,'写一份周报')
        with self.assertRaises(ValueError):await self.click(c,t,'export',format='exe')
        t.message.content=[C.TextContent(text='普通问答')];t.answer='一般说明，不是文稿。'
        with self.assertRaises(ValueError):await self.click(c,t,'export',format='docx')
        self.assertEqual(len(b.calls),1)

    async def test_file_receipt_collapses_full_explanation_without_losing_it(self):
        text='Word 文件已发送。\n\n'+DRAFT
        c,ch,b=self.setup([[E.tool_start('send_file_to_user',tool_call_id='send'),E.tool_end('send_file_to_user',tool_call_id='send'),E.delta(text),E.completed()]])
        t=await self.start(c,'给我Word');card=ch.cards[t.card]
        self.assertEqual(ch.find(card,'md1')['content'],'Word 文件已发送。')
        self.assertEqual(ch.find(card,'result_text')['content'],DRAFT.strip())
        self.assertFalse(ch.find(card,'result_details')['expanded'])
        self.assertEqual(card['config']['width_mode'],'compact')
        self.assertIsNone(ch.find(card,'controls'))

    async def test_duplicate_tool_rows_are_grouped_with_true_count(self):
        c,ch,b=self.setup([[E.tool_start('execute',tool_call_id='1'),E.tool_end('execute',tool_call_id='1'),E.tool_start('execute',tool_call_id='2'),E.tool_end('execute',tool_call_id='2'),E.delta('操作完成。'),E.completed()]])
        t=await self.start(c)
        self.assertIn('execute × 2',ch.find(ch.cards[t.card],'steps_md')['content'])
        self.assertIn('2 次工具调用',c.view(t)['status']);self.assertEqual(len(t.calls),2)
        self.assertEqual(ch.cards[t.card]['config']['width_mode'],'compact')

    async def test_completion_during_delayed_card_creation_keeps_one_consistent_reply(self):
        model_ready=asyncio.Event();answer_ready=asyncio.Event();card_started=asyncio.Event();card_release=asyncio.Event()
        async def slow(bridge,msg):
            model_ready.set();await answer_ready.wait();yield E.delta('最终短句');yield E.completed()
        c,ch,b=self.setup([slow]);original=ch._card_api
        async def api(method,path,payload):
            result=await original(method,path,payload)
            if method=='POST' and path=='/cardkit/v1/cards':
                card_started.set();await card_release.wait()
            return result
        ch._card_api=api;m,s=request();job=asyncio.create_task(c.process(m,s))
        await model_ready.wait();await card_started.wait()
        answer_ready.set();await asyncio.sleep(.01)
        self.assertFalse(job.done())
        card_release.set();await job
        t=c.turns[c.latest[b.session_key(m)]]
        self.assertTrue(t.card_message);self.assertEqual(len(ch.messages),1)
        self.assertEqual(ch.find(ch.cards[t.card],'md1')['content'],'最终短句')
        self.assertIsNone(ch.find(ch.cards[t.card],'working_panel'))
        self.assertFalse(t.render_dirty)

    async def test_short_formal_draft_keeps_usable_document_actions(self):
        c,ch,b=self.setup([[E.delta('请假期间将按既定安排完成工作交接。'*5),E.completed()]])
        t=await self.start(c,'请写一份简短请假通知')
        self.assertTrue(t.card);self.assertFalse(t.native_message)
        self.assertTrue(c.can_export(t));self.assertIsNotNone(ch.find(ch.cards[t.card],'controls'))
        self.assertEqual(ch.cards[t.card]['config']['width_mode'],'compact')

    async def test_export_payload_is_bound_to_source_not_latest_reply(self):
        c,ch,b=self.setup([[E.delta(DRAFT),E.completed()],[E.delta('文件已生成。'),E.completed()]])
        source=await self.start(c,'写一份请假报告')
        await self.click(c,source,'export',format='docx');await self.finish(source)
        prompt=b.calls[-1].text
        self.assertEqual(json.loads(prompt.split('文稿正文（JSON字符串）：\n',1)[1]),DRAFT.strip())
        self.assertNotIn('请将上一条回复中的文稿',prompt)

    async def test_new_message_during_source_refresh_cancels_export_without_wrong_source(self):
        c,ch,b=self.setup([[E.delta(DRAFT),E.completed()],[E.delta('另一个请求的回复B'),E.completed()]])
        source=await self.start(c,'写一份请假报告');original=c.refresh;interleaved=[]
        async def refresh(t,**kw):
            if t is source and source.superseded and not interleaved:
                interleaved.append(True)
                m,s=request('这是新的请求B');await c.process(m,s)
            return await original(t,**kw)
        c.refresh=refresh
        await self.click(c,source,'export',format='docx')
        if source.job:await asyncio.gather(source.job,return_exceptions=True)
        self.assertEqual([m.text for m in b.calls],['写一份请假报告','这是新的请求B'])
        exported=[t for t in c.turns.values() if t.unstarted_request]
        self.assertEqual(len(exported),1);self.assertTrue(exported[0].superseded)
        self.assertEqual(exported[0].state,'stopped')
        self.assertFalse(exported[0].card)

    async def test_queued_export_recovers_after_restart_without_losing_draft(self):
        c,ch,b=self.setup([[E.delta(DRAFT),E.completed()]])
        source=await self.start(c,'写一份请假报告');b.block=asyncio.Event()
        await self.click(c,source,'export',format='pdf');child=c.turns[c.latest[source.session]]
        await self.ready(lambda:bool(child.card_message))
        self.assertIsNone(child.snapshot);self.assertTrue(child.unstarted_request)
        await c.close()
        restored_bridge=fixture.FakeBridge([[E.delta('文件已生成。'),E.completed()]])
        c2=C.FeishuControls(ch,bridge=restored_bridge,state_path=c.path);c2.api_interval=0;self.controllers.append(c2)
        restored=c2.turns[child.id]
        self.assertEqual(restored.state,'interrupted');self.assertTrue(c2.can_retry(restored))
        await c2.refresh(restored,force=True)
        self.assertEqual(ch.find(ch.cards[restored.card],'controls')['columns'][0]['elements'][0]['text']['content'],'重试')
        await self.click(c2,restored,'retry');await self.finish(restored)
        self.assertEqual(restored.state,'completed');self.assertFalse(restored.unstarted_request)
        self.assertEqual(len(restored_bridge.calls),1);self.assertEqual(restored_bridge.forks,0)
        self.assertEqual(json.loads(restored_bridge.calls[0].text.split('文稿正文（JSON字符串）：\n',1)[1]),DRAFT.strip())

if __name__=='__main__':unittest.main()
