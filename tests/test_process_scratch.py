"""Process-level generated-directory cleanup never destroys the causal error."""
import errno
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from ditnotes import library
from ditnotes.engine import AnalysisError

class Engine:
    def __init__(self,error=None):self.error=error;self.calls=0
    def analyze(self,source,output):
        self.calls+=1
        if self.error:raise self.error
        (output/'result.json').write_text(json.dumps({'status':'complete','duration':1,'probe':{'streams':[]},'highlights':[],'tracks':[],'timing':{}}))

class ProcessScratch(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.project=Path(self.temp.name).resolve();self.clip=self.project/'clip.mp4';self.clip.write_bytes(b'original source')
        safety=patch('ditnotes.sqlite_safety.require_safe_sqlite');safety.start();self.addCleanup(safety.stop)
    def run_process(self,engine):return library.process(self.clip,self.project,engine=engine)
    def test_failure_then_three_metadata_races_keeps_cause_and_removes_scratch(self):
        remove=shutil.rmtree;calls=[];engine=Engine(AnalysisError('vlm','actual model failure'))
        def racing(path):
            calls.append(Path(path))
            if len(calls)<=3:
                (Path(path)/'.DS_Store').write_bytes(b'concurrent metadata')
                raise OSError(errno.ENOTEMPTY,'Directory not empty')
            remove(path)
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=racing),patch('ditnotes.scratch.time.sleep'):
            result=self.run_process(engine)
        self.assertEqual(result['counts']['failed'],1);self.assertEqual(engine.calls,1);self.assertEqual(len(calls),4)
        self.assertIn('actual model failure',result['failures'][0]['error']);self.assertEqual(result['failures'][0]['stage'],'vlm')
        self.assertFalse(calls[0].exists());self.assertEqual(self.clip.read_bytes(),b'original source')
    def test_persistent_outer_cleanup_failure_does_not_replace_model_failure(self):
        error=AnalysisError('asr','primary transcription failure');engine=Engine(error)
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=OSError(errno.ENOTEMPTY,'cleanup race')) as remove,patch('ditnotes.scratch.time.sleep'):
            result=self.run_process(engine)
        self.assertEqual(remove.call_count,6);self.assertEqual(result['counts']['failed'],1)
        self.assertIn('primary transcription failure',result['failures'][0]['error']);self.assertNotIn('cleanup race',result['failures'][0]['error']);self.assertEqual(result['failures'][0]['stage'],'asr')
        self.assertIn('cleanup also failed',error.__notes__[0]);self.assertEqual(list((self.project/'_DIT_NOTES/.objects').glob('*/*/complete.json')),[])
    def test_transient_cleanup_race_after_seal_still_publishes_once_without_rerun(self):
        remove=shutil.rmtree;calls=[];engine=Engine()
        def racing(path):
            path=Path(path);calls.append(path)
            if len(calls)==1:
                # Atomic publication moved the original scratch; simulate a
                # metadata writer recreating that owned generated pathname.
                path.mkdir(exist_ok=True);(path/'.DS_Store').write_bytes(b'metadata')
                raise OSError(errno.ENOTEMPTY,'Directory not empty')
            remove(path)
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=racing),patch('ditnotes.scratch.time.sleep'):
            result=self.run_process(engine)
        self.assertEqual(result['counts']['processed'],1);self.assertEqual(result['counts']['failed'],0);self.assertEqual(result['failures'],[])
        self.assertEqual(engine.calls,1);self.assertFalse(calls[0].exists())
        state=json.loads((self.project/'_DIT_NOTES/state.json').read_text())['paths']['clip.mp4'];self.assertEqual(state['status'],'ready')
        target=self.project/'_DIT_NOTES/.objects'/state['sha256']/state['profile_id'];self.assertTrue(library._valid_object(target,state['sha256'],state['profile_id']))
        self.assertEqual(self.run_process(engine)['counts']['reused'],1);self.assertEqual(engine.calls,1)
    def test_permanent_cleanup_after_seal_remains_reusable_without_reinference(self):
        engine=Engine()
        with patch('ditnotes.scratch.shutil.rmtree',side_effect=OSError(errno.ENOTEMPTY,'persistent metadata race')),patch('ditnotes.scratch.time.sleep'):
            failed=self.run_process(engine)
        self.assertEqual(failed['counts']['failed'],1);self.assertEqual(engine.calls,1)
        retry=self.run_process(engine);self.assertEqual(retry['counts']['reused'],1);self.assertEqual(retry['counts']['failed'],0);self.assertEqual(engine.calls,1)
        self.assertEqual(self.clip.read_bytes(),b'original source')

if __name__=='__main__':unittest.main()
