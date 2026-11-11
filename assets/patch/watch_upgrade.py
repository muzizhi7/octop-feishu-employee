"""Refresh the version-specific startup hook and detect updated packages."""
import os
from pathlib import Path
import subprocess
from upgrade_guard import main,HERE

runtime=Path.home()/'.octop/portable/runtime/bin/python3'
if runtime.exists():
    try:
        result=subprocess.run([str(runtime),'-c','import site;print(site.getusersitepackages())'],env={**os.environ,'OCTOP_PATCH_CHILD':'1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,check=True)
        site=Path(result.stdout.decode().strip())
        if not site.is_absolute() or not site.is_relative_to(Path.home()):
            raise ValueError('Unexpected user site path')
        site.mkdir(parents=True,exist_ok=True)
        hook=site/'octop_feishu_patch.pth'
        content='import runpy, sys; runpy.run_path('+repr(str(HERE/'startup_hook.py'))+', init_globals={"OCTOP_ORIGINAL_ARGV0": sys.argv[0]})\n'
        if not hook.exists() or hook.read_text()!=content:hook.write_text(content)
    except Exception as exc:
        (HERE/'watch-error.log').write_text(str(exc))
main()
