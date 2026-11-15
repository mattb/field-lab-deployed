"""Shoot ownership and cutover recovery with local folders and explicit fake identities."""
import copy
import json
from pathlib import Path
import plistlib
import tempfile
import time
import unittest

from ditnotes.shoot_registry import CUTOVER_STAGES,ShootRegistry

TASK_A='11111111-1111-4111-8111-111111111111'
TASK_B='22222222-2222-4222-8222-222222222222'

class Fixture:
    def __init__(self,base):
        self.base=base;self.available={};self.uuid={}
        for name in ('old','annex','backup_old','backup_annex'):
            path=base/name;path.mkdir();self.available[str(path)]=True;self.uuid[str(path)]=name.upper()+'-UUID'
            if name in ('old','annex'):(path/'Ingests').mkdir()
    def probe(self,path):
        path=Path(path)
        for mount,value in self.uuid.items():
            if path==Path(mount) or path.is_relative_to(mount):
                if not self.available[mount]:raise OSError('Disconnected fixture')
                return {'uuid':value,'mount':mount,'physical':['disk:'+value],'filesystem':'apfs'}
        raise OSError('Unknown fixture path')

class PrimaryRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.home=self.base/'app';self.home.mkdir();(self.home/'shared').mkdir()
        self.fixture=Fixture(self.base)
        self.config={'volume_uuid':'OLD-UUID','volume_mount':str(self.base/'old'),'ingest_folder':'Ingests','ingest_root':str(self.base/'old/Ingests'),'port':8766,'features':{'keep':'operator setting'},'custom_field':{'preserve':True}}
        (self.home/'shared/config.json').write_text(json.dumps(self.config))
        self.registry=ShootRegistry(self.home,probe=self.fixture.probe)
        self.first=self.registry.initialize(self.config,'Test Shoot')['active_primary']
        self.second=self.registry.register_primary(self.base/'annex',label='ThunderBlade Annex')['id']
    def record(self,primary,folder,task):
        source=self.registry.read()['profiles'][primary]['project_root'];destination=str(self.base/folder/'Shoot')
        return {'task_uuid':task,'source':source,'destination_mount':str(self.base/folder),'destination_uuid':self.fixture.uuid[str(self.base/folder)],'destination_relative':'Shoot','operator_notes':'retain','configuration_evidence':{'method':'supported-ui','observed_at':time.time(),'task_uuid':task,'source':source,'destination':destination}}
    def backup(self,primary,folder,task):
        value=self.registry.register_backup(primary,'B001',self.record(primary,folder,task),reviewed=True)
        self.registry.set_current_backup(primary,'B001');return value
    def ready(self):
        self.backup(self.first,'backup_old',TASK_A);self.backup(self.second,'backup_annex',TASK_B)
        for stage in CUTOVER_STAGES[:-1]:
            evidence={'primary_id':self.second,'kind':'readback','observed_at':time.time(),'fixture':'observed in local test'}
            if stage=='backup_bound':evidence.update(task_uuid=TASK_B,set_label='B001')
            self.registry.record_cutover(stage,evidence)
        self.registry.record_cutover('accepted',self.acceptance())
        config=self.registry.target_config();(self.home/'shared/config.json').write_text(json.dumps(config))
        pointer=self.home/'shared/runtime/backup-shortcut/current.plist';pointer.parent.mkdir(parents=True,exist_ok=True)
        backup={'task_uuid':TASK_B,'mode':'start','primary_id':self.second,
                'source_mount':str(self.base/'annex'),'source_uuid':'ANNEX-UUID',
                'destination_mount':str(self.base/'backup_annex'),'destination_uuid':'BACKUP_ANNEX-UUID'}
        pointer.write_bytes(plistlib.dumps(backup))
        return config,backup
    def acceptance(self):
        return {'primary_id':self.second,'kind':'acceptance','observed_at':time.time(),
                'volume_uuid':'ANNEX-UUID','task_uuid':TASK_B,'method':'operator-observed-acceptance',
                'artifacts':['disposable fixture ingest/restore observations'],
                'offshoot_destination_observed':True,'verified_ingest':True,'ai_index_checked':True,
                'backup_copy_verified':True,'restore_checked':True}
    def test_initialize_replay_and_registration_do_not_switch_active(self):
        before=self.registry.read();again=self.registry.initialize(self.config)
        self.assertEqual(before,again);self.assertEqual(before['active_primary'],self.first)
        self.assertEqual(self.registry.register_primary(self.base/'annex')['id'],self.second)
        self.assertEqual(len(self.registry.read()['profiles']),2)
    def test_profile_backup_sets_have_independent_label_scopes(self):
        self.backup(self.first,'backup_old',TASK_A);self.backup(self.second,'backup_annex',TASK_B)
        state=self.registry.read();self.assertEqual(state['profiles'][self.first]['backup']['sets']['B001']['task_uuid'],TASK_A)
        self.assertEqual(state['profiles'][self.second]['backup']['sets']['B001']['task_uuid'],TASK_B)
        self.assertNotEqual(self.registry.ledger_path(self.first),self.registry.ledger_path(self.second))
    def test_reusing_a_ccc_task_in_other_primary_is_refused(self):
        self.backup(self.first,'backup_old',TASK_A)
        with self.assertRaisesRegex(ValueError,'another primary'):self.backup(self.second,'backup_annex',TASK_A)
    def test_same_named_wrong_uuid_cannot_register_or_finish(self):
        self.registry.begin_cutover(self.second)
        config,backup=self.ready();self.fixture.uuid[str(self.base/'annex')]='WRONG-UUID'
        with self.assertRaisesRegex(ValueError,'UUID'):self.registry.complete_cutover(config,backup)
        self.assertEqual(self.registry.read()['active_primary'],self.first)
    def test_begin_requires_actual_source_configuration_and_one_pending_target(self):
        wrong={**self.config,'ingest_root':'/not/the/active/project'}
        with self.assertRaises(ValueError):self.registry.begin_cutover(self.second,wrong)
        first=self.registry.begin_cutover(self.second)
        self.assertEqual(self.registry.begin_cutover(self.second),first)
        third=self.registry.register_primary(self.base/'backup_old',ingest_folder='Another')['id']
        with self.assertRaisesRegex(ValueError,'pending'):self.registry.begin_cutover(third)
    def test_cutover_preserves_unknown_config_fields_and_old_backup_records(self):
        self.registry.begin_cutover(self.second);config,backup=self.ready()
        old=self.registry.read()['profiles'][self.first]
        state=self.registry.complete_cutover(config,backup)
        self.assertEqual(state['active_primary'],self.second);self.assertEqual(state['profiles'][self.first],old)
        self.assertEqual(config['features'],self.config['features']);self.assertEqual(config['custom_field'],self.config['custom_field'])
        self.assertIsNone(state['pending_cutover']);self.assertEqual(len(state['history']),1)
        self.assertEqual(self.registry.complete_cutover(config,backup),state)
    def test_disconnect_old_primary_then_reconnect_does_not_switch_ingest(self):
        self.registry.begin_cutover(self.second);config,backup=self.ready();self.registry.complete_cutover(config,backup)
        self.fixture.available[str(self.base/'old')]=False
        self.assertFalse(self.registry.availability(self.first)['online'])
        self.fixture.available[str(self.base/'old')]=True
        self.assertTrue(self.registry.availability(self.first)['online'])
        self.registry.refresh_mount(self.first,self.base/'old')
        self.assertEqual(self.registry.read()['active_primary'],self.second)
        self.assertEqual(json.loads((self.home/'shared/config.json').read_text()),config)
    def test_wrong_backup_and_observed_flags_cannot_complete(self):
        self.registry.begin_cutover(self.second);config,backup=self.ready()
        (self.home/'shared/config.json').write_text(json.dumps(self.config))
        with self.assertRaisesRegex(ValueError,'Actual Field Lab'):self.registry.complete_cutover(config,backup)
        (self.home/'shared/config.json').write_text(json.dumps(config));wrong={'task_uuid':TASK_A,'mode':'start'}
        with self.assertRaisesRegex(ValueError,'Backup binding'):self.registry.complete_cutover(config,wrong)
    def test_interrupted_registry_commit_remains_readable_and_resumable(self):
        def crash(phase):
            if phase=='cutover_started':raise OSError('power loss after commit')
        self.registry.checkpoint=crash
        with self.assertRaises(OSError):self.registry.begin_cutover(self.second)
        resumed=ShootRegistry(self.home,probe=self.fixture.probe)
        self.assertEqual(resumed.read()['active_primary'],self.first)
        self.assertEqual(resumed.begin_cutover(self.second)['to_primary'],self.second)
        self.assertEqual(self.registry.path.stat().st_mode&0o777,0o600)
    def test_out_of_order_or_boolean_only_journal_evidence_is_refused(self):
        self.registry.begin_cutover(self.second)
        with self.assertRaises(ValueError):self.registry.record_cutover('accepted',{'accepted':True})
        with self.assertRaises(ValueError):self.registry.record_cutover('accepted',{'primary_id':self.second,'observed_at':time.time(),'kind':'acceptance'})
    def test_cancel_requires_fresh_external_before_state_and_never_erases_history(self):
        self.registry.begin_cutover(self.second)
        with self.assertRaisesRegex(ValueError,'Fresh OffShoot'):self.registry.cancel_cutover(self.config)
        self.registry.cancel_cutover(self.config,{'kind':'readback','observed_at':time.time(),'offshoot_project_root':self.config['ingest_root'],'backup_task_uuid':None})
        state=self.registry.read();self.assertIsNone(state['pending_cutover']);self.assertEqual(state['active_primary'],self.first);self.assertIn('cancelled_at',state['history'][0])
    def test_missing_mount_never_recreated(self):
        missing=self.base/'Missing'
        with self.assertRaises(ValueError):self.registry.register_primary(missing)
        self.assertFalse(missing.exists())
    def test_backup_source_must_be_original_root_with_matching_ui_readback(self):
        record=self.record(self.second,'backup_annex',TASK_B);record['source']=str(self.base/'wrong')
        with self.assertRaisesRegex(ValueError,'original'):self.registry.register_backup(self.second,'B001',record,reviewed=True)
        record=self.record(self.second,'backup_annex',TASK_B);record['configuration_evidence']['destination']='wrong'
        with self.assertRaisesRegex(ValueError,'evidence'):self.registry.register_backup(self.second,'B001',record,reviewed=True)
    def test_same_uuid_mount_refresh_records_only_known_old_root_and_rechecks_pairing(self):
        self.backup(self.second,'backup_annex',TASK_B)
        old=self.base/'annex';new=self.base/'Renamed Primary';old.rename(new)
        self.fixture.uuid[str(new)]=self.fixture.uuid.pop(str(old))
        self.fixture.available[str(new)]=self.fixture.available.pop(str(old))
        refreshed=self.registry.refresh_mount(self.second,new)
        self.assertEqual(refreshed['previous_roots'],[str(old/'Ingests')])
        self.assertTrue(refreshed['backup']['sets']['B001']['needs_review'])
        with self.assertRaisesRegex(ValueError,'review'):self.registry.set_current_backup(self.second,'B001')
        record=self.record(self.second,'backup_annex',TASK_B)
        self.registry.register_backup(self.second,'B001',record,reviewed=True)
        self.assertEqual(self.registry.set_current_backup(self.second,'B001')['source'],str(new/'Ingests'))
        self.assertEqual(self.registry.read()['active_primary'],self.first)

    def test_active_mount_refresh_refuses_uncoordinated_config_change(self):
        before=self.registry.read();old=self.base/'old';new=self.base/'Renamed Active';old.rename(new)
        self.fixture.uuid[str(new)]=self.fixture.uuid.pop(str(old))
        self.fixture.available[str(new)]=self.fixture.available.pop(str(old))
        with self.assertRaisesRegex(ValueError,'coordinated'):
            self.registry.refresh_mount(self.first,new)
        self.assertEqual(self.registry.read(),before)
        self.assertEqual(json.loads((self.home/'shared/config.json').read_text()),self.config)

    def test_direct_journal_acceptance_requires_full_fresh_target_evidence(self):
        self.registry.begin_cutover(self.second);self.ready()
        state=self.registry.read();del state['pending_cutover']['steps']['accepted']
        self.registry.path.write_text(json.dumps(state))
        invalid=[{'verified_ingest':False},{'restore_checked':1},{'volume_uuid':'OLD-UUID'},
                 {'task_uuid':TASK_A},{'method':'readback'},{'artifacts':[]},{'artifacts':[None]},
                 {'observed_at':float('nan')},{'observed_at':float('inf')},
                 {'observed_at':True},{'observed_at':0},{'observed_at':time.time()+3600}]
        invalid.append({'observed_at':state['pending_cutover']['created_at']})
        for changes in invalid:
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'acceptance evidence'):
                self.registry.record_cutover('accepted',{**self.acceptance(),**changes})
        self.registry.record_cutover('accepted',self.acceptance())

    def test_accepted_target_backups_and_journal_are_frozen(self):
        self.backup(self.second,'backup_annex',TASK_B)
        task='33333333-3333-4333-8333-333333333333'
        record=self.record(self.second,'backup_annex',task)
        record['destination_relative']='Shoot2';record['configuration_evidence']['destination']=str(self.base/'backup_annex/Shoot2')
        self.registry.register_backup(self.second,'B002',record,reviewed=True)
        self.registry.begin_cutover(self.second);self.ready()
        before=self.registry.read()
        with self.assertRaisesRegex(ValueError,'freezes'):self.registry.set_current_backup(self.second,'B002')
        with self.assertRaisesRegex(ValueError,'freezes'):self.registry.register_backup(self.second,'B002',record,reviewed=True)
        with self.assertRaisesRegex(ValueError,'frozen'):self.registry.record_cutover('backup_bound',{'primary_id':self.second,'kind':'readback','observed_at':time.time(),'task_uuid':task,'set_label':'B002'})
        accepted=before['pending_cutover']['steps']['accepted']
        self.assertEqual(self.registry.record_cutover('accepted',accepted),before['pending_cutover'])
        self.assertEqual(self.registry.read(),before)

    def test_complete_revalidates_acceptance_and_bound_task(self):
        self.registry.begin_cutover(self.second);config,backup=self.ready()
        original=self.registry.read()
        mutations=[('accepted','task_uuid',TASK_A),('accepted','restore_checked',False),
                   ('backup_bound','task_uuid',TASK_A),('backup_bound','set_label','B002')]
        for stage,key,value in mutations:
            state=copy.deepcopy(original);state['pending_cutover']['steps'][stage][key]=value
            self.registry.path.write_text(json.dumps(state))
            with self.subTest(stage=stage,key=key),self.assertRaisesRegex(ValueError,'acceptance evidence'):
                self.registry.complete_cutover(config,backup)
            self.assertEqual(self.registry.read()['active_primary'],self.first)
        self.registry.path.write_text(json.dumps(original))
        self.assertEqual(self.registry.complete_cutover(config,backup)['active_primary'],self.second)

    def test_complete_revalidates_both_observed_and_actual_pointer_identity(self):
        self.registry.begin_cutover(self.second);config,backup=self.ready()
        path=self.home/'shared/runtime/backup-shortcut/current.plist'
        for field in ('primary_id','source_mount','source_uuid','destination_mount','destination_uuid'):
            with self.subTest(field=field):
                wrong={**backup,field:'wrong'}
                with self.assertRaisesRegex(ValueError,'Backup binding'):
                    self.registry.complete_cutover(config,wrong)
                path.write_bytes(plistlib.dumps(wrong))
                with self.assertRaisesRegex(ValueError,'Backup binding'):
                    self.registry.complete_cutover(config,backup)
                path.write_bytes(plistlib.dumps(backup))
        self.assertEqual(self.registry.read()['active_primary'],self.first)
        self.registry.complete_cutover(config,backup)

    def test_unknown_state_fields_are_preserved(self):
        state=self.registry.read();state['operator_extension']={'do':'keep'};self.registry.path.write_text(json.dumps(state))
        self.registry.begin_cutover(self.second)
        self.assertEqual(self.registry.read()['operator_extension'],{'do':'keep'})

if __name__=='__main__':unittest.main()
