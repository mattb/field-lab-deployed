"""Operator-buddy tests: lifecycle orchestration uses disposable state, no apps."""
import argparse
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('field_operator_test',Path(__file__).resolve().parents[1]/'deploy/field.py')
field=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(field)

class FieldOperatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'App with spaces';(self.home/'shared/runtime').mkdir(parents=True)
        self.old=self.home/'releases'/('a'*64);self.new=self.home/'releases'/('b'*64)
        self.old.mkdir(parents=True);self.new.mkdir(parents=True)
        self.state={'current':str(self.old),'previous':str(self.new)}
        self.args=argparse.Namespace(home=self.home,offshoot_idle=True,interrupt_ai=False)

    def test_desktop_workflow_installs_backup_without_binding_or_starting_ccc(self):
        import contextlib,io
        desktop=self.home/'Fake Desktop';desktop.mkdir()
        with patch.object(field,'current',return_value=self.old),patch.object(field,'save_launcher'),patch('desktop.install',return_value={'apps':['core']}) as core,patch('backup_shortcut.install',return_value={'backup_started':False}) as backup,patch.object(field,'run') as run,contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys,'argv',['field.py','desktop','--home',str(self.home),'--desktop',str(desktop)]):
                self.assertEqual(field.main(),0)
        core.assert_called_once_with(self.home.resolve(),desktop)
        backup.assert_called_once_with(self.home.resolve(),desktop)
        run.assert_not_called()

    def test_switch_needs_observed_offshoot_idle(self):
        self.args.offshoot_idle=False
        with patch.object(field,'run') as run,patch.object(field,'service') as service:
            with self.assertRaisesRegex(ValueError,'offshoot-idle'):field.switch(self.args,self.new.name)
        run.assert_not_called();service.assert_not_called()

    def test_failed_candidate_check_does_not_stop_working_service(self):
        with patch.object(field.manager,'status',return_value=self.state),patch.object(field,'run',side_effect=RuntimeError('damaged model')),patch.object(field,'service') as service,patch.object(field.manager,'activate') as activate:
            with self.assertRaisesRegex(RuntimeError,'damaged model'):field.switch(self.args,self.new.name)
        service.assert_not_called();activate.assert_not_called()

    def test_busy_ai_blocks_stop_and_switch(self):
        (self.home/'shared/runtime/status.json').write_text(json.dumps({'phase':'processing'}))
        with patch.object(field.manager,'status',return_value=self.state),patch.object(field,'run'),patch.object(field.manager,'mark_ready'),patch.object(field,'service') as service,patch.object(field.manager,'activate') as activate:
            with self.assertRaisesRegex(ValueError,'busy'):field.switch(self.args,self.new.name)
        service.assert_not_called();activate.assert_not_called()

    def test_activate_checks_then_stops_backs_up_and_switches_without_autostart(self):
        calls=[]
        with patch.object(field.manager,'status',return_value=self.state),patch.object(field,'run',side_effect=lambda *a,**k:calls.append('check')),patch.object(field.manager,'mark_ready',side_effect=lambda *a:calls.append('ready')),patch.object(field,'service',side_effect=lambda release,command:calls.append(command)),patch.object(field,'backup_state',side_effect=lambda *a:calls.append('backup') or 'backup-path'),patch.object(field.manager,'activate',side_effect=lambda *a,**k:calls.append('switch') or {'changed':True}),patch.object(field,'save_launcher',side_effect=lambda *a:calls.append('launcher')):
            result=field.switch(self.args,self.new.name)
        self.assertEqual(calls,['check','ready','stop','backup','switch','launcher'])
        self.assertIn('stopped',result['service'])

    def test_update_backup_includes_committed_wal_and_leaves_source_unchanged(self):
        project=Path(self.tmp.name)/'media project';notes=project/'_DIT_NOTES';notes.mkdir(parents=True)
        (self.home/'shared/config.json').write_text(json.dumps({'ingest_root':str(project)}))
        source=notes/'project.sqlite3';db=sqlite3.connect(source);self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE notes(value TEXT)');db.execute('INSERT INTO notes VALUES(?)',('operator edit in WAL',));db.commit()
        self.assertTrue(Path(str(source)+'-wal').exists())
        backup=field.backup_state(self.home,self.old)
        manifest=json.loads((backup/'manifest.json').read_text())
        self.assertEqual(len(manifest['databases']),1)
        with sqlite3.connect(manifest['databases'][0]['backup']) as copy:self.assertEqual(copy.execute('SELECT value FROM notes').fetchone()[0],'operator edit in WAL')
        self.assertEqual(db.execute('SELECT value FROM notes').fetchone()[0],'operator edit in WAL')

    def test_missing_media_blocks_backup_without_creating_fake_project(self):
        missing=Path(self.tmp.name)/'unmounted/ingest'
        (self.home/'shared/config.json').write_text(json.dumps({'ingest_root':str(missing)}))
        with self.assertRaisesRegex(ValueError,'unavailable'):field.backup_state(self.home,self.old)
        self.assertFalse(missing.exists())

    def test_stable_launcher_uses_current_and_quotes_home(self):
        field.save_launcher(self.home)
        text=(self.home/'run').read_text()
        self.assertIn('current/.venv/bin/python',text)
        self.assertIn('current/deploy/launcher.py',text)
        self.assertNotIn(str(self.old),text)
        self.assertTrue((self.home/'run').stat().st_mode&0o100)

    def test_launcher_rejects_symlink_without_altering_target(self):
        target=Path(self.tmp.name)/'other';target.write_text('unchanged')
        (self.home/'run').symlink_to(target)
        with self.assertRaisesRegex(ValueError,'symlink'):field.save_launcher(self.home)
        self.assertEqual(target.read_text(),'unchanged')

if __name__=='__main__':unittest.main()
