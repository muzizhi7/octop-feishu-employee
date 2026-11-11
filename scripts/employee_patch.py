#!/usr/bin/env python3
"""Environment-first, locally backed-up installer for the bundled Octop patch."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

SKILL=Path(__file__).resolve().parents[1]
PAYLOAD=SKILL/'assets/patch'
LOCAL=('octop_gateway/channels/feishu_controls.py','octop_gateway/channels/feishu_pause.py','octop_gateway/channels/feishu_preferences.py')
LIVE={'queued','running','stopping','pausing','paused','waiting_answer','waiting_approval'}

def digest(data):return hashlib.sha256(data).hexdigest()
def read(path):return path.read_bytes() if path.is_file() else None
def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(value,ensure_ascii=False,indent=2)+'\n'
    atomic(path,data.encode())
def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    old_mode=path.stat().st_mode & 0o777 if path.exists() else 0o600
    temp=path.with_name(path.name+'.employee-tmp')
    try:
        temp.write_bytes(data);temp.chmod(old_mode);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)
    for cached in (path.parent/'__pycache__').glob(path.stem+'.*.pyc'):cached.unlink()
def guard_module():
    spec=importlib.util.spec_from_file_location('employee_patch_guard',PAYLOAD/'upgrade_guard.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
def desired():
    files=[*json.loads((PAYLOAD/'baseline.json').read_text()),*LOCAL]
    result={name:(PAYLOAD/'staged'/name).read_bytes() for name in files}
    if result[LOCAL[0]]!=(PAYLOAD/'staged/feishu_controls.py').read_bytes():
        raise ValueError('Source changed without a build. Run: employee_patch.py build')
    return result
def settings(args):
    home=Path(args.octop_home).expanduser().resolve() if args.octop_home else Path.home()/'.octop'
    root=Path(args.packages).expanduser().resolve() if args.packages else home/'portable/packages'
    runtime=Path(args.runtime).expanduser().resolve() if args.runtime else home/'portable/runtime/bin/python3'
    state=Path(args.state_dir).expanduser().resolve() if args.state_dir else Path.home()/'Library/Application Support/OctopFeishuPatch/managed'
    return home,root,runtime,state
def running(home):
    if platform.system()!='Darwin':return False
    result=subprocess.run(['pgrep','-x','Octop'],capture_output=True)
    if result.returncode==0:return True
    result=subprocess.run(['pgrep','-f',re.escape(str(home/'portable/launch.py'))],capture_output=True)
    return result.returncode==0
def plan(root,state):
    guard=guard_module();want=desired();result={};errors=[]
    record=json.loads((state/'installation.json').read_text()) if (state/'installation.json').exists() else {}
    for name,target in want.items():
        current=read(root/name)
        if current==target:result[name]=current;continue
        if current is None:
            if name in LOCAL:result[name]=target
            else:errors.append(name+': missing original runtime file')
            continue
        profiles=[PAYLOAD/'before'/name,PAYLOAD/'vendor'/name,PAYLOAD/'released'/name]
        if record.get('installed',{}).get(name)==digest(current):
            profiles.insert(0,state/'current'/name)
        # Exact known inputs are authoritative. For a fresh/upgraded upstream,
        # try upstream baselines before an old patched release: merging from a
        # patched baseline can otherwise treat existing patch features as unchanged
        # and omit them from a clean recipient's source.
        profiles.sort(key=lambda profile:read(profile)!=current)
        if name in LOCAL:
            if any(read(profile)==current for profile in profiles if profile.is_file()):
                result[name]=target
            else:errors.append(name+': unknown local patch module; adaptation required')
            continue
        reasons=[]
        for profile in profiles:
            if not profile.exists():continue
            try:
                result[name]=guard.merge(profile.read_text(),target.decode(),current.decode()).encode();break
            except Exception as exc:reasons.append(str(exc))
        else:errors.append(name+': incompatible changed source; adaptation required')
    return result,errors
def media_status():
    base=Path.home()/'Library/Application Support/OctopFeishuPatch/media'
    return {'ffmpeg':shutil.which('ffmpeg') or next((str(Path(p)/'ffmpeg') for p in ('/opt/homebrew/bin','/usr/local/bin') if (Path(p)/'ffmpeg').exists()),None),
            'asr_dependencies_present':(base/'deps/faster_whisper').is_dir(),
            'asr_model_present':any((base/'models').rglob('model.bin')) if (base/'models').exists() else False,
            'note':'Media recognition is optional; image/video OCR uses Octop local OCR. Presence is not a full media acceptance test.'}
def doctor(args):
    home,root,runtime,state=settings(args);issues=[];warnings=[]
    system=platform.system();info={'os':system,'architecture':platform.machine(),'controller_python':platform.python_version()}
    info.update(octop_home=str(home),packages=str(root),runtime=str(runtime))
    if system!='Darwin':issues.append('automatic_install_supported_on_macos_only')
    standard=home==Path.home()/'.octop' and root==home/'portable/packages' and runtime==home/'portable/runtime/bin/python3'
    if not standard:issues.append('nonstandard_layout_requires_adaptation')
    if not root.is_dir():issues.append('octop_packages_not_found')
    if not runtime.is_file():issues.append('octop_runtime_not_found')
    elif system=='Darwin':
        try:
            probe=subprocess.run([str(runtime),'-B','-c','import json,sys,platform;print(json.dumps({"version":list(sys.version_info[:3]),"architecture":platform.machine()}))'],capture_output=True,text=True,timeout=15,env={**os.environ,'OCTOP_PATCH_CHILD':'1'})
            info['runtime_probe']=json.loads(probe.stdout)
            if probe.returncode:issues.append('runtime_probe_failed')
            if info['runtime_probe']['version'][:2]!=[3,12]:issues.append('runtime_python_3_12_required_for_this_profile')
        except Exception:issues.append('runtime_probe_failed')
    changes={}
    if root.is_dir():
        try:
            expected=json.loads((PAYLOAD/'contracts.json').read_text());actual=guard_module().contracts(root)
            info['dependency_contracts_match']=actual==expected
            if actual!=expected:issues.append('dependency_contract_changed')
            candidates,errors=plan(root,state);issues.extend(errors)
            changes={n:digest(v) for n,v in candidates.items() if read(root/n)!=v}
        except Exception as exc:issues.append('compatibility_check_failed: '+str(exc))
    active=[]
    journal=home/'state/feishu-controls'
    for path in journal.glob('*.json'):
        try:
            rows=json.loads(path.read_text())
            if not isinstance(rows,list):
                warnings.append('invalid_task_journal_shape');continue
            active.extend({'state':r['state']} for r in rows if isinstance(r,dict) and isinstance(r.get('state'),str) and r.get('state') in LIVE)
            if any(not isinstance(r,dict) for r in rows):warnings.append('invalid_task_journal_row')
            if any(isinstance(r,dict) and not isinstance(r.get('state'),str) for r in rows):warnings.append('invalid_task_journal_state')
        except (ValueError,OSError):warnings.append('unreadable_task_journal')
    is_running=running(home);info.update(octop_running=is_running,active_task_count=len(active),planned_files=len(changes),media=media_status())
    if active:warnings.append('live_or_stale_task_records_present; do not interrupt active work')
    if not info['media']['ffmpeg']:warnings.append('ffmpeg_missing; core chat can still work')
    if not info['media']['asr_dependencies_present'] or not info['media']['asr_model_present']:warnings.append('optional_local_voice_recognition_not_ready')
    info.update(core_compatible=not issues,install_ready=not issues and not is_running,blockers=issues+(['quit_octop_before_install'] if is_running else []),warnings=warnings)
    return info
def verify(args,candidates=None):
    home,root,runtime,state=settings(args)
    report=doctor(args)
    if not report['core_compatible']:raise ValueError('Environment incompatible: '+', '.join(report['blockers']))
    candidates,errors=plan(root,state) if candidates is None else (candidates,[])
    if errors:raise ValueError('; '.join(errors))
    guard=guard_module()
    with tempfile.TemporaryDirectory(prefix='octop-employee-verify-') as temporary:
        base=Path(temporary);package=base/'packages'
        guard.overlay(root,package,[Path(n) for n in candidates])
        (base/'runtime').symlink_to(runtime.parent.parent,target_is_directory=True)
        for name,data in candidates.items():
            path=package/name
            if path.is_symlink():path.unlink()
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        tests=base/'tests';tests.mkdir()
        for path in PAYLOAD.glob('test_*.py'):shutil.copy2(path,tests/path.name)
        shutil.copytree(PAYLOAD/'staged',tests/'staged',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for name,data in candidates.items():(tests/'staged'/name).write_bytes(data)
        (tests/'staged/feishu_controls.py').write_bytes(candidates[LOCAL[0]])
        env={**os.environ,'OCTOP_PATCH_CHILD':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(package)+os.pathsep+str(root),'OCTOP_PATCH_TEST_ROOT':str(package)}
        result=subprocess.run([str(runtime),'-B','-m','unittest','discover','-s',str(tests),'-p','test_*.py','-q'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
        match=re.search(r'Ran (\d+) tests',result.stdout)
        if result.returncode:raise ValueError('Regression tests failed:\n'+result.stdout[-6500:])
        return {'passed':True,'tests':int(match[1]) if match else None,'installed_runtime_modified':False}
def install(args):
    home,root,runtime,state=settings(args);report=doctor(args)
    if not report['install_ready']:raise ValueError('Install blocked: '+', '.join(report['blockers']))
    candidates,errors=plan(root,state)
    if errors:raise ValueError('; '.join(errors))
    original={n:read(root/n) for n in candidates}
    changes={n:v for n,v in candidates.items() if original[n]!=v}
    if args.dry_run:return {'dry_run':True,'planned_files':list(changes),'environment':report}
    if not changes:return {'status':'already_installed','files_changed':0,'rollback_record_created':False}
    validation=verify(args,candidates)
    if running(home):raise ValueError('Octop restarted during validation; install cancelled.')
    if any(read(root/n)!=v for n,v in original.items()):raise ValueError('Runtime changed during validation; install cancelled.')
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S')+'-'+os.urandom(3).hex()
    backup=state/'backups'/stamp;before={n:digest(v) if v is not None else None for n,v in original.items()}
    installed={n:digest(v) for n,v in candidates.items()}
    metadata={'id':stamp,'root':str(root),'before':before,'installed':installed,'changed':list(changes),'guard_refresh_required':True}
    backup.mkdir(parents=True)
    old_record=read(state/'installation.json')
    old_sources={n:read(state/'current'/n) for n in candidates}
    if old_record is not None:atomic(backup/'previous-installation.json',old_record)
    for name,data in original.items():
        if data is not None:atomic(backup/'files'/name,data)
    write_json(backup/'manifest.json',metadata)
    done=[]
    try:
        for name,data in changes.items():atomic(root/name,data);done.append(name)
        for name,data in candidates.items():atomic(state/'current'/name,data)
        write_json(state/'installation.json',metadata)
    except BaseException:
        for name in reversed(done):
            if original[name] is None:(root/name).unlink(missing_ok=True)
            else:atomic(root/name,original[name])
        if old_record is not None:atomic(state/'installation.json',old_record)
        else:(state/'installation.json').unlink(missing_ok=True)
        for name,data in old_sources.items():
            if data is None:(state/'current'/name).unlink(missing_ok=True)
            else:atomic(state/'current'/name,data)
        raise
    return {'status':'installed','files_changed':len(changes),'backup':str(backup),'validation':validation,'next':'Restart Octop after installation; enable/refresh guard with guard-enable.'}
def rollback(args):
    home,root,runtime,state=settings(args)
    if platform.system()!='Darwin':raise ValueError('Automatic rollback currently supports macOS only.')
    if running(home):raise ValueError('Quit Octop before rollback.')
    record=json.loads((state/'installation.json').read_text());backup=state/'backups'/record['id']
    if record['root']!=str(root):raise ValueError('Rollback record belongs to a different installation.')
    for name,expected in record['installed'].items():
        if read(root/name) is None or digest(read(root/name))!=expected:raise ValueError('Runtime changed since install: '+name)
    restored={}
    for name,expected in record['before'].items():
        data=read(backup/'files'/name)
        if (digest(data) if data is not None else None)!=expected:raise ValueError('Backup integrity check failed: '+name)
        restored[name]=data
    if args.dry_run:return {'dry_run':True,'restore_to':'state immediately before this installation','files':record['changed']}
    # Prevent an enabled startup guard from immediately reapplying the patch.
    subprocess.run([sys.executable,str(PAYLOAD/'uninstall_autoguard.py')],check=True,stdout=subprocess.PIPE)
    current={n:read(root/n) for n in record['changed']};done=[]
    try:
        for name in record['changed']:
            if restored[name] is None:(root/name).unlink(missing_ok=True)
            else:atomic(root/name,restored[name])
            done.append(name)
    except BaseException:
        for name in reversed(done):atomic(root/name,current[name])
        raise
    previous=read(backup/'previous-installation.json')
    if previous:atomic(state/'installation.json',previous)
    else:(state/'installation.json').unlink()
    for name,data in restored.items():
        if data is None:(state/'current'/name).unlink(missing_ok=True)
        else:atomic(state/'current'/name,data)
    return {'status':'rolled_back','files_restored':len(done),'guard':'disabled','restart_required':True}
def build():
    subprocess.run([sys.executable,str(PAYLOAD/'build.py')],check=True,stdout=subprocess.PIPE)
    want=desired()
    version=json.loads((SKILL/'release.json').read_text()).get('version','1.0.0') if (SKILL/'release.json').exists() else '1.0.0'
    write_json(SKILL/'release.json',{'version':version,'profile':'octop-1.0.2-python3.12-macos','presentation':int(re.search(r'turn.presentation_version=(\d+)',(PAYLOAD/'staged/feishu_controls.py').read_text())[1]),'files':{n:digest(v) for n,v in want.items()}})
    return {'status':'built','files':len(want)}
def bundle(output):
    release=json.loads((SKILL/'release.json').read_text())
    if release['files']!={n:digest(v) for n,v in desired().items()}:raise ValueError('Payload changed; run build before packaging.')
    output=Path(output).expanduser().resolve()
    if output.is_relative_to(SKILL):raise ValueError('Write release archives outside the editable Skill folder.')
    allowed=[]
    for path in SKILL.rglob('*'):
        rel=path.relative_to(SKILL)
        if not path.is_file() or any(part in {'__pycache__','.git','deps','models','managed','backups','upgrade-backups'} for part in rel.parts):continue
        if path.name.startswith('.env') or path.suffix in {'.pem','.key'}:raise ValueError('Credential-like file in Skill: '+str(rel))
        if path.suffix=='.json' and path.name not in {'release.json','baseline.json','contracts.json'}:continue
        if path.suffix in {'.pyc','.log','.zip','.db','.sqlite'} or path.name in {'installation.json','guard-state.json','guard-config.json','guard.lock','status.md','startup.log'}:continue
        if rel.parts[0] not in {'SKILL.md','release.json','agents','scripts','references','assets','tests','docs',
            'README.md','README.en.md','LICENSE','THIRD_PARTY_NOTICES.md','CHANGELOG.md','CONTRIBUTING.md','SECURITY.md'}:continue
        data=path.read_bytes()
        if re.search(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/',data) or re.search(rb'cli_[a-f0-9]{16,}',data):raise ValueError('Local environment data in release file: '+str(rel))
        allowed.append((path,rel,data))
    manifest={str(rel):digest(data) for _,rel,data in allowed}
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for _,rel,data in sorted(allowed,key=lambda item:str(item[1])):z.writestr('octop-feishu-employee/'+str(rel),data)
        z.writestr('octop-feishu-employee/bundle-manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    return {'archive':str(output),'files':len(allowed)+1,'sha256':digest(output.read_bytes())}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['doctor','status','verify','install','rollback','guard-enable','guard-disable','media-check','build','package','configure'])
    parser.add_argument('--port',type=int,default=8766);parser.add_argument('--no-open',action='store_true')
    parser.add_argument('--octop-home');parser.add_argument('--packages');parser.add_argument('--runtime');parser.add_argument('--state-dir')
    parser.add_argument('--dry-run',action='store_true');parser.add_argument('--output')
    args=parser.parse_args()
    try:
        if args.command=='configure':
            from settings_server import serve
            serve(args);return 0
        if args.command in {'doctor','status'}:result=doctor(args)
        elif args.command=='verify':result=verify(args)
        elif args.command=='install':result=install(args)
        elif args.command=='rollback':result=rollback(args)
        elif args.command=='media-check':result=media_status()
        elif args.command=='build':result=build()
        elif args.command=='package':
            if not args.output:raise ValueError('package requires --output PATH')
            result=bundle(args.output)
        else:
            report=doctor(args)
            if args.command=='guard-enable' and (not report['core_compatible'] or report['planned_files']):raise ValueError('Install the compatible current payload before enabling its guard.')
            if platform.system()!='Darwin':raise ValueError('Automatic guard uses macOS launchd; other platforms need adaptation.')
            script='install_autoguard.py' if args.command=='guard-enable' else 'uninstall_autoguard.py'
            response=subprocess.run([sys.executable,str(PAYLOAD/script)],capture_output=True,text=True,timeout=45)
            if response.returncode:raise ValueError(response.stderr[-3000:])
            result={'status':args.command,'detail':response.stdout.strip()}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='doctor' and not result['core_compatible']:return 2
        return 0
    except Exception as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False,indent=2));return 2
if __name__=='__main__':raise SystemExit(main())
