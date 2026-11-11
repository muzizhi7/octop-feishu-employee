"""Exercise the SDK ingress before typing/media/queue, not just final render."""
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from test_media import F
from octop_gateway.channels.feishu_preferences import DEFAULTS

class TopicActivationTests(unittest.TestCase):
    def setUp(self):
        self.channel=F.FeishuChannel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'))
        self.channel._running=True;self.channel._bot_open_id='ou_bot'
        self.queued=[];self.typing=[];self.counter=0
        self.channel._enqueue_callback=self.queued.append
        self.channel._add_reaction_async=lambda *args:self.typing.append(args)
        self.channel._controls=NS(turns={'known':NS(chat='oc_private',owner='ou_owner',message=NS(metadata={'thread_id':'omt_known'}))})
    def event(self,thread='',owner='ou_owner',mention=False,chat='oc_private',kind='text',sender_type='user'):
        self.counter+=1
        message=NS(message_id='om_test_'+str(self.counter),message_type=kind,content=json.dumps({'text':'继续'}),chat_type='group',chat_id=chat,thread_id=thread,root_id='om_root',parent_id='om_parent',mentions=[{'key':'@_user_1','open_id':'ou_bot','name':'bot'}] if mention else [])
        return NS(event=NS(message=message,sender=NS(sender_type=sender_type,sender_id=NS(open_id=owner))),header=None)
    def receive(self,event,**prefs):
        with patch('octop_gateway.channels.feishu_preferences.load_preferences',return_value={**DEFAULTS,'group_trigger':'topic',**prefs}):
            self.channel._on_message_event(event)
    def test_known_owner_followup_without_mention_enters_queue(self):
        self.receive(self.event(thread='omt_known'))
        self.assertEqual(len(self.queued),1);self.assertEqual(len(self.typing),1)
        # Ingress freezes the decision's settings across a save before queue drain.
        with patch('octop_gateway.channels.feishu_preferences.load_preferences',return_value=DEFAULTS):
            parsed=self.channel.parse_inbound(self.queued[0])
        self.assertTrue(parsed.metadata['bot_mentioned']);self.assertEqual(parsed.metadata['thread_id'],'omt_known')
    def test_passive_group_never_typing_or_queues_even_for_media(self):
        self.receive(self.event(kind='image'))
        self.assertEqual(self.queued,[]);self.assertEqual(self.typing,[])
    def test_unknown_topic_wrong_owner_and_other_group_not_activated(self):
        for fields in ({'thread':'omt_unknown'},{'thread':'omt_known','owner':'ou_other'},{'thread':'omt_known','chat':'oc_other'}):
            with self.subTest(fields=fields):
                self.channel._seen_message_ids.clear() if hasattr(self.channel,'_seen_message_ids') else None
                self.receive(self.event(**fields))
                self.assertEqual(self.queued,[]);self.assertEqual(self.typing,[])
    def test_explicit_mention_in_group_still_activates(self):
        self.receive(self.event(mention=True));self.assertEqual(len(self.queued),1)
    def test_mention_only_still_requires_mention_in_known_topic(self):
        self.receive(self.event(thread='omt_known'),group_trigger='mention')
        self.assertEqual(self.queued,[]);self.assertEqual(self.typing,[])
    def test_bot_messages_never_activate_even_in_known_topic(self):
        self.receive(self.event(thread='omt_known',sender_type='bot'))
        self.assertEqual(self.queued,[]);self.assertEqual(self.typing,[])
