"""Ownership and queue routing across disconnected disposable primary volumes."""
import copy
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

from ditnotes.primary_routing import (CutoverPending,OfflinePrimary,UnknownPrimary,ensure_not_cutting_over,event_for_payload,for_event,for_payload,profile_configs)
from ditnotes.shoot_registry import ShootRegistry

with mock.patch.dict(os.environ):
    ingest_service=importlib.import_module('ingest_service')

class PrimaryRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name).resolve()
        self.home=self.base/'app';self.home.mkdir();self.mounts={};self.online={}
        for name in ('old','annex'):
            mount=self.base/name;(mount/'Ingests/Card001').mkdir(parents=True)
            self.mounts[str(mount)]=name.upper()+'-UUID';self.online[str(mount)]=True
        self.registry=ShootRegistry(self.home,probe=self.registry_probe);self.root=self.home/'shared';(self.root/'runtime').mkdir()
        self.config={'volume_uuid':'OLD-UUID','volume_mount':str(self.base/'old'),'ingest_folder':'Ingests','ingest_root':str(self.base/'old/Ingests'),'queue_db':str(self.root/'runtime/queue.sqlite3'),'port':8766}
        (self.root/'config.json').write_text(json.dumps(self.config));self.old=self.registry.initialize(self.config)['active_primary'];self.target=self.registry.register_primary(self.base/'annex')['id']
        self.addCleanup(mock.patch.stopall)
    def registry_probe(self,path):
        mount=str(path)
        if not self.online.get(mount):raise OSError('fixture offline')
        return {'uuid':self.mounts[mount],'mount':mount,'physical':[self.mounts[mount]]}
    def probe(self,path):
        path=Path(path)
        for mount,identity in self.mounts.items():
            if path==Path(mount) or path.is_relative_to(mount):
                if not self.online[mount]:raise OSError('fixture offline')
                return {'VolumeUUID':identity,'MountPoint':mount}
        raise OSError('unknown fake volume')
    def profile_config(self,pid):
        from ditnotes.primary_routing import config_for_profile
        return config_for_profile(self.config,self.registry.read()['profiles'][pid])
    def payload(self,pid):
        config=self.profile_config(pid);destination=str(Path(config['ingest_root'])/'Card001')
        return {'volume_uuid':config['volume_uuid'],'ingest_root':config['ingest_root'],'destination':destination,'event':{'FileCopyCompleted_destinationPath':destination}}
    def model_active_annex(self):
        # Routing-only fixture: modeled completed registry, not external app acceptance.
        state=self.registry.read();state['active_primary']=self.target;state['pending_cutover']=None;self.registry.path.write_text(json.dumps(state))
        self.config=self.profile_config(self.target);(self.root/'config.json').write_text(json.dumps(self.config))
    def pending_validation(self):
        self.registry.begin_cutover(self.target,self.config)
        # Isolate routing's readiness contract; coordinator tests exercise actual stages.
        state=self.registry.read();state['pending_cutover']['steps']['backup_bound']={'fixture':True};self.registry.path.write_text(json.dumps(state))
        self.config=self.registry.target_config();self.config['queue_db']=str(self.root/'runtime/queue.sqlite3');self.config['port']=8766
        intent=state['pending_cutover']['id'];ensure_not_cutting_over(self.root,self.config,intent);return intent
    def test_legacy_event_keeps_original_validation_path(self):
        event={'legacy':'retained for validate_event'}
        self.assertEqual(event_for_payload({'event':event},{'ingest_root':'/legacy/project'}),event)

    def test_old_job_keeps_original_owner_after_new_primary_becomes_active(self):
        original=self.payload(self.old);self.model_active_annex();before=self.registry.read()
        owner=for_payload(self.root,self.config,original,probe=self.probe)
        self.assertEqual(owner['primary_id'],self.old);self.assertEqual(owner['ingest_root'],original['ingest_root']);self.assertEqual(event_for_payload(original,owner),original['event']);self.assertEqual(self.registry.read(),before)
    def test_offline_old_job_is_deferred_and_same_label_wrong_uuid_is_rejected(self):
        payload=self.payload(self.old);self.model_active_annex();mount=str(self.base/'old');self.online[mount]=False
        with self.assertRaises(OfflinePrimary):for_payload(self.root,self.config,payload,probe=self.probe)
        self.online[mount]=True;self.mounts[mount]='WRONG-UUID'
        with self.assertRaises(OfflinePrimary):for_payload(self.root,self.config,payload,probe=self.probe)
        self.assertEqual(self.registry.read()['active_primary'],self.target)
    def test_reconnect_alias_rebases_only_known_owner_without_changing_default(self):
        payload=self.payload(self.old);self.model_active_annex();old=self.base/'old';new=self.base/'old 1';old.rename(new)
        self.mounts[str(new)]=self.mounts.pop(str(old));self.online[str(new)]=self.online.pop(str(old));self.registry.refresh_mount(self.old,new)
        owner=for_payload(self.root,self.config,payload,probe=self.probe);event=event_for_payload(payload,owner)
        self.assertEqual(owner['primary_id'],self.old);self.assertEqual(event['FileCopyCompleted_destinationPath'],str(new/'Ingests/Card001'));self.assertEqual(self.registry.read()['active_primary'],self.target)
        forged=copy.deepcopy(payload);forged['volume_uuid']='ANNEX-UUID'
        with self.assertRaises(UnknownPrimary):for_payload(self.root,self.config,forged,probe=self.probe)
    def test_raw_event_at_reused_old_mount_cannot_borrow_registered_alias_owner(self):
        old_event=self.payload(self.old)['event'];self.model_active_annex();old=self.base/'old';new=self.base/'old 1';old.rename(new)
        self.mounts[str(new)]=self.mounts.pop(str(old));self.online[str(new)]=self.online.pop(str(old));self.registry.refresh_mount(self.old,new)
        (old/'Ingests/Card001').mkdir(parents=True);self.mounts[str(old)]='UNRELATED-UUID';self.online[str(old)]=True
        with self.assertRaises(UnknownPrimary):for_event(self.root,self.config,old_event,probe=self.probe)
        self.assertEqual(self.registry.read()['active_primary'],self.target)
    def test_explicit_missing_registry_never_falls_back_to_unscoped_legacy(self):
        config={**self.config,'shoot_registry':str(self.root/'missing-shoot.json')}
        with self.assertRaises(ValueError):for_payload(self.root,config,self.payload(self.old),probe=self.probe)
    def test_dangling_registry_symlink_is_rejected(self):
        self.registry.path.rename(self.root/'preserved-shoot.json');self.registry.path.symlink_to(self.root/'missing-target.json')
        with self.assertRaises(ValueError):ensure_not_cutting_over(self.root,self.config)
    def test_raw_event_historical_alias_cannot_traverse_replacement_symlink(self):
        old_event=self.payload(self.old)['event'];self.model_active_annex();old=self.base/'old';new=self.base/'old 1';old.rename(new)
        self.mounts[str(new)]=self.mounts.pop(str(old));self.online[str(new)]=self.online.pop(str(old));self.registry.refresh_mount(self.old,new);old.symlink_to(new,target_is_directory=True)
        with self.assertRaises(UnknownPrimary):for_event(self.root,self.config,old_event,probe=self.probe)
        self.assertEqual(json.loads(self.registry.path.read_text())['active_primary'],self.target)
    def test_payload_embedded_event_cannot_change_recorded_ingest_destination(self):
        payload=self.payload(self.old);owner=self.profile_config(self.old)
        for changed_root in (False,True):
            with self.subTest(rebased=changed_root):
                config=copy.deepcopy(owner)
                if changed_root:config['ingest_root']=str(self.base/'renamed/Ingests')
                forged=copy.deepcopy(payload);forged['event']['FileCopyCompleted_destinationPath']=str(self.base/'old/Ingests/OtherCard')
                with self.assertRaises(UnknownPrimary):event_for_payload(forged,config)
    def test_arbitrary_same_suffix_root_and_traversal_cannot_spoof_ownership(self):
        for bad_root in (str(self.base/'unregistered/Ingests'),str(self.base/'old/Ingests/../Ingests')):
            with self.subTest(root=bad_root):
                payload=self.payload(self.old);payload['ingest_root']=bad_root;payload['destination']=bad_root+'/Card001'
                with self.assertRaises(UnknownPrimary):for_payload(self.root,self.config,payload,probe=self.probe)
        payload=self.payload(self.old);payload['destination']=payload['ingest_root']
        with self.assertRaises(UnknownPrimary):for_payload(self.root,self.config,payload,probe=self.probe)
    def test_pending_normal_start_and_wrong_intent_are_blocked_before_launch(self):
        intent=self.pending_validation();config={k:v for k,v in self.config.items() if k!='_cutover_validation'}
        for supplied in (None,'another-intent'):
            with self.subTest(intent=supplied),mock.patch.object(ingest_service,'ROOT',self.root),mock.patch.object(ingest_service,'config',return_value=copy.deepcopy(config)),mock.patch.object(ingest_service,'open_progress') as progress,mock.patch.object(ingest_service.subprocess,'Popen') as launch:
                with self.assertRaises(CutoverPending):ingest_service.start(supplied)
                launch.assert_not_called();progress.assert_not_called()
        allowed=copy.deepcopy(config);ensure_not_cutting_over(self.root,allowed,intent)
        self.assertEqual(allowed['_cutover_validation'],intent);self.assertEqual(self.registry.read()['active_primary'],self.old)
        self.assertEqual([identity for identity,_ in profile_configs(self.root,allowed)],[self.target])
    def test_explicit_validation_defers_old_queue_and_routes_only_target(self):
        old_payload=self.payload(self.old);new_payload=self.payload(self.target);self.pending_validation()
        with self.assertRaises(OfflinePrimary):for_payload(self.root,self.config,old_payload,probe=self.probe)
        self.assertEqual(for_payload(self.root,self.config,new_payload,probe=self.probe)['primary_id'],self.target)
        normal={k:v for k,v in self.config.items() if k!='_cutover_validation'}
        with self.assertRaises(CutoverPending):for_payload(self.root,normal,new_payload,probe=self.probe)
    def test_explicit_validation_does_not_route_old_raw_event(self):
        old_event=self.payload(self.old)['event'];self.pending_validation()
        with self.assertRaises(OfflinePrimary):for_event(self.root,self.config,old_event,probe=self.probe)
    def test_pending_ingest_callback_is_retained_without_queue_or_volume_access(self):
        event=self.payload(self.old)['event'];self.registry.begin_cutover(self.target,self.config)
        with mock.patch.object(ingest_service,'ROOT',self.root),mock.patch.object(ingest_service,'config',return_value=copy.deepcopy(self.config)),mock.patch.object(ingest_service,'verify_volume') as verify,mock.patch.object(ingest_service,'enqueue') as enqueue:
            result=ingest_service.ingest_event(event)
        self.assertEqual(result['status'],'deferred');verify.assert_not_called();enqueue.assert_not_called()
        retained=list((self.root/'runtime/events').glob('*.json'));self.assertEqual(len(retained),1);self.assertEqual(json.loads(retained[0].read_text()),event);self.assertEqual(self.registry.read()['active_primary'],self.old)
    def controller(self):
        # sqlite context managers commit but do not close; release test handles explicitly.
        real_connect=ingest_service.connect
        def tracked_connect(config):
            db=real_connect(config);self.addCleanup(db.close);return db
        mock.patch.object(ingest_service,'connect',side_effect=tracked_connect).start()
        mock.patch('ditnotes.sqlite_safety.require_safe_sqlite',return_value={'ready':True}).start()
        mock.patch.object(ingest_service,'ROOT',self.root).start();mock.patch.object(ingest_service,'mac_volume_info',side_effect=self.probe).start()
        return ingest_service.Controller(self.config,object(),threading.Event())
    def enqueue(self,identity,payload,when):
        with ingest_service.connect(self.config) as db:db.execute('INSERT INTO jobs(job_id,payload,enqueued_at) VALUES(?,?,?)',(identity,json.dumps(payload),when))
    def queue_states(self):
        with ingest_service.connect(self.config) as db:return {row['job_id']:(row['state'],row['attempts']) for row in db.execute('SELECT * FROM jobs')}
    def test_queue_skips_offline_old_and_claims_annex_without_losing_backlog(self):
        old_payload=self.payload(self.old);new_payload=self.payload(self.target);self.model_active_annex();controller=self.controller()
        self.enqueue('old-job',old_payload,1);self.enqueue('annex-job',new_payload,2);self.online[str(self.base/'old')]=False
        row,owner=controller.claim_pending();self.assertEqual(row['job_id'],'annex-job');self.assertEqual(owner['primary_id'],self.target);self.assertEqual(self.queue_states(),{'old-job':('pending',0),'annex-job':('processing',1)})
        self.online[str(self.base/'old')]=True;row,owner=controller.claim_pending();self.assertEqual(row['job_id'],'old-job');self.assertEqual(owner['primary_id'],self.old);self.assertEqual(self.registry.read()['active_primary'],self.target)
    def test_validation_queue_retains_old_jobs_and_rejects_unknown_uuid(self):
        old_payload=self.payload(self.old);new_payload=self.payload(self.target);forged=copy.deepcopy(new_payload);forged['volume_uuid']='UNKNOWN'
        self.pending_validation();controller=self.controller();self.enqueue('old-job',old_payload,1);self.enqueue('unknown-job',forged,2);self.enqueue('target-job',new_payload,3)
        row,owner=controller.claim_pending();self.assertEqual(row['job_id'],'target-job');self.assertEqual(owner['primary_id'],self.target);self.assertEqual(self.queue_states(),{'old-job':('pending',0),'unknown-job':('error',0),'target-job':('processing',1)});self.assertEqual(controller.deferred_primary_jobs,1);self.assertEqual(self.registry.read()['active_primary'],self.old)

if __name__=='__main__':unittest.main()
