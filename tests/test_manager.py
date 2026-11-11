"""Installer safety tests use temporary runtimes and never change live Octop."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('employee_manager',ROOT/'scripts/employee_patch.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();base=Path(self.temp.name).resolve()
        self.home=base/'octop';self.root=self.home/'portable/packages';self.root.mkdir(parents=True)
        self.state=base/'state'
        self.args=argparse.Namespace(octop_home=str(self.home),packages=str(self.root),runtime=str(self.home/'portable/runtime/bin/python3'),state_dir=str(self.state),dry_run=False)
        self.want={'unit/a.py':b'value = 2\n','unit/added.py':b'new = True\n'}
        M.atomic(self.root/'unit/a.py',b'value = 1\n')
    def tearDown(self):self.temp.cleanup()
    def install(self,fail=None):
        with patch.object(M,'doctor',return_value={'install_ready':True}),patch.object(M,'plan',return_value=(self.want,[])),patch.object(M,'verify',return_value={'passed':True}),patch.object(M,'running',return_value=False):
            if fail:
                with patch.object(M,'atomic',side_effect=fail):return M.install(self.args)
            return M.install(self.args)
    def rollback(self):
        with patch.object(M.platform,'system',return_value='Darwin'),patch.object(M,'running',return_value=False),patch.object(M.subprocess,'run'):
            return M.rollback(self.args)
    def test_install_records_actual_originals_and_rollback_restores_them(self):
        result=self.install();self.assertEqual(result['files_changed'],2)
        record=json.loads((self.state/'installation.json').read_text())
        self.assertIsNone(record['before']['unit/added.py'])
        self.assertEqual((self.root/'unit/a.py').read_bytes(),self.want['unit/a.py'])
        self.rollback()
        self.assertEqual((self.root/'unit/a.py').read_bytes(),b'value = 1\n')
        self.assertFalse((self.root/'unit/added.py').exists())
    def test_dry_run_changes_neither_runtime_nor_install_state(self):
        self.args.dry_run=True;result=self.install()
        self.assertTrue(result['dry_run']);self.assertFalse(self.state.exists())
        self.assertEqual((self.root/'unit/a.py').read_bytes(),b'value = 1\n')
    def test_partial_write_failure_rolls_back_runtime_and_state(self):
        actual=M.atomic
        def fail(path,data):
            if path==self.root/'unit/added.py':raise OSError('simulated disk failure')
            return actual(path,data)
        with self.assertRaises(OSError):self.install(fail)
        self.assertEqual((self.root/'unit/a.py').read_bytes(),b'value = 1\n')
        self.assertFalse((self.state/'installation.json').exists())
    def test_source_changed_after_validation_is_never_overwritten(self):
        def verify(*args):
            (self.root/'unit/a.py').write_text('updater = True\n');return {'passed':True}
        with patch.object(M,'doctor',return_value={'install_ready':True}),patch.object(M,'plan',return_value=(self.want,[])),patch.object(M,'verify',side_effect=verify),patch.object(M,'running',return_value=False):
            with self.assertRaisesRegex(ValueError,'changed during validation'):M.install(self.args)
        self.assertEqual((self.root/'unit/a.py').read_text(),'updater = True\n')
    def test_running_app_blocks_install_without_backup_writes(self):
        with patch.object(M,'doctor',return_value={'install_ready':False,'blockers':['quit_octop_before_install']}):
            with self.assertRaises(ValueError):M.install(self.args)
        self.assertFalse(self.state.exists())
    def test_changed_runtime_or_corrupt_backup_blocks_rollback(self):
        self.install();(self.root/'unit/a.py').write_text('upstream=3\n')
        with self.assertRaises(ValueError):self.rollback()
        self.assertEqual((self.root/'unit/a.py').read_text(),'upstream=3\n')
    def test_corrupt_backup_blocks_rollback_before_any_restore(self):
        self.install();record=json.loads((self.state/'installation.json').read_text())
        (self.state/'backups'/record['id']/'files/unit/a.py').write_text('corrupt')
        with self.assertRaises(ValueError):self.rollback()
        self.assertEqual((self.root/'unit/a.py').read_bytes(),self.want['unit/a.py'])
    def test_unsupported_platform_is_detected_without_runtime_execution(self):
        for system in ('Windows','Linux'):
            with self.subTest(system=system),patch.object(M.platform,'system',return_value=system),patch.object(M.subprocess,'run') as run:
                result=M.doctor(self.args)
                self.assertFalse(result['core_compatible']);self.assertIn('automatic_install_supported_on_macos_only',result['blockers'])
                run.assert_not_called()
    def test_future_edit_can_merge_on_released_patch_without_editing_baseline(self):
        root=self.root;name='octop_gateway/channels/feishu_controls.py'
        before=(M.PAYLOAD/'released'/name).read_bytes();M.atomic(root/name,before)
        changed=before.replace(b'presentation_delay = 4.0',b'presentation_delay = 5.0')
        self.assertNotEqual(before,changed)
        with patch.object(M,'desired',return_value={name:changed}):
            candidate,errors=M.plan(root,self.state)
        self.assertFalse(errors);self.assertEqual(candidate[name],changed)
    def test_clean_upstream_installs_every_existing_patch_feature(self):
        # Old release and desired may share lifecycle fixes. A fresh source
        # must use its upstream baseline rather than skipping those fixes.
        for name,target in M.desired().items():
            original=M.PAYLOAD/'before'/name
            if original.exists():M.atomic(self.root/name,original.read_bytes())
        candidates,errors=M.plan(self.root,self.state)
        self.assertFalse(errors)
        for name,target in M.desired().items():
            before=M.PAYLOAD/'before'/name
            if before.exists():
                self.assertEqual(M.guard_module().merge(before.read_text(),target.decode(),candidates[name].decode()),candidates[name].decode(),name)
            else:self.assertEqual(candidates[name],target,name)

    def test_unrelated_fresh_upstream_update_keeps_all_patch_features(self):
        name='octop/infra/agents/manager.py'
        before=(M.PAYLOAD/'before'/name).read_text()
        M.atomic(self.root/name,(before+'\ndef recipient_upstream_feature():\n    return 42\n').encode())
        with patch.object(M,'desired',return_value={name:M.desired()[name]}):
            candidates,errors=M.plan(self.root,self.state)
        self.assertFalse(errors)
        self.assertIn(b'def recipient_upstream_feature',candidates[name])
        self.assertIn(b'async with _octop_owned_stream',candidates[name])

    def test_unknown_local_source_change_is_rejected(self):
        name='octop_gateway/channels/feishu_controls.py';M.atomic(self.root/name,b'class Unknown: pass\n')
        with patch.object(M,'desired',return_value={name:(M.PAYLOAD/'released'/name).read_bytes()}):
            candidate,errors=M.plan(self.root,self.state)
        self.assertTrue(errors)

    def test_malformed_journal_keeps_structured_environment_report(self):
        folder=self.home/'state/feishu-controls';folder.mkdir(parents=True)
        (folder/'object.json').write_text('{"state":"paused"}')
        (folder/'rows.json').write_text('[null,"junk",{"state":[]},{"state":{}},{"state":"paused","content":"DO_NOT_OUTPUT"}]')
        with patch.object(M.platform,'system',return_value='Linux'):
            result=M.doctor(self.args)
        self.assertIn('core_compatible',result);self.assertEqual(result['active_task_count'],1)
        self.assertIn('invalid_task_journal_shape',result['warnings']);self.assertIn('invalid_task_journal_state',result['warnings'])
        self.assertNotIn('DO_NOT_OUTPUT',json.dumps(result))

    def test_release_packages_itself_and_manifest_matches_every_file(self):
        output=Path(self.temp.name)/'skill.zip';result=M.bundle(output)
        self.assertTrue(output.is_file());self.assertGreater(result['files'],20)
        with zipfile.ZipFile(output) as z:
            manifest=json.loads(z.read('octop-feishu-employee/bundle-manifest.json'))
            for name,sha in manifest.items():
                self.assertEqual(M.digest(z.read('octop-feishu-employee/'+name)),sha)
            self.assertIn('scripts/employee_patch.py',manifest)
            self.assertNotIn('installation.json',manifest)

if __name__=='__main__':unittest.main()
