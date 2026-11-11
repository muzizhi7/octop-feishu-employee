"""Install a user-owned, removable Octop startup compatibility guard."""
from pathlib import Path
import os
import json
import plistlib
import shutil
import subprocess

SOURCE=Path(__file__).resolve().parent
DEST=Path.home()/'Library/Application Support/OctopFeishuPatch'
LABEL='cn.octop.feishu-patch-guard'
PLIST=Path.home()/'Library/LaunchAgents'/(LABEL+'.plist')

def main():
    DEST.mkdir(parents=True,exist_ok=True,mode=0o700)
    files=['upgrade_guard.py','startup_hook.py','watch_upgrade.py','contracts.json','baseline.json','guard_tests.py','uninstall_autoguard.py','media_methods.py','media_patch.py','topic_patch.py',*[p.name for p in SOURCE.glob('test_*.py')]]
    for name in files:shutil.copy2(SOURCE/name,DEST/name)
    for name in ['before','vendor','staged','media']:
        shutil.copytree(SOURCE/name,DEST/name,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    report=DEST/'status.md'
    (DEST/'guard-config.json').write_text(json.dumps({'report':str(report.resolve())},ensure_ascii=False))
    subprocess.run(['/usr/bin/python3',str(DEST/'watch_upgrade.py')],check=True)
    PLIST.parent.mkdir(parents=True,exist_ok=True)
    data={'Label':LABEL,'ProgramArguments':['/usr/bin/python3',str(DEST/'watch_upgrade.py')],
          'RunAtLoad':True,'StartInterval':60,'ProcessType':'Background',
          'StandardOutPath':str(DEST/'watch.log'),'StandardErrorPath':str(DEST/'watch-error.log')}
    PLIST.write_bytes(plistlib.dumps(data))
    domain='gui/'+str(os.getuid())
    subprocess.run(['launchctl','bootout',domain+'/'+LABEL],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['launchctl','bootstrap',domain,str(PLIST)],check=True)
    print('Installed startup compatibility guard and 60-second package check: '+str(DEST))

if __name__=='__main__':main()
