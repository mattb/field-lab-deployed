import contextlib
import io
from pathlib import Path
from unittest import mock
import unittest
import tempfile

from deploy.backup import main


class FakeManager:
    def __init__(self, state):
        self.calls = []
        self.root = state
    def status(self):
        self.calls.append('status')
        return {'message': 'Connect backup drive B002'}
    def add_set(self, *args, **kwargs):
        self.calls.append(('add', args, kwargs))
        return {'registered': True}
    def discover_queue(self, queue):
        self.calls.append(('discover', queue))
    def run_pending(self):
        self.calls.append('run')
        return {'message': 'Finished'}


class BackupCLITests(unittest.TestCase):
    def setUp(self):
        self.out = io.StringIO()
        self.err = io.StringIO()
    def invoke(self, argv, **kwargs):
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return main(argv, **kwargs)
    def test_status_never_runs(self):
        manager = FakeManager('/state')
        self.assertEqual(self.invoke(['status', '--state', '/state'], manager_type=lambda _:manager), 0)
        self.assertEqual(manager.calls, ['status'])
        self.assertIn('Connect backup drive B002', self.out.getvalue())
    def test_default_init_state_and_no_task_configuration(self):
        factory = mock.Mock()
        manager = FakeManager('/state')
        factory.initialize.return_value = manager
        self.assertEqual(self.invoke(['init','--project','/project','--spool','/spool'], manager_type=factory), 0)
        factory.initialize.assert_called_once_with(Path('/project/_DIT_NOTES/backup-rotation'), Path('/project'), Path('/spool'))
        self.assertEqual(manager.calls, ['status'])
    def test_run_discovers_before_running(self):
        manager = FakeManager('/state')
        self.assertEqual(self.invoke(['run','--state','/state','--queue-db','/queue'], manager_type=lambda _:manager), 0)
        self.assertEqual(manager.calls, [('discover', Path('/queue')), 'run'])
    def test_register_requires_explicit_review_flag_forwarded(self):
        manager = FakeManager('/state')
        args = ['add-drive','--state','/state','--label','B002','--mount','/drive','--destination-relative','FieldLab', '--task-uuid','uuid','--guard-path','/guard','--dedicated-task-reviewed']
        self.assertEqual(self.invoke(args, manager_type=lambda _:manager), 0)
        self.assertEqual(manager.calls[0], ('add', ('B002', Path('/drive'), 'FieldLab','uuid'), {'dedicated_task_reviewed':True,'guard_path':Path('/guard')}))
    def test_ui_does_not_start_backup_until_button(self):
        manager = FakeManager('/state')
        server = mock.Mock(origin='http://127.0.0.1:8778')
        serve = mock.Mock(return_value=server)
        opener = mock.Mock()
        self.assertEqual(self.invoke(['ui','--state','/state','--queue-db','/queue','--open'],manager_type=lambda _:manager,serve_fn=serve,opener=opener), 0)
        self.assertEqual(manager.calls, [])
        opener.assert_called_once_with(['/usr/bin/open','http://127.0.0.1:8778'], check=True,timeout=15)
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()
        serve.call_args.args[0].run_pending()
        self.assertEqual(manager.calls, [('discover',Path('/queue')), 'run'])
    def test_guard_written_only_to_new_requested_file(self):
        manager = FakeManager('/state')
        manager.state = {'spool':'/Volumes/PRIMARY/Spool','primary':{'uuid':'PRIMARY-UUID'},'sets':[{'label':'B001','mount':'/Volumes/SECONDARY','destination_relative':'FieldLab','uuid':'SECONDARY-UUID'}]}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder).resolve()/'guard.sh'
            args = ['guard','--state','/state','--label','B001','--output',str(output)]
            self.assertEqual(self.invoke(args,manager_type=lambda _:manager),0)
            text = output.read_text()
            self.assertIn('/bin/bash',text)
            self.assertIn('/Volumes/PRIMARY/Spool/B001/payload',text)
            self.assertIn('SECONDARY-UUID',text)
            self.assertEqual(output.stat().st_mode&0o777,0o700)
            self.assertEqual(self.invoke(args,manager_type=lambda _:manager),1)
            self.assertEqual(output.read_text(),text)
            self.assertEqual(manager.calls,[])

    def test_unknown_failure_reports_error(self):
        def broken(_):
            raise ValueError('State damaged')
        self.assertEqual(self.invoke(['status','--state','/state'],manager_type=broken),1)
        self.assertIn('State damaged',self.err.getvalue())
        self.assertEqual(self.out.getvalue(),'')


if __name__ == '__main__':
    unittest.main()
