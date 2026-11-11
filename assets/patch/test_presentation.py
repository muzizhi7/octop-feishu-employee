"""Contextual controls and compact cards, including old-card migration."""
import copy
import unittest
import test_controls as fixture
from test_controls import C,E,request,pause
from octop_gateway.models import FileContent

class PresentationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ControlsTests.asyncSetUp
    asyncTearDown=fixture.ControlsTests.asyncTearDown
    setup=fixture.ControlsTests.setup
    start=fixture.ControlsTests.start
    click=fixture.ControlsTests.click
    finish=fixture.ControlsTests.finish

    async def test_social_replies_have_only_body_and_no_hidden_replay(self):
        for message in ['你好','谢谢！','好的','hello','收到']:
            with self.subTest(message=message):
                c,ch,b=self.setup([[E.delta('我在。'),E.completed()]])
                msg,subject=request(message);await c.process(msg,subject);t=next(iter(c.turns.values()))
                self.assertEqual([e['element_id'] for e in ch.cards[t.card]['body']['elements']],['md1'])
                with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
                self.assertEqual(len(b.calls),1)

    async def test_content_answer_has_no_replay_and_accepts_followup(self):
        c,ch,b=self.setup([[E.delta('这是分析正文。'),E.completed()],[E.delta('这是另一版正文。'),E.completed()]])
        t=await self.start(c)
        self.assertIsNone(ch.find(ch.cards[t.card],'working_panel'))
        self.assertIsNone(ch.find(ch.cards[t.card],'question_md'))
        self.assertIsNone(ch.find(ch.cards[t.card],'controls'))
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
        m,s=request('请换个表达');await c.process(m,s)
        self.assertEqual(list(c.turns.values())[-1].answer,'这是另一版正文。')
        self.assertEqual(len(ch.messages),2)

    async def test_side_effect_and_unknown_tools_cannot_be_replayed(self):
        for key in ['send_file_to_user','write_file','execute','unknown_vendor_tool']:
            with self.subTest(tool=key):
                c,ch,b=self.setup([[E.tool_start(key,tool_call_id='x'),E.tool_end(key,tool_call_id='x'),E.delta('处理完成'),E.completed()]])
                t=await self.start(c)
                self.assertIsNone(ch.find(ch.cards[t.card],'controls'))
                with self.assertRaises(ValueError):await self.click(c,t,'regenerate')
                self.assertEqual(len(b.calls),1)

    async def test_media_without_tool_event_still_has_no_replay(self):
        c,ch,b=self.setup([[E(type=C.Event.MESSAGE,content=[FileContent(local_path='test.txt')]),E.delta('附件已发出'),E.completed()]])
        t=await self.start(c)
        self.assertTrue(t.media_sent);self.assertIsNone(ch.find(ch.cards[t.card],'controls'))
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')

    async def test_readonly_answer_has_single_process_disclosure(self):
        c,ch,b=self.setup([[E.tool_start('read_file',tool_call_id='x'),E.tool_end('read_file',tool_call_id='x'),E.delta('文档分析正文'),E.completed()]])
        t=await self.start(c);card=ch.cards[t.card]
        self.assertFalse(ch.find(card,'working_panel')['expanded'])
        self.assertIsNone(ch.find(card,'tools_panel'))
        self.assertIsNone(ch.find(card,'controls'))
        self.assertIn('1 次工具调用',ch.find(card,'working_panel')['header']['title']['content'])

    async def test_new_message_removes_old_actions_instead_of_disabling(self):
        c,ch,b=self.setup([[E.delta('一版正文'),E.completed()],[E.delta('在。'),E.completed()]])
        t=await self.start(c)
        m,s=request('你好');await c.process(m,s)
        self.assertIsNone(ch.find(ch.cards[t.card],'controls'))
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')

    async def test_legacy_empty_card_is_cleaned_on_recovery(self):
        c,ch,b=self.setup([[E.delta('在。'),E.completed()]])
        m,s=request('你好');await c.process(m,s);t=next(iter(c.turns.values()))
        ch.cards[t.card]['body']['elements']=[
            {'tag':'collapsible_panel','element_id':'working_panel','elements':[{'tag':'collapsible_panel','element_id':'tools_panel','elements':[{'tag':'markdown','element_id':'steps_md','content':'正在处理请求'}]}]},
            {'tag':'markdown','element_id':'md1','content':t.answer},
            {'tag':'markdown','element_id':'question_md','content':' '},
            c.columns([c.button(t,'重新生成','regenerate')],element_id='controls')]
        t.layout=None;t.presentation_version=0;t.cache.clear()
        await c._recover()
        self.assertEqual([e['element_id'] for e in ch.cards[t.card]['body']['elements']],['md1'])

    async def test_resume_separates_previous_body_and_new_delta(self):
        async def initial(bridge,msg):
            yield E.delta('先说明已知条件。');bridge.pending=pause();yield E.completed()
        async def resumed(bridge,msg):
            bridge.pending.status='approved';yield E.delta('这是补全后的建议。');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        await self.click(c,t,'answer',answer='a');await self.finish(t)
        self.assertEqual(t.answer,'先说明已知条件。\n\n这是补全后的建议。')
        self.assertIsNone(ch.find(ch.cards[t.card],'answer_form'))
        self.assertIsNone(ch.find(ch.cards[t.card],'question_md'))

    async def test_test_receipt_has_no_replay(self):
        c,ch,b=self.setup([[E.delta('交互验证完成'),E.completed()]])
        m,s=request('仅回复交互验证完成');await c.process(m,s);t=next(iter(c.turns.values()))
        self.assertIsNone(ch.find(ch.cards[t.card],'controls'))

    async def test_empty_completion_has_visible_receipt(self):
        c,ch,b=self.setup([[E.completed()]])
        t=await self.start(c)
        self.assertEqual(ch.find(ch.cards[t.card],'md1')['content'],'已完成。')
        self.assertIsNone(ch.find(ch.cards[t.card],'controls'))

    async def test_short_receipt_does_not_offer_regeneration(self):
        c,ch,b=self.setup([[E.delta('已收到。'),E.completed()]])
        t=await self.start(c)
        self.assertIsNone(ch.find(ch.cards[t.card],'controls'))
        with self.assertRaises(ValueError):await self.click(c,t,'regenerate')

    async def test_question_continues_after_display_delete_failure(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'a');bridge.pending.status='approved'
            yield E.delta('这是确认后的正文。');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        original=ch._card_api;failed=[]
        async def api(method,path,payload):
            if method=='DELETE' and not failed:
                failed.append(path);return {'code':400,'msg':'injected display failure'}
            return await original(method,path,payload)
        ch._card_api=api
        with self.assertLogs(C.LOG.name,level='WARNING'):
            await self.click(c,t,'answer',answer='a')
        await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(len(b.calls),2)
        self.assertFalse(t.render_dirty)
        self.assertIsNone(ch.find(ch.cards[t.card],'answer_form'))

    async def test_failed_display_is_marked_and_can_recover(self):
        c,ch,b=self.setup([[E.delta('一段正文'),E.completed()]])
        t=await self.start(c);t.superseded=True
        original=ch._card_api
        async def fail(method,path,payload):return {'code':400}
        ch._card_api=fail
        with self.assertLogs(C.LOG.name,level='WARNING'):await c.refresh(t,force=True)
        self.assertTrue(t.render_dirty);self.assertEqual(t.state,'completed')
        ch._card_api=original;await c.refresh(t,force=True)
        self.assertFalse(t.render_dirty);self.assertIsNone(ch.find(ch.cards[t.card],'controls'))

    async def test_recovery_migrates_all_retained_cards_not_only_last_twelve(self):
        c,ch,b=self.setup([[E.delta('在。'),E.completed()]]*13)
        for i in range(13):
            m,s=request('你好');await c.process(m,s)
        for t in c.turns.values():
            t.presentation_version=0;t.layout=None;t.cache.clear()
            ch.cards[t.card]['body']['elements']=[
                {'tag':'collapsible_panel','element_id':'working_panel','elements':[]},
                {'tag':'markdown','element_id':'md1','content':t.answer},
                {'tag':'markdown','element_id':'question_md','content':' '},
                c.columns([c.button(t,'重新生成','regenerate')],element_id='controls')]
        first=next(iter(c.turns.values()));first.state='interrupted';first.superseded=False
        await c._recover()
        self.assertIsNotNone(ch.find(ch.cards[first.card],'controls'))
        self.assertEqual(ch.find(ch.cards[first.card],'controls')['columns'][0]['elements'][0]['text']['content'],'重试')
        self.assertEqual(ch.find(ch.cards[first.card],'working_panel')['header']['title']['content'].split(' · ')[0],'执行已中断')
        for t in list(c.turns.values())[1:]:
            self.assertEqual(t.presentation_version,6)
            self.assertEqual([e['element_id'] for e in ch.cards[t.card]['body']['elements']],['md1'])

    async def test_committed_insert_with_lost_response_uses_full_put(self):
        c,ch,b=self.setup([[E.delta('第一版正文'),E.completed()],[E.delta('第二版正文'),E.completed()]])
        t=await self.start(c);t.transition('failed');original=ch._card_api;dropped=[]
        async def api(method,path,payload):
            result=await original(method,path,payload)
            if method=='POST' and path.endswith('/elements') and not dropped:
                dropped.append(True);return {'code':-1}
            return result
        ch._card_api=api
        with self.assertLogs(C.LOG.name,level='WARNING'):await self.click(c,t,'retry')
        await self.finish(t)
        self.assertEqual(t.answer,'第二版正文');self.assertEqual(len(b.calls),2)
        self.assertTrue(any(method=='PUT' and '/elements' not in path for method,path,_ in ch.api))
        self.assertFalse(t.layout_uncertain);self.assertFalse(t.render_dirty)
        self.assertIsNone(ch.find(ch.cards[t.card],'working_panel'))
        self.assertEqual(ch.find(ch.cards[t.card],'md1')['content'],'第二版正文')

    async def test_committed_delete_with_lost_response_uses_full_put(self):
        async def initial(bridge,msg):bridge.pending=pause();yield E.completed()
        async def resumed(bridge,msg):
            bridge.pending.status='approved';yield E.delta('回答之后的正文');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c);original=ch._card_api;dropped=[]
        async def api(method,path,payload):
            result=await original(method,path,payload)
            if method=='DELETE' and not dropped:dropped.append(True);return {'code':-1}
            return result
        ch._card_api=api
        with self.assertLogs(C.LOG.name,level='WARNING'):await self.click(c,t,'answer',answer='a')
        await self.finish(t)
        self.assertEqual(len(b.calls),2);self.assertEqual(t.state,'completed')
        self.assertFalse(t.render_dirty);self.assertFalse(t.layout_uncertain)
        self.assertIsNone(ch.find(ch.cards[t.card],'answer_form'))

    async def test_recovery_reconciles_uncertain_layout_even_without_dirty_flag(self):
        c,ch,b=self.setup([[E.delta('最终正文'),E.completed()]])
        t=await self.start(c)
        t.layout_uncertain=True;t.render_dirty=False;t.presentation_version=3
        ch.find(ch.cards[t.card],'md1')['content']='旧正文'
        await c._recover()
        self.assertFalse(t.layout_uncertain);self.assertFalse(t.render_dirty)
        self.assertEqual(ch.find(ch.cards[t.card],'md1')['content'],'最终正文')
        self.assertTrue(any(method=='PUT' for method,_,_ in ch.api))

    async def test_acceptance_report_is_content_not_a_test_receipt(self):
        c,ch,b=self.setup([[E.delta('项目验收报告正文'),E.completed()]])
        m,s=request('请写一份项目验收报告');await c.process(m,s)
        t=next(iter(c.turns.values()))
        self.assertFalse(c.can_regenerate(t));self.assertIsNone(ch.find(ch.cards[t.card],'controls'))

if __name__=='__main__':unittest.main()
