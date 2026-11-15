"""Independent disclosure, path safety and atomic publication review for static exports."""
import copy,hashlib,json,os,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch

from ditnotes.atomic import atomic_json
from ditnotes.catalog_index import build
from ditnotes.project_store import ProjectStore
from ditnotes.search_projection import project_documents
from ditnotes.static_export import export


class StaticExportSafety(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.project=self.root/'project';self.project.mkdir()
  self.notes=self.project/'_DIT_NOTES';self.notes.mkdir();self.db=self.notes/'project.sqlite3';self.output=self.root/'public'
  (self.project/'day').mkdir();(self.project/'day/interview.mp4').write_bytes(b'synthetic video bytes')
  (self.notes/'display.jpg').write_bytes(b'synthetic display jpeg')
  (self.notes/'unreferenced.jpg').write_bytes(b'NOT_REFERENCED_PRIVATE_IMAGE')
  (self.notes/'raw-analysis.json').write_text('{"embedding":"PRIVATE_ANALYSIS_SENTINEL"}')
  self.catalog={'project':{'id':'fixture','name':'Read only fixture','private':'PRIVATE_PROJECT_SENTINEL'},'assets':[{'id':'asset-one','content_id':'sha-one','name':'interview.mp4','relative_path':'day/interview.mp4','duration':12,'kind':'video','poster_url':'/notes/display.jpg','transcript':[{'start':1,'end':3,'text':'Public transcript','private':'PRIVATE_TOKEN_SENTINEL'}],'highlights':[{'seconds':1,'note':'Public visual note','image_url':'/notes/display.jpg'}],'private':'PRIVATE_ASSET_SENTINEL'}]}
  with ProjectStore(self.db,'fixture') as s:
   face=s.assign_face('sha-one',1,[1,0],'face-private-model',1,bbox=[.1,.1,.8,.8],frame_ref='/notes/display.jpg',crop_ref='/notes/display.jpg')['face_id'];s.name_face(face,'Public person')
   s.assign_speaker('sha-one','speaker0',0,4,[1,0],'voice-private-model',1)
   s.add_annotation('sha-one','Public note')
   s.ensure_unified_people();self.catalog['search_projection']=project_documents(self.catalog,s)
   self.catalog['search_projection']['documents'][0]['provenance']={'secret':'PRIVATE_PROVENANCE_SENTINEL'}
   with s._transaction():s._audit('private_test_diagnostic',{'secret':'PRIVATE_AUDIT_SENTINEL'})
   # Refresh the projection revision after fixture diagnostics; the data itself
   # intentionally retains a nonpublic field for exporter allowlist coverage.
   self.catalog['search_projection']['edit_revision']=s.edit_revision;s.mark_indexed(s.edit_revision)
  self.tool=self.root/'tool';runtime=self.tool/'models/.runtime';runtime.mkdir(parents=True)
  atomic_json(runtime/'search-readiness.json',{'schema_version':1,'ready':True,'models':{'embeddings':{'ready':True,'revision':'fixture'}},'runtime':{'ready':True}})
  (runtime/'search-models/embeddings').mkdir(parents=True);(runtime/'search-models/embeddings/config.json').write_text('{}')
  (runtime/'browser').mkdir();(runtime/'browser/runtime.wasm').write_bytes(b'wasm fixture')
  self.ui=self.root/'ui';(self.ui/'assets').mkdir(parents=True);(self.ui/'static.html').write_text('<html>Static fixture</html>');(self.ui/'assets/static.js').write_text('console.log("Read only")')
  self.model_patches=[patch('prepare_search.check',return_value={'ready':True}),patch('prepare_search.MODEL_LOCK',{'embeddings':{'files':[{'path':'config.json'}]}}),patch('prepare_search.RUNTIME_LOCK',{'runtime.wasm':(12,'fixture')})]
  for mocked in self.model_patches:mocked.start()
  self.publish()

 def tearDown(self):
  for mocked in reversed(self.model_patches):mocked.stop()
  self.tmp.cleanup()

 def publish(self):
  def infer(action,values,query):return {'vectors':[[1]+[0]*(383 if action=='embed_text' else 511) for _ in values]}
  build(self.project,self.catalog,infer,{'text':'fixture-text','clip':'fixture-clip','reranker':'fixture-reranker'})
  atomic_json(self.notes/'catalog.json',self.catalog)

 def run_export(self):return export(self.project,self.output,tool_root=self.tool,ui_dist=self.ui)

 def test_only_display_projection_and_allowlisted_referenced_files_are_public(self):
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();report=self.run_export()
  self.assertEqual(report['assets'],1);self.assertTrue((self.output/'index.html').is_file())
  self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(),before)
  self.assertFalse(list(self.output.rglob('*.sqlite3')))
  self.assertFalse(list(self.output.rglob('*.sqlite')))
  self.assertFalse((self.output/'notes/unreferenced.jpg').exists());self.assertFalse((self.output/'notes/raw-analysis.json').exists())
  text='\n'.join(p.read_text() for p in (self.output/'data').glob('*.json'))
  for token in ['PRIVATE_','face-private-model','voice-private-model','"embedding"','"centroid"','"quality_components"','"bbox"']:
   self.assertNotIn(token,text)
  cat=json.loads((self.output/'data/catalog.json').read_text());a=cat['assets'][0]
  self.assertEqual(a['media_url'],'/media/asset-one.mp4')
  self.assertEqual(a['transcript'][0]['text'],'Public transcript')
  self.assertTrue((self.output/'notes/display.jpg').is_file())

 def test_existing_export_is_never_overwritten(self):
  self.output.mkdir();(self.output/'operator.txt').write_text('Keep')
  with self.assertRaises(ValueError):self.run_export()
  self.assertEqual((self.output/'operator.txt').read_text(),'Keep')

 def test_stale_metadata_revision_refuses_publication(self):
  with ProjectStore(self.db,'fixture') as s:s.add_annotation('sha-one','New operator note')
  with self.assertRaisesRegex(ValueError,'changed'):self.run_export()
  self.assertFalse(self.output.exists())

 def test_unsafe_note_url_refuses_publication(self):
  self.catalog['assets'][0]['poster_url']='/notes/../project.sqlite3';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_unknown_note_image_refuses_publication(self):
  self.catalog['assets'][0]['poster_url']='/notes/missing.jpg';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_image_symlink_refuses_publication(self):
  (self.notes/'linked.jpg').symlink_to(self.notes/'display.jpg');self.catalog['assets'][0]['poster_url']='/notes/linked.jpg';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_external_note_url_refuses_publication(self):
  self.catalog['assets'][0]['poster_url']='https://outside.example/private.jpg';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_media_symlink_refuses_publication(self):
  (self.project/'linked.mp4').symlink_to(self.project/'day/interview.mp4');self.catalog['assets'][0]['relative_path']='linked.mp4';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_media_parent_symlink_refuses_even_if_resolving_inside_project(self):
  (self.project/'alias').symlink_to(self.project/'day',target_is_directory=True);self.catalog['assets'][0]['relative_path']='alias/interview.mp4';self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())

 def test_unsafe_asset_identifier_cannot_escape_the_staging_directory(self):
  # Merely reading a sealed catalog does not make its identifiers safe paths.
  self.catalog['assets'][0]['id']='../../escaped';self.catalog['search_projection']['documents']=[];self.publish()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists());self.assertFalse((self.root/'escaped.mp4').exists())

 def test_private_extra_database_in_ui_build_does_not_enter_export(self):
  (self.ui/'project.sqlite3').write_bytes(b'PRIVATE_BUILD_SENTINEL')
  try:self.run_export()
  except ValueError:pass
  self.assertFalse((self.output/'project.sqlite3').exists())

 def test_incomplete_copy_cannot_activate_snapshot(self):
  with patch('ditnotes.static_export._copy',side_effect=OSError('Synthetic interrupted copy')):
   with self.assertRaises(OSError):self.run_export()
  self.assertFalse(self.output.exists())
  self.assertTrue(list(self.root.glob('.public-*.pending')))

 def test_metadata_edit_during_copy_prevents_snapshot_activation(self):
  from ditnotes.static_export import _copy
  edited=False
  def copy_and_edit(source,target):
   nonlocal edited
   _copy(source,target)
   if not edited:
    edited=True
    with ProjectStore(self.db,'fixture') as store:store.add_annotation('sha-one','Concurrent saved operator note')
  with patch('ditnotes.static_export._copy',side_effect=copy_and_edit):
   with self.assertRaisesRegex(ValueError,'changed'):self.run_export()
  self.assertFalse(self.output.exists())

 def test_missing_web_entry_cannot_publish_an_unusable_site(self):
  (self.ui/'static.html').unlink()
  with self.assertRaises(ValueError):self.run_export()
  self.assertFalse(self.output.exists())


if __name__=='__main__':unittest.main()
