"""Validated personal preferences, shared by the local page and channel runtime."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {'reply_mode':'quote','group_trigger':'mention','show_status':True,
            'process_expanded':False,'presentation_delay':4,'image_ocr':True,
            'audio_asr':True,'video_ocr':True}
ENUMS = {'reply_mode':{'quote','direct','topic'},'group_trigger':{'mention','topic','all'}}

def config_path():
    override=os.environ.get('OCTOP_FEISHU_SETTINGS')
    if override:return Path(override).expanduser()
    return Path.home()/'Library/Application Support/OctopFeishuPatch/preferences.json'

def validate(value):
    if not isinstance(value,dict):raise ValueError('配置必须为对象')
    if set(value)-set(DEFAULTS):raise ValueError('包含不支持的配置项')
    out=dict(DEFAULTS)
    for key,val in value.items():
        if key in ENUMS:
            if not isinstance(val,str) or val not in ENUMS[key]:raise ValueError('无效选项：'+key)
        elif key=='presentation_delay':
            if type(val) not in (int,float) or not 0<=val<=15:raise ValueError('等待时间应为 0–15 秒')
        elif type(val) is not bool:raise ValueError('开关值必须为布尔值：'+key)
        out[key]=val
    return out

def read_document(path=None):
    path=Path(path) if path else config_path()
    if not path.exists():return {'version':1,'channels':{}},'new'
    raw=path.read_bytes()
    doc=json.loads(raw)
    if not isinstance(doc,dict) or doc.get('version')!=1 or not isinstance(doc.get('channels'),dict):raise ValueError('配置文件格式不受支持')
    for key,value in doc['channels'].items():validate(value)
    return doc,hashlib.sha256(raw).hexdigest()

def load_preferences(channel_id='',path=None):
    try:
        doc,_=read_document(path)
        return validate(doc['channels'].get(str(channel_id),{}))
    except (ValueError,OSError,TypeError):return dict(DEFAULTS)

def save_preferences(channel_id,value,revision,path=None):
    path=Path(path) if path else config_path()
    # Exclusive OS lock protects revision validation + replace across page processes.
    import fcntl
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        os.chmod(lock.name,0o600);fcntl.flock(lock,fcntl.LOCK_EX)
        doc,current=read_document(path)
        if revision!=current:raise ValueError('设置已在另一个页面更新，请刷新后再保存')
        doc['channels'][str(channel_id)]=validate(value)
        fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.preferences-')
        try:
            with os.fdopen(fd,'w') as out:
                json.dump(doc,out,ensure_ascii=False,indent=2);out.write('\n');out.flush();os.fsync(out.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return read_document(path)

def message_preferences(message):
    meta=message.metadata or {}
    snapshot=meta.get('octop_employee_preferences')
    return validate(snapshot) if isinstance(snapshot,dict) else load_preferences(getattr(message,'channel_id',''))
