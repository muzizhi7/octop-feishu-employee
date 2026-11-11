import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from octop_gateway.models import AudioContent,VideoContent,FileContent,ChannelSubject

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('media_staged_feishu',HERE/'staged/octop_gateway/channels/feishu.py')
F=importlib.util.module_from_spec(spec);sys.modules[spec.name]=F;spec.loader.exec_module(F)

class MediaTests(unittest.IsolatedAsyncioTestCase):
    def channel(self):return F.FeishuChannel(lambda m:None,F.FeishuConfig(app_id='test',app_secret='test'))
    async def test_native_audio_does_not_use_file_message(self):
        channel=self.channel();sent=[]
        async def upload(part):return 'file_key'
        async def deliver(subject,**kwargs):sent.append(kwargs)
        channel._upload_file_to_feishu=upload;channel._deliver=deliver
        await channel._send_media(ChannelSubject(subject_id='ou_test'),AudioContent(data='AA=='))
        self.assertEqual(sent[0]['msg_type'],'audio')
    async def test_audio_file_is_promoted_to_native_voice(self):
        channel=self.channel();sent=[]
        async def upload(part):self.assertIsInstance(part,AudioContent);return 'key'
        async def deliver(subject,**kwargs):sent.append(kwargs)
        channel._upload_file_to_feishu=upload;channel._deliver=deliver
        await channel._send_media(ChannelSubject(subject_id='ou_test'),FileContent(filename='recording.mp3',data='AA=='))
        self.assertEqual(sent[0]['msg_type'],'audio')
    async def test_native_video_is_not_silently_dropped(self):
        channel=self.channel();sent=[]
        async def upload(part):return 'file_key'
        async def deliver(subject,**kwargs):sent.append(kwargs)
        async def load(part):return b'fixture','video/mp4'
        async def command(*args,**kwargs):raise RuntimeError('optional cover unavailable')
        channel._upload_file_to_feishu=upload;channel._deliver=deliver;channel.load_media_bytes=load;channel._run_media_command=command
        await channel._send_media(ChannelSubject(subject_id='ou_test'),VideoContent(data='AA=='))
        self.assertEqual(sent[0]['msg_type'],'media')
    async def test_incoming_native_audio_and_video_are_downloadable_parts(self):
        channel=self.channel()
        for kind,expected in [('media',VideoContent),('audio',AudioContent)]:
            part=channel._parse_content_parts(kind,{'file_key':'file_123','duration':3800},message_id='om_test')[0]
            self.assertIsInstance(part,expected);self.assertEqual(part.duration,3800)
            self.assertIn('om_test/resources/file_123',part.url)
    async def test_delivery_error_reaches_task_instead_of_false_success(self):
        channel=self.channel()
        async def send(**kwargs):return {'code':230055,'msg':'fixture failure'}
        channel._send_message=send
        with self.assertRaisesRegex(RuntimeError,'230055'):
            await channel._deliver(ChannelSubject(subject_id='ou_test'),msg_type='audio',content='{}')
    async def test_media_binary_static_binding_and_plain_file_metadata(self):
        channel=self.channel()
        with patch('shutil.which',return_value='/fixture/ffmpeg'):
            self.assertTrue(channel._media_binary('ffmpeg').endswith('ffmpeg'))
        async def load(part):return b'hello','text/plain'
        channel.load_media_bytes=load
        data,mime,name,kind,duration=await channel._prepare_feishu_upload(FileContent(filename='test.txt',data='AA=='))
        self.assertEqual((data,mime,name,kind,duration),(b'hello','text/plain','test.txt','stream',0))
