import json
import shutil
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import upgrade_guard as guard

SOURCE=Path(__file__).resolve().parent
ACTUAL=Path.home()/'.octop/portable/packages'

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.assets=self.root/'assets'
        shutil.copytree(SOURCE,self.assets,ignore=shutil.ignore_patterns('__pycache__','backup','upgrade-backups','*.zip'))
        self.old_here,self.old_state=guard.HERE,guard.STATE
        guard.HERE=self.assets;guard.STATE=self.assets/'guard-state.json'
        (self.assets/'guard-config.json').unlink(missing_ok=True)
        self.package=self.root/'packages'
        self.names=list(json.loads((SOURCE/'baseline.json').read_text()))+['octop_gateway/channels/feishu_controls.py','octop_gateway/channels/feishu_pause.py','octop_gateway/channels/feishu_preferences.py']
        guard.overlay(ACTUAL,self.package,[Path(p) for p in self.names])
        (self.root/'runtime').symlink_to(ACTUAL.parent/'runtime',target_is_directory=True)
        for name in self.names:
            p=self.package/name
            if p.is_symlink():p.unlink()
            before=SOURCE/'before'/name
            if before.exists():p.write_bytes(before.read_bytes())
            elif name.endswith(("/feishu_controls.py","/feishu_pause.py","/feishu_preferences.py")):p.unlink(missing_ok=True)
        self.original=self.snapshot()
    def snapshot(self):return {n:(self.package/n).read_bytes() if (self.package/n).exists() else None for n in self.names}
    def tearDown(self):
        guard.HERE,guard.STATE=self.old_here,self.old_state;self.temp.cleanup()
    def test_restore_and_preserve_unrelated_upgrade_then_idempotent(self):
        name='octop/infra/gateway/gateway.py';p=self.package/name
        p.write_text(p.read_text()+'\ndef upstream_new_feature():\n    return 42\n')
        result=guard.check(self.package,startup=True)
        self.assertEqual(result['status'],'restored',result)
        self.assertIn('upstream_new_feature',p.read_text())
        installed=self.snapshot()
        self.assertEqual(guard.check(self.package,startup=True)['status'],'healthy')
        self.assertEqual(self.snapshot(),installed)
        self.assertEqual(guard.check(self.package,startup=False)['status'],'healthy')
        self.assertEqual(self.snapshot(),installed)
    def test_changed_target_rejected_without_any_writes(self):
        p=self.package/'octop/infra/gateway/gateway.py'
        p.write_text(p.read_text().replace('if registered is not None:','if registered is None:',1))
        before=self.snapshot();result=guard.check(self.package,startup=True)
        self.assertEqual(result['status'],'incompatible');self.assertEqual(before,self.snapshot())
    def test_regression_failure_keeps_update_unchanged(self):
        (self.assets/'test_forced_failure.py').write_text('import unittest\nclass Fail(unittest.TestCase):\n def test_failure(self): self.fail("simulated incompatible dependency")\n')
        result=guard.check(self.package,startup=True)
        self.assertEqual(result['status'],'incompatible');self.assertEqual(self.original,self.snapshot())
    def test_partial_install_failure_rolls_back(self):
        actual=guard.write_atomic;count=[]
        def fail(path,data):
            count.append(path)
            if len(count)==2:raise OSError('simulated disk error')
            return actual(path,data)
        with patch.object(guard,'write_atomic',side_effect=fail):result=guard.check(self.package,startup=True)
        self.assertEqual(result['status'],'incompatible');self.assertEqual(self.original,self.snapshot())
    def test_running_check_never_changes_files(self):
        self.assertEqual(guard.check(self.package)['status'],'pending_startup')
        self.assertEqual(self.original,self.snapshot())
    def test_dependency_contract_change_blocks_restore(self):
        name='octop_gateway/models.py';path=self.package/name
        original=path.read_text();path.unlink();path.write_text(original+'\nGUARD_INCOMPATIBLE_TEST=True\n')
        result=guard.check(self.package,startup=True)
        self.assertEqual(result['status'],'incompatible');self.assertEqual(self.original,self.snapshot())

if __name__=='__main__':unittest.main(verbosity=2)
