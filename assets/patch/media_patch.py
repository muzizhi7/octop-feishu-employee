import ast
from pathlib import Path

def apply(source,replace_methods):
    helpers=(Path(__file__).parent/'media_methods.py').read_text()
    cls=ast.parse(helpers).body[1] if isinstance(ast.parse(helpers).body[0],ast.Expr) else ast.parse(helpers).body[0]
    definitions={node.name:''.join(helpers.splitlines(keepends=True)[min([node.lineno]+[d.lineno for d in getattr(node,'decorator_list',[])])-1:node.end_lineno]) for node in cls.body}
    original=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='FeishuChannel')
    names={n.name for n in original.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    source=replace_methods(source,{k:v for k,v in definitions.items() if k in names})
    original=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='FeishuChannel')
    lines=source.splitlines(keepends=True)
    lines[original.end_lineno:original.end_lineno]=['\n'+v+'\n' for k,v in definitions.items() if k not in names]
    source=''.join(lines).replace('    AudioContent,\n','    AudioContent,\n    VideoContent,\n',1)
    source=source.replace('parts.append(AudioContent(url=download_url))',"parts.append(AudioContent(url=download_url,mime_type='audio/ogg',duration=content_data.get('duration')))\n\n        elif message_type == 'media':\n            file_key=content_data.get('file_key','')\n            if file_key and message_id:\n                download_url=self._build_resource_url(message_id,file_key,resource_type='file')\n                parts.append(VideoContent(url=download_url,mime_type='video/mp4',duration=content_data.get('duration')))",1)
    source=source.replace('parts.append(FileContent(url=download_url, filename=filename))',"if filename.lower().endswith(('.mp4','.mov','.webm','.mkv')):\n                    parts.append(VideoContent(url=download_url,mime_type='video/mp4'))\n                elif filename.lower().endswith(('.ogg','.opus','.mp3','.wav','.m4a','.aac','.flac')):\n                    parts.append(AudioContent(url=download_url))\n                else:\n                    parts.append(FileContent(url=download_url, filename=filename))",1)
    source=source.replace('        await self._send_message(\n            receive_id=receive_id,','        data = await self._send_message(\n            receive_id=receive_id,',1)
    marker='''            content=content,
        )

    async def _reply_message'''
    source=source.replace(marker,'''            content=content,
        )
        if data.get('code') != 0:
            raise RuntimeError('飞书媒体发送失败：'+str(data.get('code'))+' '+str(data.get('msg')))

    async def _reply_message''',1)
    compile(source,'feishu-media','exec')
    return source
