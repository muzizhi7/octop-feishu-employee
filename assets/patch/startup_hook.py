"""User-site entry point, restricted to Octop's own application launch."""
import os
from pathlib import Path
import subprocess
import sys

def run():
    if os.environ.get('OCTOP_PATCH_CHILD'):return
    portable=Path.home()/'.octop/portable'
    if Path(sys.executable).resolve()!=(portable/'runtime/bin/python3').resolve():return
    entry=globals().get('OCTOP_ORIGINAL_ARGV0',sys.argv[0] if sys.argv else '')
    if not entry or Path(entry).resolve()!=portable/'launch.py':return
    base=Path(__file__).resolve().parent
    env={**os.environ,'OCTOP_PATCH_CHILD':'1'}
    try:
        result=subprocess.run(['/usr/bin/python3',str(base/'upgrade_guard.py'),'--startup'],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=100)
        (base/'startup.log').write_bytes(result.stdout)
    except Exception as exc:
        (base/'startup.log').write_text('Startup check failed; Octop can start normally: '+str(exc))

run()
