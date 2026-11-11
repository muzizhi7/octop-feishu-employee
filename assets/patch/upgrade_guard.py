"""Conservative, offline Octop patch migration. Runs before application imports."""
import ast
import contextlib
import datetime
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=Path.home()/'.octop/portable/packages'
STATE=HERE/'guard-state.json'

def digest(data):return hashlib.sha256(data).hexdigest()
def semantic(node):
    # Ignore empty syntax fields introduced by newer parser versions.
    def stable(value):
        if isinstance(value, ast.AST):
            return [type(value).__name__, {k:stable(v) for k,v in ast.iter_fields(value) if not (k=='type_params' and not v)}]
        if isinstance(value,list):return [stable(v) for v in value]
        if value is None or isinstance(value,(str,int,float,bool)):return value
        return {'literal':repr(value)}
    return hashlib.sha256(json.dumps(stable(node),sort_keys=True,ensure_ascii=True).encode()).hexdigest()
def key(node):
    if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):return ('named',node.name)
    if isinstance(node,(ast.Import,ast.ImportFrom)):return ('import',semantic(node))
    if isinstance(node,ast.AnnAssign):return ('assign',ast.dump(node.target))
    if isinstance(node,ast.Assign):return ('assign',','.join(ast.dump(t) for t in node.targets))
    return ('other',semantic(node))

def merge(old,new,current):
    """Replace unchanged touched symbols; preserve unrelated upstream changes."""
    o,n,c=map(ast.parse,(old,new,current));lines=current.splitlines(keepends=True);edits=[]
    def visit(ob,nb,cb,container=None):
        om={key(v):v for v in ob};nm={key(v):v for v in nb};cm={key(v):v for v in cb}
        for k,desired in nm.items():
            before=om.get(k);actual=cm.get(k)
            if before is not None and semantic(before)==semantic(desired):continue
            if actual is not None and semantic(actual)==semantic(desired):continue
            if isinstance(before,ast.ClassDef) and isinstance(desired,ast.ClassDef) and isinstance(actual,ast.ClassDef):
                for attr in ('bases','keywords','decorator_list'):
                    if ast.dump(ast.Module(body=getattr(before,attr),type_ignores=[]))!=ast.dump(ast.Module(body=getattr(actual,attr),type_ignores=[])):
                        raise ValueError('class contract changed: '+actual.name)
                visit(before.body,desired.body,actual.body,actual);continue
            if before is not None and actual is not None and semantic(before)!=semantic(actual):
                raise ValueError('modified patch target: '+str(k))
            if before is not None and actual is None:raise ValueError('missing patch target: '+str(k))
            start=min([desired.lineno]+[v.lineno for v in getattr(desired,'decorator_list',[])])-1
            replacement=''.join(new.splitlines(keepends=True)[start:desired.end_lineno])
            if actual is not None:
                first=min([actual.lineno]+[v.lineno for v in getattr(actual,'decorator_list',[])])-1
                edits.append((first,actual.end_lineno,replacement))
            else:
                position=container.end_lineno if container else len(lines)
                if isinstance(desired,(ast.Import,ast.ImportFrom)):
                    position=max([v.end_lineno for v in c.body if isinstance(v,(ast.Import,ast.ImportFrom))]+[0])
                edits.append((position,position,'\n'+replacement+'\n'))
        for k,before in om.items():
            if k not in nm and not isinstance(before,(ast.Import,ast.ImportFrom)):
                raise ValueError('unsupported deletion: '+str(k))
    visit(o.body,n.body,c.body)
    for start,end,replacement in sorted(edits,reverse=True):lines[start:end]=[replacement]
    result=''.join(lines);compile(result,'merged','exec');return result

def contracts(root):
    paths={
        'octop/infra/gateway/threads.py':{'get_or_create_by_key','get_thread','create_thread','update_composer','rebind'},
        'octop/infra/gateway/hitl/store.py':{'get_pending','resolve_for_session','mark_resolved'},
        'octop_gateway/manager.py':{'run_in_session'},
        'octop/infra/agents/threads/fork.py':{'write_checkpoint_messages'},
        'octop_gateway/models.py':set(),
        'langchain/agents/middleware/types.py':{'abefore_model','awrap_tool_call'},
        'langgraph/config.py':{'get_config'},
    }
    result={}
    for name,names in paths.items():
        tree=ast.parse((root/name).read_text())
        if not names:
            result[name]=semantic(tree);continue
        found={n.name:semantic(n.args) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names}
        if set(found)!=names:raise ValueError('missing dependency API: '+name)
        result[name]=found
    result['_python_runtime_libraries']=sorted(p.name for p in (root.parent/'runtime/lib').glob('python*') if p.is_dir())
    return result

def status(kind,detail,**extra):
    record={'status':kind,'detail':detail,'checked_at':datetime.datetime.now().astimezone().isoformat(),**extra}
    temp=STATE.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');os.replace(temp,STATE)
    report='# Octop 飞书补丁自动检查\n\n状态：'+kind+'\n\n'+detail+'\n\n检查时间：'+record['checked_at']+'\n'
    (HERE/'status.md').write_text(report)
    config=HERE/'guard-config.json'
    if config.exists():
        destination=json.loads(config.read_text()).get('report')
        if destination:
            Path(destination).parent.mkdir(parents=True,exist_ok=True)
            Path(destination).write_text(report)
    return record

def overlay(root,target,changes):
    """Symlink untouched modules; create private directories for candidates."""
    target.mkdir(parents=True,exist_ok=True)
    for item in root.iterdir():
        if item.name=='__pycache__':continue
        relevant=[p for p in changes if p.parts[0]==item.name]
        if item.is_dir() and relevant:
            overlay(item,target/item.name,[Path(*p.parts[1:]) for p in relevant])
        else:(target/item.name).symlink_to(item,target_is_directory=item.is_dir())

def write_atomic(path,data):
    mode=path.stat().st_mode & 0o777 if path.exists() else 0o644
    temporary=path.with_suffix('.patch-tmp');temporary.write_bytes(data);temporary.chmod(mode);os.replace(temporary,path)
    for cache in (path.parent/'__pycache__').glob(path.stem+'.*.pyc'):cache.unlink()

def check(root=ROOT,startup=False):
    desired={p: (HERE/'staged'/p).read_bytes() for p in json.loads((HERE/'baseline.json').read_text())}
    desired['octop_gateway/channels/feishu_controls.py']=(HERE/'staged/feishu_controls.py').read_bytes()
    desired['octop_gateway/channels/feishu_pause.py']=(HERE/'staged/octop_gateway/channels/feishu_pause.py').read_bytes()
    desired['octop_gateway/channels/feishu_preferences.py']=(HERE/'staged/octop_gateway/channels/feishu_preferences.py').read_bytes()
    current={p:(root/p).read_bytes() if (root/p).exists() else None for p in desired}
    try:
        if contracts(root)!=json.loads((HERE/'contracts.json').read_text()):
            return status('incompatible','依赖接口已变化，暂停自动恢复，请查看兼容报告。')
    except Exception as exc:
        return status('incompatible','依赖检查失败，保持现有文件：'+str(exc))
    if all(current[p]==v for p,v in desired.items()):return status('healthy','补丁已安装，无需改动。')
    # AST merges may retain harmless method ordering or unrelated upstream code.
    # Do not report an update every minute when all patch symbols are present.
    def already_present(name,target):
        actual=current[name]
        if actual==target:return True
        if actual is None or name.endswith(('/feishu_controls.py','/feishu_pause.py','/feishu_preferences.py')):return False
        original=HERE/'before'/name
        try:return original.exists() and merge(original.read_text(),target.decode(),actual.decode()).encode()==actual
        except (ValueError,TypeError,SyntaxError,UnicodeError):return False
    if all(already_present(name,target) for name,target in desired.items()):
        return status('healthy','补丁功能已存在，已保留新版的其他变更。')
    fingerprint=digest(b''.join((current[p] or b'') for p in sorted(current)))
    if not startup:
        return status('pending_startup','检测到包文件变化，将在下次启动 Octop 前检查兼容并恢复。',fingerprint=fingerprint)
    try:
        if contracts(root)!=json.loads((HERE/'contracts.json').read_text()):raise ValueError('dependency API contract changed')
        candidates={}
        for name,wanted in desired.items():
            if current[name]==wanted:continue
            if name.endswith(('/feishu_controls.py','/feishu_pause.py','/feishu_preferences.py')):
                if current[name] is not None:raise ValueError('unknown controls module')
                candidates[name]=wanted;continue
            profiles=[HERE/'before'/name,HERE/'vendor'/name]
            errors=[]
            for profile in profiles:
                if not profile.exists():continue
                try:
                    candidates[name]=merge(profile.read_text(),wanted.decode(),current[name].decode()).encode();break
                except Exception as exc:errors.append(str(exc))
            else:raise ValueError(name+': '+str(errors))
        candidates={p:v for p,v in candidates.items() if current[p]!=v}
        if not candidates:return status('healthy','补丁功能已存在，已保留新版的其他变更。')
        with tempfile.TemporaryDirectory(prefix='octop-patch-') as temporary:
            sandbox=Path(temporary);package=sandbox/'packages'
            overlay(root,package,[Path(p) for p in candidates])
            for name,data in candidates.items():
                path=package/name
                if path.is_symlink():path.unlink()
                path.write_bytes(data)
            tests=sandbox/'tests';tests.mkdir();(tests/'staged').mkdir()
            for source in HERE.glob('test_*.py'):shutil.copy2(source,tests/source.name)
            shutil.copy2(HERE/'staged/feishu_controls.py',tests/'staged/feishu_controls.py')
            for name in desired:
                if name.endswith('feishu_controls.py'):continue
                path=tests/'staged'/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(package/name,path)
            env={**os.environ,'PYTHONPATH':str(package)+os.pathsep+str(root),'OCTOP_PATCH_CHILD':'1','PYTHONDONTWRITEBYTECODE':'1'}
            env['OCTOP_PATCH_TEST_ROOT']=str(package)
            run=subprocess.run([str(root.parent/'runtime/bin/python3'),'-m','unittest','discover','-s',str(tests),'-p','test_*.py','-q'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
            (HERE/'last-test.log').write_bytes(run.stdout)
            if run.returncode:raise ValueError('candidate regression tests failed; see last-test.log')
        # Re-read every source: an updater may still be extracting packages.
        if any(((root/p).read_bytes() if (root/p).exists() else None)!=data for p,data in current.items()):raise ValueError('files changed during validation; retry on next launch')
        backup=HERE/'upgrade-backups'/fingerprint;backup.mkdir(parents=True,exist_ok=True)
        metadata={p:digest(v) if v is not None else None for p,v in current.items()}
        (backup/'manifest.json').write_text(json.dumps(metadata,indent=2))
        for name in candidates:
            if current[name] is not None:
                path=backup/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(current[name])
        changed=[]
        try:
            for name,data in candidates.items():write_atomic(root/name,data);changed.append(name)
        except BaseException:
            for name in reversed(changed):
                if current[name] is None:(root/name).unlink(missing_ok=True)
                else:write_atomic(root/name,current[name])
            raise
        return status('restored','兼容检查和回归测试通过，已自动恢复补丁。',backup=str(backup),files=list(candidates))
    except Exception as exc:
        return status('incompatible','保持更新后的原文件；未强行覆盖。'+str(exc),fingerprint=fingerprint)

def main():
    if '--contracts' in sys.argv:
        (HERE/'contracts.json').write_text(json.dumps(contracts(ROOT),indent=2));return
    if fcntl is None:
        raise SystemExit('Automatic guard currently supports macOS only.')
    with (HERE/'guard.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX if '--startup' in sys.argv else fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        print(json.dumps(check(startup='--startup' in sys.argv),ensure_ascii=False))

if __name__=='__main__':main()
