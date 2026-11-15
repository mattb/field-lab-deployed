"""Local-folder multi-primary cache/reconnect/recovery tests. No disks or model inference."""
import copy
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from ditnotes.multivolume import MultiVolumeLibrary
from ditnotes.catalog_index import build
from ditnotes.server import create_server


class FakeBackend:
    def __init__(self,*args,**kwargs):pass
    def readiness(self):return {'ready':False}
    def close(self):pass


class MultiVolumeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name).resolve()
        self.shared=self.base/'shared';self.shared.mkdir();self.connected={'old':True,'annex':True};self.uuid={'old':'OLD-UUID','annex':'ANNEX-UUID'}
        profiles={}
        for pid in ['old','annex']:
            mount=self.base/pid;project=mount/'Ingests';project.mkdir(parents=True)
            (project/'clip.mp4').write_bytes((pid+' media').encode());notes=project/'_DIT_NOTES';notes.mkdir();(notes/'frame.jpg').write_bytes((pid+' preview').encode())
            asset={'id':'same-asset','content_id':'same-content','name':'clip.mp4','relative_path':'clip.mp4','media_url':'/media/clip.mp4','original_url':'/media/clip.mp4','poster_url':'/notes/frame.jpg','highlights':[{'seconds':0,'note':pid+' image','image_url':'/notes/frame.jpg'}],'transcript':[{'start':0,'end':2,'text':pid+' spoken words'}]}
            documents=[{'id':'same-doc','assetId':'same-asset','contentId':'same-content','title':'clip.mp4','path':'clip.mp4','kind':'speech','text':pid+' spoken words','seconds':0,'embed':True}]
            catalog={'project':{'id':pid+'-project','name':pid},'assets':[asset],'search_projection':{'documents':documents}}
            build(project,catalog,lambda action,values,query:{'vectors':[[.1]*384 for _ in values]}, {'text':'text-v1','clip':'clip-v1','reranker':'rerank-v1'})
            profiles[pid]={'id':pid,'label':'ThunderBlade' if pid=='old' else 'ThunderBlade Annex','volume_uuid':self.uuid[pid],'volume_mount':str(mount),'ingest_folder':'Ingests','project_root':str(project),'backup':{'current_set':'B001','sets':{}}}
        self.registry={'schema':1,'shoot_id':'shoot1','name':'Field shoot','active_primary':'annex','profiles':profiles,'revision':4,'pending_cutover':None,'history':[]}
        (self.shared/'shoot.json').write_text(json.dumps(self.registry))
        self.library=MultiVolumeLibrary(self.shared,probe=self.probe);self.addCleanup(self.library.close)
    def probe(self,path):
        pid=Path(path).name
        if not self.connected[pid]:raise OSError('Disconnected')
        return {'uuid':self.uuid[pid],'mount':str(path),'physical':[pid],'filesystem':'apfs'}
    def project(self,pid):return Path(self.registry['profiles'][pid]['project_root'])
    def request(self,server,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection(*server.server_address,timeout=5)
        headers={**({'Content-Type':'application/json'} if body is not None else {}),**(headers or {})}
        connection.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
        response=connection.getresponse();status=response.status;payload=response.read();connection.close();return status,payload
    def server(self,project=None):
        tool=self.base/'tool';tool.mkdir(exist_ok=True)
        with patch('ditnotes.server.SearchBackend',FakeBackend):server=create_server(project or self.project('annex'),0,tool,shoot_library=self.library)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup():server.shutdown();server.server_close();thread.join(3)
        self.addCleanup(cleanup);return server
    def test_metadata_snapshot_preserves_tags_people_and_original_database(self):
        from ditnotes.project_store import ProjectStore
        import sqlite3
        guard=patch('ditnotes.sqlite_safety.require_safe_sqlite');guard.start();self.addCleanup(guard.stop)
        db=self.project('old')/'_DIT_NOTES/project.sqlite3'
        with ProjectStore(db,'old-project') as store:
            store.set_asset_tags('same-content',['Life on station'],0)
            store.add_annotation('same-content','Operator note')
            face=store.assign_face('same-content',0,None,'fixture',.4,bbox=[.1,.1,.4,.4],crop_ref='/notes/frame.jpg')['face_id']
            store.name_face(face,'Operator label')
        with sqlite3.connect(db) as connection:before=connection.execute('SELECT operation,payload FROM audit ORDER BY rowid').fetchall()
        self.library.refresh('old');self.connected['old']=False
        state=self.library.cached_api('old','/api/metadata')
        self.assertEqual(state['annotations'][0]['text'],'Operator note')
        self.assertEqual(state['asset_tags'][0]['name'],'Life on station')
        people=self.library.people()['people'];self.assertEqual(people[0]['name'],'Operator label')
        self.assertTrue(people[0]['id'].startswith('old::'));self.assertEqual(people[0]['appearances'][0]['content_id'],'old::same-content')
        self.assertEqual(people[0]['face_samples'][0]['crop_ref'],'/primaries/old/notes/frame.jpg')
        with sqlite3.connect(db) as connection:self.assertEqual(connection.execute('SELECT operation,payload FROM audit ORDER BY rowid').fetchall(),before)
    def test_cached_preview_and_source_notes_symlinks_fail_closed(self):
        self.library.refresh('old')
        notes=self.project('old')/'_DIT_NOTES';notes.rename(self.project('old')/'saved-notes');notes.symlink_to(self.project('old')/'saved-notes',target_is_directory=True)
        self.assertFalse(self.library.available('old'));self.library.refresh('old')
        self.assertEqual(self.library.cached('old')['catalog']['assets'][0]['name'],'clip.mp4')
    def test_offline_transition_changes_catalog_revision_but_keeps_vector_ids(self):
        self.library.refresh();before=self.library.catalog()['catalog_revision'];ids={r['document_id'] for r in self.library.vectors()['vectors']}
        self.connected['old']=False
        self.assertNotEqual(self.library.catalog()['catalog_revision'],before)
        self.assertEqual({r['document_id'] for r in self.library.vectors()['vectors']},ids)
    def test_legacy_raw_catalogue_retains_transcript_search_documents(self):
        import shutil
        notes=self.project('old')/'_DIT_NOTES';raw=json.loads((notes/'.catalogs'/json.loads((notes/'current.json').read_text())['catalog_revision']/'catalog.json').read_text());raw.pop('search_projection')
        (notes/'current.json').unlink();shutil.rmtree(notes/'.catalogs');(notes/'catalog.json').write_text(json.dumps(raw))
        self.library.refresh('old');docs=self.library.catalog()['search_projection']['documents']
        self.assertTrue(any(row['text']=='old spoken words' and row['kind']=='speech' for row in docs))
    def test_duplicate_content_on_two_primaries_has_distinct_assets_docs_and_vectors(self):
        self.library.refresh();catalog=self.library.catalog();vectors=self.library.vectors()
        self.assertEqual({a['id'] for a in catalog['assets']},{'old::same-asset','annex::same-asset'})
        self.assertEqual({a['content_id'] for a in catalog['assets']},{'old::same-content','annex::same-content'})
        self.assertEqual({d['id'] for d in catalog['search_projection']['documents']},{'old::same-doc','annex::same-doc'})
        self.assertEqual({v['document_id'] for v in vectors['vectors']},{'old::same-doc','annex::same-doc'})
        self.assertEqual(vectors['catalog_revision'],catalog['catalog_revision'])
        self.assertEqual({a['original_relative_path'] for a in catalog['assets']},{'clip.mp4'})
    def test_offline_old_volume_remains_searchable_with_cached_preview(self):
        self.library.refresh();self.connected['old']=False;self.library.refresh()
        old=next(a for a in self.library.catalog()['assets'] if a['primary_id']=='old')
        self.assertFalse(old['available']);self.assertEqual(old['transcript'][0]['text'],'old spoken words')
        image=self.library.resolve_image(old['poster_url']);self.assertEqual(image.read_bytes(),b'old preview')
        root=self.library.cached('old')['root'];self.assertFalse(list(root.rglob('*.mp4')));self.assertFalse(list(root.rglob('*.sqlite3')))
    def test_same_name_wrong_uuid_never_exposes_live_media_or_writes(self):
        server=self.server();self.uuid['old']='WRONG-UUID'
        code,_=self.request(server,'GET','/primaries/old/media/clip.mp4');self.assertNotEqual(code,200)
        code,payload=self.request(server,'POST','/primaries/old/api/annotation',{'content_id':'same-content','text':'must not save'})
        self.assertEqual(code,409);self.assertFalse((self.project('old')/'_DIT_NOTES/project.sqlite3').exists())
        code,body=self.request(server,'GET','/api/shoot/catalog');self.assertEqual(code,200);self.assertEqual(len(json.loads(body)['assets']),2)
    def test_root_background_workers_refuse_a_replaced_primary(self):
        server=self.server()
        self.assertIs(server.metadata,self.library.contexts['annex'].metadata)
        self.uuid['annex']='REPLACEMENT'
        with self.assertRaises(OSError):server.metadata.store()
        with self.assertRaises(OSError):server.premiere_worker.tick()
    def test_reconnect_old_does_not_change_ingest_or_backup_ownership(self):
        before=(self.shared/'shoot.json').read_bytes();self.library.refresh();self.connected['old']=False;self.library.refresh();self.connected['old']=True;self.library.refresh()
        self.assertEqual(self.library.status()['active_primary'],'annex');self.assertEqual((self.shared/'shoot.json').read_bytes(),before)
    def test_source_publish_during_capture_is_not_marked_fully_current(self):
        changed=[]
        def update(phase):
            if phase=='database_copied' and not changed:
                changed.append(True)
                from ditnotes.catalog_index import Snapshots
                catalog=Snapshots(self.project('old')).read();catalog['assets'][0]['name']='new publication.mp4'
                build(self.project('old'),catalog,lambda action,values,query:{'vectors':[[.1]*384 for _ in values]},{'text':'text-v1','clip':'clip-v1','reranker':'rerank-v1'})
        self.library.checkpoint=update;self.library.refresh('old')
        first=self.library.cached('old')
        self.assertEqual(first['catalog']['assets'][0]['name'],'clip.mp4')
        self.assertEqual(first['catalog']['catalog_revision'],first['vectors']['catalog_revision'])
        self.library.checkpoint=lambda phase:None;self.library.refresh('old')
        self.assertEqual(self.library.cached('old')['catalog']['assets'][0]['name'],'new publication.mp4')
    def test_cache_crash_before_publish_retains_old_complete_generation(self):
        self.library.refresh();old=self.library.cached('old')['catalog'];pointer=(self.library._root('old')/'current.json').read_bytes()
        # Changed ordinary fallback does not supersede a valid sealed publication; edit the source seal via build.
        catalog=copy.deepcopy(old);catalog['assets'][0]['name']='updated.mp4';build(self.project('old'),catalog,lambda action,values,query:{'vectors':[[.1]*384 for _ in values]},{'text':'text-v1','clip':'clip-v1','reranker':'rerank-v1'})
        def fail(phase):
            if phase=='before_cache_publish':raise OSError('Power interrupted')
        self.library.checkpoint=fail
        with self.assertRaises(OSError):self.library.refresh('old')
        self.assertEqual((self.library._root('old')/'current.json').read_bytes(),pointer)
        self.assertEqual(self.library.cached('old')['catalog'],old)
        self.library.checkpoint=lambda phase:None;self.library.refresh('old')
        self.assertEqual(self.library.cached('old')['catalog']['assets'][0]['name'],'updated.mp4')
    def test_cached_and_live_route_traversal_refused(self):
        server=self.server()
        for path in ['/primaries/old/notes/../shoot.json','/primaries/old/media/../shoot.json','/primaries/old/notes/%2e%2e/shoot.json','/primaries/not-known/media/clip.mp4']:
            with self.subTest(path=path):self.assertNotEqual(self.request(server,'GET',path)[0],200)
    def test_bootstrap_library_starts_when_all_volumes_offline(self):
        self.library.refresh();self.connected={'old':False,'annex':False}
        # Missing actual roots do not get recreated under a missing mount.
        shutil.rmtree(self.project('annex'))
        server=self.server(project=self.project('annex'))
        self.assertFalse(self.project('annex').exists());self.assertFalse(hasattr(server,'premiere_worker'))
        code,body=self.request(server,'GET','/api/shoot/catalog');self.assertEqual(code,200);self.assertEqual(len(json.loads(body)['assets']),2)
        code,body=self.request(server,'GET','/primaries/old/api/metadata');self.assertEqual(code,200);self.assertTrue(json.loads(body)['readonly'])
        code,body=self.request(server,'GET','/api/catalog');self.assertEqual(code,200);self.assertFalse(json.loads(body)['available'])
    def test_backup_action_requires_origin_token_and_explicit_old_pair(self):
        self.registry['profiles']['old']['backup']['sets']['B001']={'task_uuid':'11111111-1111-4111-8111-111111111111'}
        (self.shared/'shoot.json').write_text(json.dumps(self.registry));server=self.server()
        code,data=self.request(server,'GET','/api/shoot');shoot=json.loads(data);self.assertEqual(code,200)
        headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'X-Field-Lab-CSRF':shoot['csrf_token']}
        body={'primary_id':'old','set_label':'B001'}
        with patch('deploy.backup_shortcut.run_primary_task',return_value={'requested':True,'verification_certified':False}) as run:
            self.assertEqual(self.request(server,'POST','/api/shoot/backup',body)[0],403)
            wrong={**headers,'Origin':'https://unrelated.example'}
            self.assertEqual(self.request(server,'POST','/api/shoot/backup',body,wrong)[0],403)
            self.assertEqual(self.request(server,'POST','/api/shoot/backup',body,headers)[0],202)
            import time
            for _ in range(100):
                if not server.backup_status['running']:break
                time.sleep(.01)
            run.assert_called_once_with(self.shared.parent,'old','B001')
            self.assertEqual(json.loads((self.shared/'shoot.json').read_text())['active_primary'],'annex')
            self.assertFalse(server.backup_status['result']['verification_certified'])
    def test_backup_attempt_survives_restart_and_is_scoped_to_registered_pair(self):
        self.registry['profiles']['old']['backup']['sets']['B001']={'task_uuid':'old-task'}
        (self.shared/'shoot.json').write_text(json.dumps(self.registry))
        folder=self.shared/'runtime/backup-shortcut/attempts';folder.mkdir(parents=True)
        path=folder/'old-B001.json'
        receipt={'primary_id':'old','set_label':'B001','task_uuid':'old-task','state':'launching','attempt_id':'attempt1','created_at':123,'backup_verified':True}
        path.write_text(json.dumps(receipt));(folder/'unregistered.json').write_text('secret')
        server=self.server()
        code,body=self.request(server,'GET','/api/shoot/backup');state=json.loads(body)
        self.assertEqual(code,200);self.assertEqual(state['status'],'needs_history_review');self.assertFalse(state['running'])
        self.assertEqual(len(state['attempts']),1);self.assertTrue(state['attempts'][0]['needs_history_review']);self.assertFalse(state['attempts'][0]['backup_verified'])
        receipt['state']='ccc_finished';path.write_text(json.dumps(receipt))
        state=json.loads(self.request(server,'GET','/api/shoot/backup')[1]);self.assertFalse(state['attempts'][0]['needs_history_review']);self.assertEqual(state['attempts'][0]['state'],'ccc_finished')
        receipt.update(state='reconciled');path.write_text(json.dumps(receipt))
        self.assertEqual(json.loads(self.request(server,'GET','/api/shoot/backup')[1])['attempts'][0]['state'],'reconciled')
    def test_backup_attempt_symlink_or_mismatched_receipt_needs_review(self):
        self.registry['profiles']['old']['backup']['sets']['B001']={'task_uuid':'old-task'}
        (self.shared/'shoot.json').write_text(json.dumps(self.registry))
        folder=self.shared/'runtime/backup-shortcut/attempts';folder.mkdir(parents=True)
        path=folder/'old-B001.json';outside=self.base/'outside.json';outside.write_text('{"private":"not returned"}');path.symlink_to(outside)
        server=self.server();state=json.loads(self.request(server,'GET','/api/shoot/backup')[1]);self.assertEqual(state['attempts'][0]['state'],'unreadable');self.assertNotIn('private',json.dumps(state))
        path.unlink();path.write_text(json.dumps({'primary_id':'annex','set_label':'B001','task_uuid':'old-task','state':'ccc_finished'}))
        self.assertTrue(json.loads(self.request(server,'GET','/api/shoot/backup')[1])['attempts'][0]['needs_history_review'])
    def test_pending_cutover_blocks_backup_action(self):
        self.registry['pending_cutover']={'target':'old'};(self.shared/'shoot.json').write_text(json.dumps(self.registry));server=self.server()
        code,data=self.request(server,'GET','/api/shoot');shoot=json.loads(data)
        headers={'Origin':'http://127.0.0.1:'+str(server.server_port),'X-Field-Lab-CSRF':shoot['csrf_token']}
        with patch('deploy.backup_shortcut.run_primary_task') as run:
            code,body=self.request(server,'POST','/api/shoot/backup',{'primary_id':'old','set_label':'B001'},headers)
            self.assertEqual(code,409);run.assert_not_called()
    def test_original_file_link_cannot_fall_through_to_active_primary(self):
        server=self.server();catalog=self.library.catalog();asset=next(a for a in catalog['assets'] if a['primary_id']=='old')
        self.assertEqual(asset['original_url'],'/primaries/old/media/clip.mp4')
        code,body=self.request(server,'GET',asset['original_url']);self.assertEqual(code,200);self.assertEqual(body,b'old media')
    def test_missing_new_preview_keeps_last_cache_and_other_volume_usable(self):
        self.library.refresh();old=self.library.cached('old')['catalog'];catalog=copy.deepcopy(old)
        catalog['assets'][0]['poster_url']='/notes/missing.jpg'
        build(self.project('old'),catalog,lambda action,values,query:{'vectors':[[.1]*384 for _ in values]},{'text':'text-v1','clip':'clip-v1','reranker':'rerank-v1'})
        self.library.last_refresh.clear();self.library.refresh()
        self.assertEqual(self.library.cached('old')['catalog'],old)
        self.assertEqual(len(self.library.catalog()['assets']),2)
        self.assertTrue(next(p for p in self.library.status()['profiles'] if p['id']=='old')['metadata_error'])
    def test_cached_image_modified_on_laptop_is_not_served(self):
        self.library.refresh();path=self.library.resolve_image('/primaries/old/notes/frame.jpg');path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):self.library.resolve_image('/primaries/old/notes/frame.jpg')
    def test_valid_volume_media_range_uses_original_project_only(self):
        server=self.server();code,body=self.request(server,'GET','/primaries/old/media/clip.mp4')
        self.assertEqual(code,200);self.assertEqual(body,b'old media')
        code,body=self.request(server,'GET','/primaries/annex/notes/frame.jpg');self.assertEqual(code,200);self.assertEqual(body,b'annex preview')

if __name__=='__main__':unittest.main()
