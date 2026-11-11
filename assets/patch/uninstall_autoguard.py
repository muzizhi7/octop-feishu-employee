"""Disable only the automatic guard; keep backups and installed patches."""
from pathlib import Path
import os
import subprocess

base=Path.home()/'Library/Application Support/OctopFeishuPatch'
subprocess.run(['launchctl','bootout','gui/'+str(os.getuid())+'/cn.octop.feishu-patch-guard'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for parent in (Path.home()/'.local/lib').glob('python*/site-packages'):
    hook=parent/'octop_feishu_patch.pth'
    if hook.exists() and str(base/'startup_hook.py') in hook.read_text():hook.rename(hook.with_suffix('.disabled'))
plist=Path.home()/'Library/LaunchAgents/cn.octop.feishu-patch-guard.plist'
if plist.exists():plist.rename(plist.with_suffix('.disabled'))
print('Automatic guard disabled. Patch sources, backups and status files are retained.')
