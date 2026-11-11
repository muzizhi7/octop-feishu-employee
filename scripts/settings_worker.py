"""Short-lived Feishu probe. Only whitelisted non-secret results leave this worker."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import urllib.error


GROUP_MESSAGE_SCOPES={'im:message.group_msg','im:message.group_msg:readonly','im:message.group_msg.include_bot:read'}

def has_group_messages(scopes):
    return bool(GROUP_MESSAGE_SCOPES.intersection(scopes))

def main():
    home=Path(sys.argv[1]);channel=sys.argv[2];action=sys.argv[3]
    with sqlite3.connect('file:'+str(home/'octop.db')+'?mode=ro',uri=True) as db:
        row=db.execute("select config_json from channels where channel_id=? and kind='feishu'",(channel,)).fetchone()
    if not row:raise ValueError('连接不存在')
    config=json.loads(row[0]);base='https://open.larksuite.com/open-apis' if config.get('domain')=='lark' else 'https://open.feishu.cn/open-apis'
    def request(method,path,data=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=12) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            try:return json.load(exc)
            except ValueError:return {'code':exc.code}
    token=request('POST','/auth/v3/tenant_access_token/internal',{'app_id':config.get('app_id'),'app_secret':config.get('app_secret')})
    if token.get('code')!=0:return {'connected':False,'error':'飞书凭证校验失败（'+str(token.get('code'))+'）','checked_at':time.time()}
    token=token['tenant_access_token']
    def own_dm():
        path=home/'state/feishu-controls'/(hashlib.sha256(channel.encode()).hexdigest()[:20]+'.json')
        rows=json.loads(path.read_text()) if path.exists() else []
        for row in reversed(rows):
            meta=(row.get('message') or {}).get('metadata') or {}
            if meta.get('chat_type') in ('p2p','direct') and str(meta.get('chat_id','')).startswith('oc_'):return meta['chat_id']
        raise ValueError('请先与机器人私聊一条消息，再发送测试或授权请求')
    if action=='probe':
        bot=request('GET','/bot/v3/info',token=token)
        scopes=request('GET','/application/v6/scopes',token=token)
        granted=[v['scope_name'] for v in scopes.get('data',{}).get('scopes',[]) if v.get('grant_status')==1]
        capabilities={key:any(any(scope.startswith(prefix) for prefix in prefixes) for scope in granted) for key,prefixes in
                      {'docs':('docx:document','docs:document','drive:drive'),'wiki':('wiki:',),'calendar':('calendar:',),'task':('task:',),'bitable':('bitable:',)}.items()}
        result={'connected':bot.get('code')==0,'bot_name':bot.get('bot',{}).get('app_name',''),'scope_check':scopes.get('code')==0,
                'group_messages':has_group_messages(granted),'capabilities':capabilities,'checked_at':time.time()}
        if scopes.get('code')!=0:result['scope_error']='权限检测未完成（'+str(scopes.get('code'))+'）'
        return result
    if action=='test':
        text='连接测试成功：这是从 Octop 飞书数字员工配置页发送的测试消息。'
        sent=request('POST','/im/v1/messages?receive_id_type=chat_id',{'receive_id':own_dm(),'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False)},token)
        # Bot-originated messages do not activate the agent. Authorization is an
        # explicit copyable instruction, never falsely reported as completed.
        if sent.get('code')!=0:raise ValueError('消息未发送（'+str(sent.get('code'))+'）')
        return {'sent':True,'message_id':sent.get('data',{}).get('message_id',''),'instruction':''}
    raise ValueError('未知操作')

if __name__=='__main__':
    try:print(json.dumps(main(),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'error':str(exc) if isinstance(exc,ValueError) else '飞书请求未完成，请检查网络后重试'},ensure_ascii=False))
