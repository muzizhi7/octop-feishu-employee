"""Minimal wiring of the native Feishu adapter to the tested lifecycle module."""
from pathlib import Path
import ast

HERE=Path(__file__).resolve().parent

def own_streams(source, functions):
    """Close delegated async generators before their ContextVar scope exits."""
    tree=ast.parse(source);lines=source.splitlines(keepends=True);edits=[]
    for fn in ast.walk(tree):
        if not isinstance(fn,ast.AsyncFunctionDef) or fn.name not in functions:
            continue
        loops=[n for n in ast.walk(fn) if isinstance(n,ast.AsyncFor)]
        if fn.name=='_project_chunks':
            loops=[n for n in loops if isinstance(n.iter,ast.Name) and n.iter.id=='chunks']
        for n in loops:
            indent=' '*n.col_offset
            expression=ast.get_source_segment(source,n.iter)
            target=ast.get_source_segment(source,n.target)
            key='_owned_events_'+str(n.lineno)
            body=''.join('    '+line if line.strip() else line for line in lines[n.body[0].lineno-1:n.end_lineno])
            replacement=indent+'async with _octop_owned_stream('+expression+') as '+key+':\n'+indent+'    async for '+target+' in '+key+':\n'+body
            edits.append((n.lineno-1,n.end_lineno,replacement))
    for start,end,replacement in sorted(edits,reverse=True):
        lines[start:end]=[replacement]
    result=''.join(lines)
    result=result.replace('from __future__ import annotations\n','from __future__ import annotations\n\nfrom contextlib import aclosing as _octop_owned_stream\n',1)
    return result

def methods(source, replacements):
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FeishuChannel')
    nodes={n.name:n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
    lines=source.splitlines(keepends=True)
    edits=[]
    for name,text in replacements.items():
        node=nodes[name]
        start=min([node.lineno]+[d.lineno for d in node.decorator_list])-1
        edits.append((start,node.end_lineno,text+'\n'))
    for start,end,text in sorted(edits,reverse=True):
        lines[start:end]=[text]
    return ''.join(lines)

def build():
    source=(HERE/'before/octop_gateway/channels/feishu.py').read_text()
    tree=ast.parse(source)
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='FeishuChannel')
    process=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_process_inbound')
    lines=source.splitlines(keepends=True)
    legacy=''.join(lines[process.lineno-1:process.end_lineno]).replace('async def _process_inbound(', 'async def _legacy_process_inbound(',1)
    wrapper='''    def configure_controls(self, processor: Any, manager: Any) -> None:
        from octop_gateway.channels.feishu_controls import FeishuControls
        self._controls = FeishuControls(self, processor, manager)

    async def _process_inbound(self, message: InboundMessage, subject: ChannelSubject) -> bool:
        controls = getattr(self, "_controls", None)
        if controls is not None:
            return await controls.process(message, subject)
        return await self._legacy_process_inbound(message, subject)

'''+legacy
    callback='''    def _on_card_action(self, data: Any) -> Any:
        controls = getattr(self, "_controls", None)
        if controls is not None:
            return controls.callback(data)
        from lark_oapi.event.callback.model.p2_card_action_trigger import CallBackToast, P2CardActionTriggerResponse
        response = P2CardActionTriggerResponse()
        response.toast = CallBackToast({"type": "warning", "content": "服务尚未就绪，请稍后重试。"})
        return response
'''
    source=methods(source,{'_process_inbound':wrapper,'_on_card_action':callback})
    source=source.replace('        self._running = False\n        await self._cancel_watchdog()','        self._running = False\n        controls = getattr(self, "_controls", None)\n        if controls is not None:\n            await controls.close()\n        await self._cancel_watchdog()',1)
    from media_patch import apply
    source=apply(source,methods)
    from topic_patch import apply as topic_apply
    source=topic_apply(source)
    target=HERE/'staged/octop_gateway/channels/feishu.py';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(source)
    gateway=(HERE/'before/octop/infra/gateway/gateway.py').read_text()
    old='''        if registered is not None:
            backend = media_backend_for_agent(self._agent_manager, row.agent_id)
'''
    new='''        if registered is not None:
            configure_controls = getattr(registered, "configure_controls", None)
            if row.kind == "feishu" and configure_controls is not None:
                configure_controls(self._processor, manager)
            backend = media_backend_for_agent(self._agent_manager, row.agent_id)
'''
    if gateway.count(old)!=1:
        raise RuntimeError('Gateway registration source changed')
    gateway=gateway.replace(old,new,1)
    target=HERE/'staged/octop/infra/gateway/gateway.py';target.parent.mkdir(parents=True,exist_ok=True);target.write_text(gateway)
    target=HERE/'staged/octop_gateway/channels/feishu_controls.py';target.write_text((HERE/'staged/feishu_controls.py').read_text())
    cleanup={
        'octop/infra/gateway/process/processor.py':{'__call__'},
        'octop/infra/gateway/process/stream_project.py':{'project_stream','project_resume_stream','_project_chunks'},
        'octop/infra/gateway/hitl/coordinator.py':{'iter_slash_resolution','iter_answer_resolution'},
        'octop/infra/agents/manager.py':{'stream','resume_hitl'},
    }
    for name,functions in cleanup.items():
        target=HERE/'staged'/name;target.parent.mkdir(parents=True,exist_ok=True)
        content=own_streams((HERE/'before'/name).read_text(),functions)
        if name=='octop/infra/agents/manager.py':
            marker='        agent_middleware: list[Any] = [\n'
            assert content.count(marker)==1
            content=content.replace(marker,'        from octop_gateway.channels.feishu_pause import middleware_factory as feishu_pause_middleware\n'+marker+'            feishu_pause_middleware(row.agent_id),\n',1)
        target.write_text(content)
    for target in (HERE/'staged').rglob('*.py'):
        compile(target.read_text(),str(target),'exec')
    print('Built and compiled controls, integration and owned stream cleanup')

if __name__=='__main__':
    build()
