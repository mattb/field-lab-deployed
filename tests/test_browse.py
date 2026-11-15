import json,unittest
from test_metadata_api import API
from ditnotes.project_store import ProjectStore
class BrowseTests(API):
 def test_browse_is_live_compact_and_cached(self):
  code,body=self.request('/api/browse');self.assertEqual(code,200);data=json.loads(body)
  self.assertEqual(data['faces'][0]['contents'],['sha']);self.assertNotIn('embedding',body.decode());self.assertEqual(len(data['people']),2);self.assertTrue(all('members' not in p for p in data['people']))
  first=self.server.metadata.browse();self.assertIs(first,self.server.metadata.browse())
  self.request('/api/asset-tags',{'content_id':'sha','tags':['Ice'],'base_revision':0})
  updated=self.server.metadata.browse();self.assertIsNot(first,updated);self.assertEqual(updated['tags'][0]['contents'],['sha'])
 def test_face_merge_aggregates_unique_clip_membership(self):
  with ProjectStore(self.project/'_DIT_NOTES/project.sqlite3',project_id='fixture') as s:
   face=s.assign_face('sha',2,None,'another',.5,bbox=[.5,.1,.9,.5])['face_id'];s.merge_faces(face,self.face,'test same person')
  data=self.server.metadata.browse();self.assertEqual(len(data['faces']),1);self.assertEqual(data['faces'][0]['contents'],['sha'])
