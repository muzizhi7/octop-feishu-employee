import asyncio
import unittest
import test_controls as fixture
from test_controls import request,pause,E,C
from octop_gateway.models import FileContent,ImageContent,AudioContent,VideoContent

class ScenarioTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.ControlsTests.asyncSetUp
    asyncTearDown=fixture.ControlsTests.asyncTearDown
    setup=fixture.ControlsTests.setup
    start=fixture.ControlsTests.start
    click=fixture.ControlsTests.click
    finish=fixture.ControlsTests.finish
    async def test_multi_select_waits_for_explicit_submit(self):
        from octop.infra.gateway.hitl.format import parse_ask_reply
        record=pause();record.action_requests[0]['args']['questions'][0]['multi_select']=True
        async def initial(bridge,msg):
            bridge.pending=record;yield E.text('问题');yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'a,b')
            parsed=parse_ask_reply(msg.text,record.action_requests[0]['args']['questions'],locale='zh')
            self.assertIn('继续',parsed);self.assertIn('取消',parsed)
            record.status='approved';yield E.text('多选已收到');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        await self.click(c,t,'toggle',answer='a');await self.click(c,t,'toggle',answer='b')
        self.assertEqual(len(b.calls),1);self.assertEqual(t.state,'waiting_answer')
        await self.click(c,t,'toggle',answer='a');self.assertEqual(t.selected,['b'])
        await self.click(c,t,'toggle',answer='a');await self.click(c,t,'answer');await self.finish(t)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'多选已收到')

    async def test_other_group_member_cannot_answer_by_typing(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        c,ch,b=self.setup([initial]);t=await self.start(c)
        msg,subject=request('a',owner='ou_other');msg.channel_subject=t.subject
        await c.process(msg,subject)
        self.assertEqual(len(b.calls),1);self.assertEqual(t.state,'waiting_answer')
    async def test_plain_answer_without_tools(self):
        c,ch,b=self.setup([[E.text('普通问题的回答'),E.completed()]])
        t=await self.start(c)
        self.assertEqual(t.answer,'普通问题的回答');self.assertEqual(t.state,'completed');self.assertEqual(t.calls,[])

    async def test_stream_final_message_not_duplicated(self):
        c,ch,b=self.setup([[E.delta('你'),E.delta('好'),E.text('你好'),E.completed()]])
        t=await self.start(c);self.assertEqual(t.answer,'你好')

    async def test_chat_text_answers_existing_question(self):
        async def initial(bridge,msg):
            bridge.pending=pause();yield E.text('问题');yield E.completed()
        async def resumed(bridge,msg):
            self.assertEqual(msg.text,'b');bridge.pending.status='approved';yield E.text('收到');yield E.completed()
        c,ch,b=self.setup([initial,resumed]);t=await self.start(c)
        msg,subject=request('b');await c.process(msg,subject)
        self.assertEqual(t.state,'completed');self.assertEqual(len(ch.messages),1)

    async def test_parallel_tools_match_ids_when_finishing_out_of_order(self):
        c,ch,b=self.setup([[E.tool_start('读取文件',tool_call_id='a'),E.tool_start('读取文件',tool_call_id='b'),E.tool_end('读取文件',tool_call_id='b'),E.tool_end('读取文件',tool_call_id='a'),E.completed()]])
        t=await self.start(c);self.assertEqual([v['state'] for v in t.calls],['done','done'])

    async def test_file_response_is_delivered(self):
        media=[]
        parts=[FileContent(local_path='test.md',filename='test.md'),ImageContent(local_path='image.png'),AudioContent(local_path='audio.mp3'),VideoContent(local_path='video.mp4')]
        c,ch,b=self.setup([[E(type=C.Event.MESSAGE,content=parts),E.completed()]])
        async def send(subject,part):media.append(part)
        ch.reply_media=send;t=await self.start(c)
        self.assertEqual(len(media),4);self.assertEqual(t.state,'completed')

    async def test_inbound_image_is_kept_for_model_and_failure_retry(self):
        c,ch,b=self.setup([[E.error_event('暂时失败')],[E.text('再次回答'),E.completed()]])
        msg,subject=request('看看图片');msg.content.append(ImageContent(local_path='test.png'))
        await c.process(msg,subject);t=next(iter(c.turns.values()))
        await self.click(c,t,'retry');await self.finish(t)
        self.assertEqual(b.calls[0].content,b.calls[1].content)
        self.assertIsInstance(b.calls[1].content[1],ImageContent)

    async def test_transient_patch_failure_recovers_without_losing_answer(self):
        c,ch,b=self.setup([[E.text('恢复后答案'),E.completed()]])
        original=ch._card_api;failures=[]
        async def api(method,path,payload):
            if method=='PATCH' and not failures:
                failures.append(True);return {'code':-1}
            return await original(method,path,payload)
        ch._card_api=api;t=await self.start(c)
        self.assertEqual(t.state,'completed');self.assertEqual(t.answer,'恢复后答案')

    async def test_card_create_failure_has_visible_fallback_and_no_model_call(self):
        c,ch,b=self.setup([])
        async def api(*args):return {'code':403}
        ch._card_api=api;t=await self.start(c)
        self.assertEqual(t.state,'failed');self.assertEqual(b.calls,[]);self.assertTrue(ch.messages)

    async def test_unexpected_end_has_retry(self):
        c,ch,b=self.setup([[E.delta('部分结果')]])
        t=await self.start(c);self.assertEqual(t.state,'failed');self.assertEqual(t.answer,'部分结果')
