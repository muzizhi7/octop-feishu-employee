import json,sys
from pathlib import Path
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base/'deps'))
from faster_whisper import WhisperModel
model=WhisperModel('tiny',device='cpu',compute_type='int8',download_root=str(base/'models'),local_files_only='--download' not in sys.argv)
if '--download' in sys.argv:
 print('Local ASR model ready')
else:
 import subprocess,shutil,numpy as np
 ffmpeg=shutil.which('ffmpeg') or '/opt/homebrew/bin/ffmpeg'
 audio=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-i',sys.argv[1],'-f','f32le','-ar','16000','-ac','1','pipe:1'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45).stdout
 segments,info=model.transcribe(np.frombuffer(audio,dtype=np.float32),beam_size=5,vad_filter=False,condition_on_previous_text=False)
 print(json.dumps({'text':''.join(segment.text for segment in segments),'language':info.language},ensure_ascii=False))
