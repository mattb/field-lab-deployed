"""Failure causes survive reconciliation, retries and atomic-write interruption."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from ditnotes import library,atomic
from ditnotes.engine import AnalysisError

class Engine:
    def __init__(self,error=None):self.error=error;self.calls=0
    def analyze(self,source,output):
        self.calls+=1
        if self.error:raise self.error
        (output/'result.json').write_text(json.dumps({'status':'complete','duration':1,'probe':{'streams':[]},'highlights':[],'tracks':[],'timing':{}}))

class FailureHistory(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();self.clip=self.project/'clip.mp4';self.clip.write_bytes(b'disposable source')
        self.state_path=self.project/'_DIT_NOTES/state.json'
        safety=patch('ditnotes.sqlite_safety.require_safe_sqlite');safety.start();self.addCleanup(safety.stop)
    def state(self):return json.loads(self.state_path.read_text())
    def process(self,engine,**kwargs):return library.process(self.clip,self.project,engine=engine,**kwargs)
    def test_failure_reconcile_retry_success_and_reuse_retain_causal_history(self):
        failed=self.process(Engine(AnalysisError('vlm','deterministic fixture failure')))
        self.assertEqual(failed['counts']['failed'],1);self.assertEqual(len(failed['failures']),1)
        cause=failed['failures'][0]
        self.assertEqual(cause['stage'],'vlm');self.assertEqual(cause['path'],'clip.mp4')
        self.assertEqual(cause['source_sha256'],hashlib.sha256(self.clip.read_bytes()).hexdigest());self.assertTrue(cause['profile_id']);self.assertTrue(cause['time'])
        library.reconcile(self.project)
        pending=self.state()['paths']['clip.mp4'];self.assertEqual(pending['status'],'pending');self.assertIsNone(pending.get('error'));self.assertEqual(pending['last_failure'],cause)
        retry=self.process(Engine(AnalysisError('asr','second fixture failure')))
        self.assertEqual(retry['failures'][0]['stage'],'asr');self.assertEqual(len(self.state()['failures']),2)
        success=self.process(Engine());self.assertEqual(success['failures'],[])
        state=self.state();self.assertEqual(state['paths']['clip.mp4']['status'],'ready');self.assertIsNone(state['paths']['clip.mp4']['error']);self.assertEqual(len(state['failures']),2)
        self.assertEqual(state['paths']['clip.mp4']['last_failure'],retry['failures'][0])
        reuse=Engine();self.process(reuse);self.assertEqual(reuse.calls,0);self.assertEqual(len(self.state()['failures']),2)
    def test_error_checkpoint_precedes_interrupting_progress_callback(self):
        def progress(path,status):
            if status.startswith('error:'):raise KeyboardInterrupt('operator stopped')
        with self.assertRaises(KeyboardInterrupt):self.process(Engine(RuntimeError('fixture cause')),progress=progress)
        self.assertEqual(self.state()['failures'][0]['error'],'fixture cause')
        library.reconcile(self.project);self.assertEqual(self.state()['paths']['clip.mp4']['last_failure']['error'],'fixture cause')
    def test_interrupted_atomic_failure_write_retains_prior_valid_history(self):
        self.process(Engine(RuntimeError('first durable cause')));cause=self.state()['failures'][0];save=library._save
        def interrupted(path,data):
            if Path(path)==self.state_path and len(data.get('failures',[]))>1:
                with patch.object(atomic.os,'replace',side_effect=OSError('simulated replacement interruption')):return save(path,data)
            return save(path,data)
        with patch.object(library,'_save',side_effect=interrupted),self.assertRaisesRegex(OSError,'replacement interruption'):
            self.process(Engine(RuntimeError('second unsaved cause')))
        state=self.state();self.assertEqual(state['failures'],[cause]);self.assertEqual(state['paths']['clip.mp4']['last_failure'],cause)
        self.assertEqual(list(self.state_path.parent.glob('.state.json.*.tmp')),[])
    def test_legacy_error_is_migrated_before_reconciliation_drops_current_status(self):
        self.process(Engine(RuntimeError('legacy cause')));state=self.state();state.pop('failures');state['paths']['clip.mp4'].pop('last_failure');atomic.atomic_json(self.state_path,state)
        library.reconcile(self.project);state=self.state()
        self.assertEqual(state['paths']['clip.mp4']['status'],'pending');self.assertEqual(state['paths']['clip.mp4']['last_failure']['error'],'legacy cause')
        self.assertTrue(state['failures'][0]['migrated_legacy_error'])
        library.reconcile(self.project);self.assertEqual(len(self.state()['failures']),1)
    def test_failure_paths_are_project_relative_and_external_error_paths_redacted(self):
        outside='/Volumes/Private/hidden.mp4';quoted="'/Users/private/Secret Media/recording.wav'"
        result=self.process(Engine(RuntimeError('source '+str(self.clip)+' external '+outside+' quoted '+quoted)))
        serialized=json.dumps(result['failures']);self.assertNotIn(str(self.project),serialized);self.assertNotIn(outside,serialized);self.assertNotIn('/Users/private',serialized)
        self.assertEqual(result['failures'][0]['path'],'clip.mp4');self.assertIn('<project>/clip.mp4',result['failures'][0]['error'])
    def test_rename_preserves_failure_identity_without_rewriting_history(self):
        self.process(Engine(RuntimeError('fixture failure')));cause=self.state()['failures'][0]
        renamed=self.project/'renamed.mp4';self.clip.rename(renamed)
        library.reconcile(self.project,input_paths=[self.project])
        state=self.state();self.assertEqual(state['paths']['renamed.mp4']['last_failure'],cause);self.assertEqual(state['failures'][0]['path'],'clip.mp4')

if __name__=='__main__':unittest.main()
