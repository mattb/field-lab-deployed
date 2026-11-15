import importlib.util,json,shutil,tempfile,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('premiere_demo',ROOT/'development/premiere_demo.py');demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
from ditnotes.project_store import ProjectStore

class PremiereDemo(unittest.TestCase):
 def test_refuses_existing_destination_before_writing(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp);sentinel=path/'keep';sentinel.write_text('existing')
   with self.assertRaises(FileExistsError):demo.build(path,ffmpeg='/unused')
   self.assertEqual(sentinel.read_text(),'existing');self.assertEqual(list(path.iterdir()),[sentinel])
 @unittest.skipUnless(shutil.which('ffmpeg'),'ffmpeg required for synthetic media smoke test')
 def test_generated_corpus_has_exact_tags_authored_words_and_native_payload(self):
  with tempfile.TemporaryDirectory() as temp:
   result=demo.build(Path(temp)/'New synthetic project',speech=False)
   project=Path(result['project']);cat=json.loads(Path(result['catalog']).read_text());expected=json.loads(Path(result['expected']).read_text())
   self.assertEqual(len(cat['assets']),4);self.assertLess(result['duration_seconds'],60);self.assertFalse(result['speech'])
   self.assertEqual(set(expected['bins']['Sea ice']),{'Morgan interview.mp4','Casey sea ice field.mp4'})
   self.assertEqual(expected['bins']['Sea ice cream'],['Sea ice cream.mp4'])
   with ProjectStore(project/'_DIT_NOTES/project.sqlite3',project_id=cat['project']['id']) as store:
    self.assertEqual(len(store.annotations()),8)
    for asset in cat['assets']:
     self.assertTrue((project/asset['relative_path']).is_file())
     data=asset['transcript_json'];self.assertEqual(set(data),{'language','segments','speakers'})
     speaker=data['speakers'][0];self.assertEqual(uuid.UUID(speaker['id']).version,4)
     segment=data['segments'][0];self.assertEqual(segment['speaker'],speaker['id'])
     self.assertEqual(' '.join(w['text'] for w in segment['words']),asset['transcript'][0]['text'])
     previous=0
     for w in segment['words']:
      self.assertEqual(set(w),{'confidence','duration','eos','start','tags','text','type'})
      self.assertGreaterEqual(w['start'],previous);self.assertLessEqual(w['start']+w['duration'],asset['duration']);previous=w['start']+w['duration']
     self.assertEqual(len(asset['outline']),1)
    self.assertEqual(len(store.faces()),2);self.assertEqual(len(store.people()),2)
    self.assertTrue(all(o['embedding'] is None for o in store.observations()))
if __name__=='__main__':unittest.main()
