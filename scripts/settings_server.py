"""Loopback-only personal configuration page; no extra account or service."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import webbrowser
import employee_patch as manager

SPEC=importlib.util.spec_from_file_location('employee_preferences',manager.PAYLOAD/'staged/octop_gateway/channels/feishu_preferences.py')
PREF=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(PREF)
STATIC=manager.SKILL/'assets/settings'

class App:
    def __init__(self,args):
        self.args=args;self.home,self.packages,self.runtime,self.state=manager.settings(args)
        self.token=secrets.token_urlsafe(32);self.lock=threading.Lock();self.probes={};self.busy=False
    def channels(self):
        db=self.home/'octop.db'
        if not db.exists():return []
        with sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("select channel_id,agent_id,name,enabled,config_json from channels where kind='feishu'").fetchall()
        result=[]
        for row in rows:
            try:config=json.loads(row['config_json'] or '{}')
            except ValueError:config={}
            # App secret is never returned to the page or diagnostics.
            result.append({'id':row['channel_id'],'agent_id':row['agent_id'],'name':row['name'],'enabled':bool(row['enabled']),
                           'app_id':str(config.get('app_id','')),'configured':bool(config.get('app_id') and config.get('app_secret'))})
        return result
    def channel(self,id):
        for channel in self.channels():
            if channel['id']==id:return channel
        raise ValueError('未找到这个飞书连接，请刷新页面')
    def worker(self,channel,action):
        self.channel(channel)
        result=subprocess.run([str(self.runtime),'-B',str(Path(__file__).with_name('settings_worker.py')),str(self.home),channel,action],
            capture_output=True,text=True,timeout=35,env={**os.environ,'OCTOP_PATCH_CHILD':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(self.packages)})
        if result.returncode:raise ValueError('飞书检测未完成，请检查连接或稍后重试')
        try:return json.loads(result.stdout)
        except ValueError:raise ValueError('检测返回格式异常，请重试')
    def state_json(self):
        doc,revision=PREF.read_document()
        release=json.loads((manager.SKILL/'release.json').read_text())
        return {'channels':self.channels(),'values':doc['channels'],'defaults':PREF.DEFAULTS,'revision':revision,
                'media':manager.media_status(),'release':{'version':release['version'],'presentation':release['presentation']},
                'guard':(Path.home()/'Library/LaunchAgents/cn.octop.feishu-patch-guard.plist').exists(),
                'os':sys.platform,'probes':self.probes}
    def action(self,action,data):
        channel=data.get('channel','')
        if action=='save':
            self.channel(channel);value=PREF.validate(data.get('values'))
            if value['group_trigger']!='mention':
                probe=self.probes.get(channel,{})
                if time.time()-probe.get('checked_at',0)>300:
                    probe=self.worker(channel,'probe');self.probes[channel]=probe
                if not probe.get('group_messages'):raise ValueError('尚未确认可接收群消息，请先检查飞书权限并保留「仅 @ 时回复」')
            PREF.save_preferences(channel,value,data.get('revision'))
            return self.state_json()
        if action=='probe':
            result=self.worker(channel,'probe');self.probes[channel]=result;return result
        if action=='test':return self.worker(channel,'test')
        if action=='check':return manager.doctor(self.args)
        if action=='diagnostics':
            report=manager.doctor(self.args)
            return {'exported_at':time.strftime('%Y-%m-%d %H:%M:%S'),'environment':report,
                    'preferences':PREF.read_document()[0],'release':self.state_json()['release'],
                    'note':'不包含密钥、用户令牌和聊天正文。'}
        if action=='rollback':
            if manager.doctor(self.args)['active_task_count']:
                raise ValueError('仍有运行中或等待确认的任务，请先在 Octop 中结束任务')
            if data.get('confirm')!='rollback':raise ValueError('请先确认回滚')
            self.args.dry_run=True
            preview=manager.rollback(self.args)
            self.args.dry_run=False
            return manager.rollback(self.args)
        raise ValueError('未知操作')

def serve(args):
    app=App(args)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def allowed_host(self):return self.headers.get('Host')=='127.0.0.1:'+str(self.server.server_port)
        def json_response(self,code,value):
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(code)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def authenticated(self):return secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+app.token)
        def do_GET(self):
            if not self.allowed_host():return self.json_response(403,{'error':'仅允许从本机地址打开'})
            path=urlsplit(self.path).path
            if path=='/api/state':
                if not self.authenticated():return self.json_response(401,{'error':'请通过 Agent 提供的链接重新打开配置页'})
                try:return self.json_response(200,app.state_json())
                except (ValueError,OSError,sqlite3.Error):return self.json_response(500,{'error':'读取本地配置失败，请检查配置文件和 Octop 是否已安装'})
            name={'/':'index.html','/style.css':'style.css','/app.js':'app.js'}.get(path)
            if not name:return self.json_response(404,{'error':'页面不存在'})
            data=(STATIC/name).read_bytes();self.send_response(200)
            self.send_header('Content-Type',{'index.html':'text/html; charset=utf-8','style.css':'text/css; charset=utf-8','app.js':'application/javascript; charset=utf-8'}[name])
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def do_POST(self):
            origin='http://127.0.0.1:'+str(self.server.server_port)
            if not self.allowed_host() or self.headers.get('Origin')!=origin or not self.authenticated():return self.json_response(403,{'error':'请求来源无效，请从本机配置页操作'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.json_response(415,{'error':'请使用 JSON'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=32768:raise ValueError('请求大小无效')
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):raise ValueError('请求格式无效')
                if urlsplit(self.path).path!='/api/action':return self.json_response(404,{'error':'操作不存在'})
                if data.get('action') in {'save','rollback'}:
                    if not app.lock.acquire(blocking=False):return self.json_response(409,{'error':'上一项保存还在进行，请稍后再试'})
                    try:result=app.action(data.get('action'),data)
                    finally:app.lock.release()
                else:result=app.action(data.get('action'),data)
                return self.json_response(200,result)
            except (ValueError,subprocess.TimeoutExpired) as exc:
                return self.json_response(400,{'error':str(exc) if isinstance(exc,ValueError) else '操作超时，设置未更改，请重试'})
            except Exception:
                return self.json_response(500,{'error':'操作未完成，设置未更改，请稍后重试'})
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    url='http://127.0.0.1:'+str(server.server_port)+'/#'+app.token
    print(json.dumps({'url':url,'local_only':True},ensure_ascii=False),flush=True)
    if not args.no_open:webbrowser.open(url)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
