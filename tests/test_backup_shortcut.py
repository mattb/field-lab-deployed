import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
from deploy import backup_shortcut as shortcut

TASK='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'

class BackupShortcut(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.home=self.base/'App Home';self.desktop=self.base/'Desktop';self.desktop.mkdir()
        self.runner=Mock(return_value=subprocess.CompletedProcess([],0,TASK+': Current task\n'+OTHER+': Next task\n',''))
    def test_binding_changes_pointer_without_running_or_configuring_task(self):
        shortcut.bind(self.home,TASK,'start',self.runner)
        shortcut.bind(self.home,OTHER,'start',self.runner)
        path=self.home/'shared/runtime/backup-shortcut/current.plist'
        self.assertEqual(plistlib.loads(path.read_bytes())['task_uuid'],OTHER)
        self.assertEqual(path.stat().st_mode&0o777,0o600)
        self.assertTrue(all(call.args[0][-1]=='--uuids' for call in self.runner.call_args_list))
    def test_refresh_keeps_current_binding_unchanged(self):
        shortcut.bind(self.home,TASK,'start',self.runner)
        binding=self.home/'shared/runtime/backup-shortcut/current.plist';before=binding.read_bytes()
        def compile_fake(src,dest,*args):
            dest.mkdir();(dest/'fixture').write_text('compiled')
        shortcut.install(self.home,self.desktop,compile_fake)
        shortcut.install(self.home,self.desktop,compile_fake)
        self.assertEqual(binding.read_bytes(),before)
        self.assertEqual(len(self.runner.call_args_list),1)

    def test_missing_task_does_not_replace_current(self):
        shortcut.bind(self.home,TASK,'start',self.runner);path=self.home/'shared/runtime/backup-shortcut/current.plist';before=path.read_bytes()
        self.runner.return_value.stdout=''
        with self.assertRaises(ValueError):shortcut.bind(self.home,OTHER,'start',self.runner)
        self.assertEqual(path.read_bytes(),before)
    def test_install_unbound_and_retry_never_runs_app(self):
        def compile_fake(src,dest,*args):
            dest.mkdir();(dest/'fixture').write_text('compiled')
        compiler=Mock(side_effect=compile_fake)
        result=shortcut.install(self.home,self.desktop,compiler)
        self.assertFalse(result['backup_started']);self.assertFalse((self.home/'shared/runtime/backup-shortcut/current.plist').exists())
        self.assertTrue((self.desktop/'Backup.app').is_symlink())
        shortcut.install(self.home,self.desktop,compiler);self.assertEqual(compiler.call_count,1)
    def test_interrupted_publication_recovers_and_code_update_repoints(self):
        from unittest.mock import patch
        def compile_fake(src,dest,*args):
            dest.mkdir();(dest/'fixture').write_text('compiled')
        def fail(phase):raise OSError('interrupted')
        with self.assertRaises(OSError):shortcut.install(self.home,self.desktop,compile_fake,fail)
        self.assertFalse((self.desktop/'Backup.app').exists())
        shortcut.install(self.home,self.desktop,compile_fake)
        old=(self.desktop/'Backup.app').resolve()
        with patch.object(shortcut,'script',return_value='new program'):
            shortcut.install(self.home,self.desktop,compile_fake)
            new=(self.desktop/'Backup.app').resolve()
        self.assertNotEqual(old,new);self.assertTrue(old.exists())
    def test_compile_failure_never_publishes_and_retry_succeeds(self):
        with self.assertRaises(RuntimeError):shortcut.install(self.home,self.desktop,Mock(side_effect=RuntimeError('compile failed')))
        self.assertFalse((self.desktop/'Backup.app').exists())
        def compile_fake(src,dest,*args):
            dest.mkdir();(dest/'fixture').write_text('compiled')
        shortcut.install(self.home,self.desktop,compile_fake)
        self.assertTrue((self.desktop/'Backup.app').exists())
    @unittest.skipUnless(Path('/usr/bin/osacompile').exists(),'macOS app compiler')
    def test_concurrent_installers_agree_on_owned_shortcut(self):
        import sys
        args=[sys.executable,str(Path(shortcut.__file__)),'install','--home',str(self.home),'--desktop',str(self.desktop)]
        first=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        second=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        for process in [first,second]:
            stdout,stderr=process.communicate(timeout=30);self.assertEqual(process.returncode,0,stderr)
        record=json.loads((self.home/'shared/runtime/backup-shortcut/ownership.json').read_text())
        self.assertIn(str((self.desktop/'Backup.app').resolve()),record['desktops'][str(self.desktop/'Backup.app')])
    def test_corrupt_generation_is_refused(self):
        def compile_fake(src,dest,*args):
            dest.mkdir();(dest/'fixture').write_text('compiled')
        shortcut.install(self.home,self.desktop,compile_fake);app=(self.desktop/'Backup.app').resolve()
        (app/'fixture').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'integrity'):shortcut.install(self.home,self.desktop,compile_fake)
    def test_unrelated_desktop_item_is_never_replaced(self):
        (self.desktop/'Backup.app').mkdir()
        with self.assertRaises(ValueError):shortcut.install(self.home,self.desktop,Mock())
        self.assertTrue((self.desktop/'Backup.app').is_dir())
    def test_binding_symlink_refuses(self):
        root=shortcut.private_dir(self.home);target=self.base/'keep';target.write_text('keep');(root/'current.plist').symlink_to(target)
        with self.assertRaises(ValueError):shortcut.bind(self.home,TASK,'start',self.runner)
        self.assertEqual(target.read_text(),'keep')
    @unittest.skipUnless(Path('/usr/bin/osacompile').exists(),'macOS app compiler')
    def test_actual_native_app_compiles_and_contains_stable_binding_path(self):
        result=shortcut.install(self.home,self.desktop)
        app=Path(result['shortcut']).resolve()
        output=subprocess.run(['/usr/bin/osadecompile',str(app/'Contents/Resources/Scripts/main.scpt')],capture_output=True,text=True,check=True).stdout
        self.assertIn(str(self.home/'shared/runtime/backup-shortcut/current.plist'),output)
        self.assertIn('--start=',output);self.assertIn('--uuids',output)
        self.assertNotIn('System Events',output)
