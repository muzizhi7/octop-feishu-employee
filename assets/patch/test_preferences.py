"""Real routing/render/media paths with saved personal preferences."""
import asyncio
from unittest.mock import patch
import test_adaptive as adaptive
from test_controls import C,E,request,pause
from octop_gateway.channels.feishu_preferences import DEFAULTS
from octop_gateway.models import ImageContent,AudioContent,VideoContent

class PreferenceRuntimeTests(adaptive.AdaptiveTests):
    def pref_message(self,**prefs):
        m,s=request('测试设置');m.metadata['octop_employee_preferences']={**DEFAULTS,**prefs};s.metadata=m.metadata.copy();return m,s
    async def test_mention_only_is_execution_gate_even_when_shared_policy_allows_message(self):
        c,ch,b=self.setup([[E.delta('不应执行'),E.completed()]])
        m,s=self.pref_message();m.metadata.update(chat_type='group',bot_mentioned=False)
        result=await c.process(m,s)
        self.assertFalse(result);self.assertFalse(c.turns);self.assertFalse(b.calls)

    async def test_direct_group_reply_uses_chat_send_and_keeps_original_metadata(self):
        c,ch,b=self.setup([[E.delta('收到。'),E.completed()]])
        m,s=self.pref_message(reply_mode='direct');m.metadata.update(chat_type='group',to_handle='oc_group');s.metadata=m.metadata.copy()
        await c.process(m,s)
        path,payload=ch.native_requests[0]
        self.assertIn('?receive_id_type=',path);self.assertNotIn('/reply',path)
        self.assertEqual(payload['receive_id'],'oc_group')
        self.assertEqual(next(iter(c.turns.values())).message.metadata['message_id'],'om_original')
    async def test_always_topic_route_and_existing_topic_override_direct(self):
        m,s=self.pref_message(reply_mode='topic');m.metadata['chat_type']='group'
        self.assertTrue(C.wants_new_topic(m))
        m.metadata.update(thread_id='omt_existing',octop_employee_preferences={**DEFAULTS,'reply_mode':'direct'})
        self.assertFalse(C.wants_new_topic(m));self.assertEqual(C.group_conversation_key(m),'omt_existing')
    async def test_hidden_completed_process_keeps_pending_actions(self):
        c,ch,b=self.setup([[E.delta('完成说明'),E.completed()]])
        m,s=self.pref_message(show_status=False,process_expanded=True);await c.process(m,s);t=next(iter(c.turns.values()))
        t.calls=[{'label':'读取文件','state':'done','key':'read_file'}]
        self.assertFalse(c.view(t)['show_process'])
        t.state='running';view=c.view(t);self.assertTrue(view['show_process'])
        self.assertTrue(c.elements(t,view)[0]['expanded'])
        self.assertTrue(view['has_controls'])
    async def test_all_media_recognition_toggles_skip_processing_but_preserve_originals(self):
        from test_media import F
        channel=F.FeishuChannel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'))
        parts=[ImageContent(data='AA=='),AudioContent(data='AA=='),VideoContent(data='AA==')]
        m,s=self.pref_message(image_ocr=False,audio_asr=False,video_ocr=False)
        m=m.model_copy(update={'content':parts})
        async def load(part):raise AssertionError('Disabled recognition must not load attachment')
        channel.load_media_bytes=load
        enriched=await channel._enrich_inbound_media(m)
        self.assertEqual(enriched.content[:3],parts)
        self.assertIn('自动识别已关闭',enriched.content[-1].text)
    async def test_direct_attachment_delivered_into_chat(self):
        from test_media import F
        channel=F.FeishuChannel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'));sent=[]
        m,s=self.pref_message();s.metadata.update(octop_reply_direct=True,to_handle='oc_group')
        async def send(**kw):sent.append(kw);return {'code':0}
        async def reply(*a,**kw):raise AssertionError('direct media must not quote')
        channel._send_message=send;channel._reply_message=reply
        await channel._deliver(s,msg_type='file',content='{}');self.assertEqual(sent[0]['receive_id'],'oc_group')

for name in dir(adaptive.AdaptiveTests):
    if name.startswith('test_') and name not in PreferenceRuntimeTests.__dict__:setattr(PreferenceRuntimeTests,name,None)
