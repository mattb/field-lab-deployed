import contextlib,hashlib,json,tempfile,time,sqlite3,unittest,zipfile
from pathlib import Path
from deploy import premiere


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.home=self.base/'home';self.source=self.base/'source';self.source.mkdir()
        manifest={'id':premiere.PLUGIN_ID,'name':'Field Lab','manifestVersion':5,'version':'0.1.0','main':'index.html','host':{'app':'premierepro','minVersion':'25.6.0'},'requiredPermissions':{'localFileSystem':'request','network':{'domains':['http://127.0.0.1:8766']}}}
        for rel in premiere.FILES:
            p=self.source/rel;p.parent.mkdir(parents=True,exist_ok=True)
            if rel=='manifest.json':p.write_text(json.dumps(manifest))
            elif rel in ['main.js','native.js','commands.js']:
                body="'use strict';\nmodule.exports={};\n";name=p.stem;stamp=name+'-'+hashlib.sha256(body.encode()).hexdigest()[:12]
                p.write_text(body.replace("'use strict';\n","'use strict';\nconst BUILD_ID="+repr(stamp)+";\n"))
            else:p.write_text('fixture')
        self.shared=self.home/'shared';self.shared.mkdir(parents=True)
        (self.shared/'config.json').write_text('machine settings untouched')
        self.project=self.base/'footage';self.project.mkdir();(self.project/'original.mp4').write_bytes(b'original bytes')

    def staged(self,version='0.1.1'):
        return premiere.stage(self.home,version,self.source)

    def ccx(self,candidate,extra=None):
        path=self.base/(candidate['version']+'.ccx');plugin=Path(candidate['manifest']).parent
        with zipfile.ZipFile(path,'w') as z:
            for rel in premiere.FILES:z.write(plugin/rel,rel)
            if extra:z.writestr(extra,b'extra')
        return path

    def packaged(self,version='0.1.1'):
        c=self.staged(version);premiere.attach(self.home,c['candidate_id'],self.ccx(c));return c

    def runner(self,version,fail=False):
        self.calls=[]
        def run(command):
            self.calls.append(command)
            return {'returncode':1 if fail else 0,'stdout':('Enabled    Field Lab     '+version+'\n') if command[0]=='--list' else 'installed','stderr':''}
        return run

    def install(self,c,**kwargs):
        return premiere.install(self.home,c['candidate_id'],True,running=lambda:False,runner=self.runner(c['version']),**kwargs)

    def test_stage_reproducible_versions_separate_and_source_unmodified(self):
        a=self.staged();b=self.staged();self.assertEqual(a,b)
        self.assertNotEqual(a['candidate_id'],self.staged('0.1.2')['candidate_id'])
        self.assertEqual(json.loads((self.source/'manifest.json').read_text())['version'],'0.1.0')
        self.assertEqual(set(Path(a['manifest']).parent.iterdir()) & {self.source/'tests'},set())

    def test_interrupted_staging_does_not_publish_candidate(self):
        def fail(*args):raise OSError('power loss')
        with self.assertRaises(OSError):premiere.stage(self.home,'0.1.1',self.source,fault=fail)
        self.assertFalse((premiere.root(self.home)/'state.json').exists())
        self.assertTrue(self.staged()['candidate_id'])

    def test_stale_build_stamp_rejected(self):
        with (self.source/'native.js').open('a') as f:f.write('// changed')
        with self.assertRaisesRegex(ValueError,'Stale module'):self.staged()

    def test_symlink_source_and_unsealed_runtime_rejected(self):
        target=self.source/'native-values.js';target.unlink();target.symlink_to(self.source/'index.html')
        with self.assertRaises(ValueError):self.staged()
        target.unlink();target.write_text('fixture');c=self.staged();plugin=Path(c['manifest']).parent
        (plugin/'extra.js').write_text('unexpected')
        with self.assertRaisesRegex(ValueError,'unsealed'):premiere.verify_candidate(plugin.parent)

    def test_ccx_payload_tampering_and_path_traversal_rejected(self):
        c=self.staged();path=self.ccx(c,'../../escape')
        with self.assertRaisesRegex(ValueError,'Unsafe'):premiere.attach(self.home,c['candidate_id'],path)
        path=self.ccx(c,'private-data.txt')
        with self.assertRaisesRegex(ValueError,'Unsealed'):premiere.attach(self.home,c['candidate_id'],path)
        path=self.ccx(c)
        with zipfile.ZipFile(path,'a') as z:z.writestr('native.js','tampered')
        with self.assertRaises(ValueError):premiere.attach(self.home,c['candidate_id'],path)

    def test_attach_interruption_requires_reseal_before_install(self):
        c=self.staged()
        def fail(*args):raise OSError('power loss')
        with self.assertRaises(OSError):premiere.attach(self.home,c['candidate_id'],self.ccx(c),fault=fail)
        with self.assertRaises(OSError):premiere.verified_package(self.home,c['candidate_id'])
        premiere.attach(self.home,c['candidate_id'],self.ccx(c));self.assertTrue(premiere.verified_package(self.home,c['candidate_id']))

    def test_running_host_blocks_install_without_side_effect(self):
        c=self.packaged();calls=[]
        with self.assertRaisesRegex(ValueError,'quit Premiere'):
            premiere.install(self.home,c['candidate_id'],True,running=lambda:True,runner=lambda cmd:calls.append(cmd))
        self.assertFalse(calls);self.assertIsNone(premiere.read_state(premiere.root(self.home))['attempt'])

    def test_package_seal_checked_before_native_installer(self):
        c=self.packaged();r,ccx=premiere.verified_package(self.home,c['candidate_id']);ccx.write_bytes(b'changed')
        with self.assertRaises((ValueError,zipfile.BadZipFile)):self.install(c)

    def test_saved_before_ledger_ack_reconciles_without_reinstall(self):
        c=self.packaged()
        def fail(phase,state):
            if phase=='after_install':raise OSError('power loss after Adobe install')
        with self.assertRaises(OSError):self.install(c,fault=fail)
        state=premiere.read_state(premiere.root(self.home));self.assertIsNone(state['current']);self.assertTrue(state['attempt'])
        with self.assertRaisesRegex(ValueError,'unknown'):self.install(c)
        run=self.runner(c['version']);r=premiere.reconcile(self.home,runner=run)
        self.assertTrue(r['reconciled']);self.assertEqual([cmd[0] for cmd in self.calls],['--list'])
        self.assertFalse(r['state']['runtime_verified'])

    def test_before_install_crash_stays_unknown_and_does_not_guess_success(self):
        c=self.packaged()
        def fail(*args):raise OSError('power loss')
        with self.assertRaises(OSError):self.install(c,fault=fail)
        r=premiere.reconcile(self.home,runner=self.runner('0.1.0'));self.assertFalse(r['reconciled']);self.assertTrue(r['state']['attempt'])

    def test_update_preserves_previous_and_operator_data_no_downgrade(self):
        a=self.packaged();self.install(a);b=self.packaged('0.1.2');r=self.install(b)
        self.assertEqual(r['state']['previous'],a['candidate_id']);self.assertEqual(r['state']['current'],b['candidate_id'])
        with self.assertRaisesRegex(ValueError,'increased version'):self.install(a)
        self.assertEqual((self.shared/'config.json').read_text(),'machine settings untouched');self.assertEqual((self.project/'original.mp4').read_bytes(),b'original bytes')

    def test_nonzero_installer_and_missing_listing_remain_pending(self):
        c=self.packaged();r=premiere.install(self.home,c['candidate_id'],True,running=lambda:False,runner=self.runner(c['version'],fail=True))
        self.assertIsNone(r['state']['current']);self.assertTrue(r['state']['attempt'])

    def test_runtime_verification_requires_actual_build_version_saved_operation(self):
        c=self.packaged();self.install(c);r,_=premiere.verified_package(self.home,c['candidate_id']);receipt=self.project/'_DIT_NOTES/editorial/bridge/receipt.json';receipt.parent.mkdir(parents=True)
        packet={'protocol':1,'status':'saved','operation_id':'op-test','desired_hash':'desired','plugin_build':r['plugin_build'],'project_id':'p','native_project_path':'/test.prproj','readback':{'native_project_guid':'g'}}
        self.register_receipt(receipt,packet);self.assertTrue(premiere.verify_runtime(self.home,c['candidate_id'],receipt,'op-test')['state']['runtime_verified'])
        packet['plugin_build']['native']='old';self.register_receipt(receipt,packet)
        with self.assertRaisesRegex(ValueError,'does not match'):premiere.verify_runtime(self.home,c['candidate_id'],receipt,'op-test')
        packet['plugin_build']=r['plugin_build'];packet['status']='applied';self.register_receipt(receipt,packet)
        with self.assertRaisesRegex(ValueError,'fresh requested'):premiere.verify_runtime(self.home,c['candidate_id'],receipt,'op-test')

    def test_runtime_replay_changes_only_envelope_id(self):
        c=self.packaged();self.install(c);r,_=premiere.verified_package(self.home,c['candidate_id']);path=self.project/'_DIT_NOTES/editorial/bridge/receipt.json';path.parent.mkdir(parents=True)
        packet={'protocol':1,'status':'saved','receipt_id':'first','operation_id':'replay-op','desired_hash':'desired','plugin_build':r['plugin_build'],'project_id':'p','native_project_path':'/test.prproj','readback':{'native_project_guid':'g'}}
        self.register_receipt(path,packet);packet['receipt_id']='replayed';path.write_text(json.dumps(packet))
        self.assertTrue(premiere.verify_runtime(self.home,c['candidate_id'],path,'replay-op')['state']['runtime_verified'])
        packet['readback']['native_project_guid']='different-project';path.write_text(json.dumps(packet))
        with self.assertRaisesRegex(ValueError,'differs from durable'):premiere.verify_runtime(self.home,c['candidate_id'],path,'replay-op')

    def test_adobe_manifest_whitespace_normalization_but_no_semantic_changes(self):
        c=self.staged();plugin=Path(c['manifest']).parent;path=self.base/'normalized.ccx'
        def write(changed=False):
            with zipfile.ZipFile(path,'w') as archive:
                for rel in premiere.FILES:
                    if rel=='manifest.json':
                        value=json.loads((plugin/rel).read_text())
                        if changed:value['author']='unreviewed change'
                        archive.writestr(rel,json.dumps(value,indent=2))
                    else:archive.write(plugin/rel,rel)
        write();premiere.attach(self.home,c['candidate_id'],path)
        write(True)
        with self.assertRaisesRegex(ValueError,'manifest differs'):premiere.attach(self.home,c['candidate_id'],path)

    def test_sdk_packaging_failure_preserves_candidate_and_current_state(self):
        c=self.staged()
        def fail(*args,**kwargs):raise OSError('offline cache unavailable')
        with self.assertRaises(OSError):premiere.package_adobe(self.home,c['candidate_id'],offline=True,node='/fake/node',npm='/fake/npm',runner=fail)
        self.assertEqual(premiere.verify_candidate(Path(c['manifest']).parent.parent)['candidate_id'],c['candidate_id'])
        self.assertIsNone(premiere.read_state(premiere.root(self.home))['current'])
        self.assertEqual((self.shared/'config.json').read_text(),'machine settings untouched')

    def register_receipt(self,path,packet,created=None):
        path.write_text(json.dumps(packet));dbpath=path.parents[2]/'project.sqlite3'
        with contextlib.closing(sqlite3.connect(dbpath)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_config (id INTEGER PRIMARY KEY,value TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_operations (operation_id TEXT PRIMARY KEY,desired_hash TEXT,status TEXT,receipt TEXT,created_at REAL)')
            db.execute('INSERT OR REPLACE INTO premiere_bridge_config VALUES(1,?)',(json.dumps({'project_id':packet['project_id'],'native_project_path':packet['native_project_path']}),))
            db.execute('INSERT OR REPLACE INTO premiere_bridge_operations VALUES(?,?,?,?,?)',(packet['operation_id'],packet['desired_hash'],'saved',json.dumps(packet),time.time() if created is None else created))
            db.commit()

    def test_permissions_and_registration_drift_fail_closed(self):
        manifest=json.loads((self.source/'manifest.json').read_text());manifest['requiredPermissions']['clipboard']='readAndWrite';(self.source/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'additional plugin permissions'):self.staged()
        del manifest['requiredPermissions']['clipboard'];(self.source/'manifest.json').write_text(json.dumps(manifest));c=self.packaged();self.install(c)
        base=premiere.root(self.home);state=premiere.read_state(base);state['runtime_verified']={'old':True};premiere.manager.atomic_json(base/'state.json',state)
        result=premiere.install(self.home,c['candidate_id'],True,running=lambda:False,runner=self.runner('0.1.0'))
        self.assertFalse(result['state']['runtime_verified']);self.assertIn('registration_drift',result['state'])

    def test_fresh_copy_of_old_receipt_does_not_verify_installation(self):
        c=self.packaged();self.install(c);r,_=premiere.verified_package(self.home,c['candidate_id']);path=self.project/'_DIT_NOTES/editorial/bridge/receipt.json';path.parent.mkdir(parents=True)
        packet={'protocol':1,'status':'saved','operation_id':'old-op','desired_hash':'desired','plugin_build':r['plugin_build'],'project_id':'p','native_project_path':'/test.prproj','readback':{'native_project_guid':'g'}}
        self.register_receipt(path,packet,created=1)
        with self.assertRaisesRegex(ValueError,'fresh operation'):premiere.verify_runtime(self.home,c['candidate_id'],path,'old-op')

    def test_archive_duplicate_and_overlarge_rejected(self):
        c=self.staged();path=self.ccx(c)
        with zipfile.ZipFile(path,'a') as z:z.writestr('manifest.json','{}')
        with self.assertRaisesRegex(ValueError,'duplicate'):premiere.attach(self.home,c['candidate_id'],path)


if __name__=='__main__':unittest.main()
