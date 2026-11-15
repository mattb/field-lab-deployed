"""Real loopback requests and shared projection, no real project/media changes."""
import json
import unittest
from tests import test_metadata_api as fixture
from ditnotes.project_store import ProjectStore
from ditnotes.search_projection import project_documents

class AssetTagHTTPTests(unittest.TestCase):
    setUp=fixture.API.setUp
    tearDown=fixture.API.tearDown
    request=fixture.API.request
    def test_asset_tags_survive_reopen_and_are_independent_of_notes(self):
        code,raw=self.request('/api/asset-tags?content_id=sha');self.assertEqual(code,200)
        self.assertEqual(json.loads(raw)['tags'],[])
        code,raw=self.request('/api/asset-tags',{'content_id':'sha','tags':['North field','B-roll'],'base_revision':0})
        self.assertEqual(code,200);view=json.loads(raw)
        self.assertEqual([t['name'] for t in view['tags']],['North field','B-roll'])
        self.assertEqual(view['revision'],1)
        with ProjectStore(self.project/'_DIT_NOTES/project.sqlite3',project_id='fixture') as store:
            self.assertEqual(store.asset_tags('sha')['tags'],view['tags']);self.assertEqual(store.annotations('sha'),[])
            catalog=self.server.catalog.read();docs=project_documents(catalog,store)['documents']
            tag=next(d for d in docs if d['kind']=='tag')
            self.assertFalse(tag['embed']);self.assertEqual(tag['source'],'asset_tag')
            self.assertEqual(tag['tagNames'],['North field','B-roll'])
            self.assertEqual(tag['tagIds'],[t['id'] for t in view['tags']])
            self.assertEqual(next(d for d in docs if d['kind']=='speech')['tagIds'],tag['tagIds'])
        code,raw=self.request('/api/asset-tags',{'content_id':'sha','tags':[],'base_revision':1})
        self.assertEqual(code,200);self.assertFalse(json.loads(raw)['tags'])
        self.assertEqual({t['name'] for t in json.loads(raw)['vocabulary']},{'North field','B-roll'})
    def test_conflicting_browser_save_is_409_and_preserves_first_writer(self):
        self.assertEqual(self.request('/api/asset-tags',{'content_id':'sha','tags':['Interview'],'base_revision':0})[0],200)
        code,raw=self.request('/api/asset-tags',{'content_id':'sha','tags':['B-roll'],'base_revision':0})
        self.assertEqual(code,409);self.assertEqual([t['name'] for t in json.loads(raw)['current']['tags']],['Interview'])
    def test_unknown_asset_bad_query_and_cross_origin_cannot_mutate(self):
        for path in ['/api/asset-tags','/api/asset-tags?content_id=unknown','/api/asset-tags?content_id=sha&content_id=unknown']:
            self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.request('/api/asset-tags',{'content_id':'sha','tags':['No'],'base_revision':0},origin='https://example.com')[0],403)
        self.assertEqual(self.request('/api/asset-tags',{'content_id':'unknown','tags':['No'],'base_revision':0})[0],400)
        self.assertEqual(json.loads(self.request('/api/asset-tags?content_id=sha')[1])['tags'],[])

if __name__=='__main__':unittest.main()
