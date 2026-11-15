import json,tempfile,unittest
from pathlib import Path
from ditnotes.project_store import ProjectStore
from ditnotes.search_projection import project_documents
from deploy.people import migrate,legacy_fingerprints
from deploy.manager import check_data_capabilities
class PeopleIntegration(unittest.TestCase):
 def test_backup_migrate_and_projection_distinguish_visible_and_heard(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);n=p/'_DIT_NOTES';n.mkdir();db=n/'project.sqlite3'
   with ProjectStore(db,'p') as s:
    f=s.assign_face('a',1,None,'face',.4,bbox=[.1,.1,.4,.4])['face_id'];s.name_face(f,'Alex')
    v=s.provisional_speaker('a','voice',0,3)['person_id'];s.name_person(v,'Blair')
    before=legacy_fingerprints(s.db)
   report=migrate(p);self.assertTrue(Path(report['backup']).is_file());self.assertTrue(report['legacy_unchanged'])
   with ProjectStore(db,'p') as s:
    self.assertEqual(before,legacy_fingerprints(s.db));a=s.person_for_evidence('face',f);b=s.person_for_evidence('voice',v)
    cat={'project':{'id':'p'},'assets':[{'id':'a','content_id':'a','name':'a.mp4','relative_path':'a.mp4','highlights':[],'transcript':[{'start':0,'end':2,'text':'Hello.'}]}]}
    docs=project_documents(cat,s)['documents'];speech=next(d for d in docs if d['kind']=='speech');face=next(d for d in docs if d['kind']=='face')
    self.assertEqual(speech['heardPersonIds'],[b]);self.assertEqual(speech['visiblePersonIds'],[]);self.assertEqual(face['visiblePersonIds'],[a]);self.assertEqual(face['heardPersonIds'],[])
    s.merge_unified_people(b,a,'Reviewed same person',chosen_name='Alex');s.note_unified_person(a,'Expedition participant')
    docs=project_documents(cat,s)['documents'];self.assertEqual(next(d for d in docs if d['kind']=='speech')['personNames'],['Alex']);self.assertTrue(any(d['source']=='operator_person_note' for d in docs))
 def test_old_writer_activation_is_refused(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);new=p/'new';old=p/'old';(new/'deploy').mkdir(parents=True);old.mkdir()
   (new/'deploy/data-capabilities.json').write_text('{"unified_people":1}')
   with self.assertRaisesRegex(ValueError,'cannot safely write'):check_data_capabilities(new,old)
   check_data_capabilities(old,new)
