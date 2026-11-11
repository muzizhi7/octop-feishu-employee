import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import settings_server as S
P=S.PREF

class PreferencesTests(unittest.TestCase):
    def test_atomic_roundtrip_and_channel_isolation(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'preferences.json'
            doc,rev=P.read_document(p)
            self.assertEqual(rev,'new')
            doc,rev=P.save_preferences('one',{'reply_mode':'direct','image_ocr':False},rev,p)
            self.assertEqual(P.load_preferences('one',p)['reply_mode'],'direct')
            self.assertFalse(P.load_preferences('one',p)['image_ocr'])
            self.assertEqual(P.load_preferences('two',p),P.DEFAULTS)
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
            with self.assertRaisesRegex(ValueError,'另一个页面'):P.save_preferences('one',{},'new',p)
            self.assertEqual(P.load_preferences('one',p)['reply_mode'],'direct')
    def test_invalid_values_never_overwrite_saved(self):
        for bad in [{'presentation_delay':True},{'presentation_delay':-1},{'presentation_delay':float('nan')},{'show_status':'false'},{'group_trigger':'unknown'},{'admin':True}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):P.validate(bad)
    def test_corrupt_config_fail_closed_and_page_reports_it(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'preferences.json';p.write_text('{broken')
            self.assertEqual(P.load_preferences('one',p),P.DEFAULTS)
            with self.assertRaises(ValueError):P.read_document(p)
    def test_message_snapshot_is_stable_after_save(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'OCTOP_FEISHU_SETTINGS':str(Path(t)/'p.json')}):
            m=SimpleNamespace(channel_id='one',metadata={'octop_employee_preferences':{**P.DEFAULTS,'reply_mode':'quote'}})
            P.save_preferences('one',{'reply_mode':'topic'},'new')
            self.assertEqual(P.message_preferences(m)['reply_mode'],'quote')
            self.assertEqual(P.message_preferences(SimpleNamespace(channel_id='one',metadata={}))['reply_mode'],'topic')
    def test_unconfirmed_group_permission_cannot_be_enabled(self):
        args=argparse.Namespace(octop_home=None,packages=None,runtime=None,state_dir=None)
        app=S.App(args)
        with patch.object(app,'channel',return_value={}),patch.object(app,'worker',return_value={'group_messages':False}):
            with self.assertRaisesRegex(ValueError,'群消息'):app.action('save',{'channel':'one','values':{'group_trigger':'all'},'revision':'new'})

if __name__=='__main__':unittest.main()

class ScopeDetectionTests(unittest.TestCase):
    def test_all_supported_group_event_scopes_and_user_scope_are_distinguished(self):
        import settings_worker as worker
        for scope in worker.GROUP_MESSAGE_SCOPES:self.assertTrue(worker.has_group_messages([scope]))
        self.assertFalse(worker.has_group_messages(['im:message.group_msg:get_as_user','im:message.group_at_msg:readonly']))
