"""Date-filter proposals operate on disposable folder trees, never CCC settings."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'.agents/skills/field-lab-backup/scripts/plan_rotation.py'
spec=importlib.util.spec_from_file_location('date_filter_plan',SCRIPT)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class DateFilterPlans(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name).resolve();self.project=self.base/'Shoot';self.project.mkdir()
        for day in ['20261115_D01','20261116_D02','20261117_D03']:
            card=self.project/day/'CAM_A/A001';card.mkdir(parents=True);(card/'clip.mp4').write_bytes(b'own synthetic fixture')
        self.ledger={'schema':1,'project':str(self.project),'closed_sets':[{'label':'B001','dates':['20261115_D01']}],'current_label':'B002'}
    def plan(self,ledger=None,label='B003'):
        return module.plan(self.project,self.ledger if ledger is None else ledger,label)
    def test_closing_assignment_and_current_complement_use_all_prior_sets(self):
        result=self.plan()
        self.assertEqual(result['previous'][1]['folders'],['20261116_D02','20261117_D03'])
        self.assertEqual(result['current']['folders'],['20261115_D01','20261116_D02','20261117_D03'])
        self.assertEqual(result['current']['mode'],'Exclude');self.assertEqual(result['previous'][0]['mode'],'Include')
        self.assertFalse(result['applied']);self.assertFalse(result['backup_verified'])
    def test_next_rotation_keeps_all_old_assignments(self):
        first=self.plan();(self.project/'20261118_D04').mkdir()
        result=self.plan(first['proposed_ledger'],'B004')
        self.assertEqual(result['previous'][2]['folders'],['20261118_D04'])
        self.assertEqual(len(result['current']['folders']),4)
    def test_late_file_in_closed_day_does_not_reassign_it(self):
        (self.project/'20261115_D01'/'late.mp4').write_bytes(b'late')
        self.assertNotIn('20261115_D01',self.plan()['previous'][1]['folders'])
    def test_new_backdated_folder_is_current_without_calendar_guess(self):
        (self.project/'20261101').mkdir()
        self.assertIn('20261101',self.plan()['previous'][1]['folders'])
    def test_missing_assigned_day_refuses_instead_of_losing_coverage(self):
        (self.project/'20261115_D01').rename(self.project/'Renamed')
        with self.assertRaisesRegex(ValueError,'missing'):self.plan()
    def test_overlap_is_rejected(self):
        self.ledger['closed_sets'].append({'label':'B009','dates':['20261115_D01']})
        with self.assertRaisesRegex(ValueError,'Overlapping'):self.plan()
    def test_used_next_label_and_wrong_project_refuse(self):
        for label in ['B001','B002','../other']:
            with self.assertRaises(ValueError):self.plan(label=label)
        self.ledger['project']=str(self.base)
        with self.assertRaisesRegex(ValueError,'mismatch'):self.plan()
    def test_dated_folder_validation_avoids_glob_and_invalid_dates(self):
        for value in ['20260230','20261115*','../20261115','20261115_D?','20261115/child']:
            self.assertFalse(module.date_folder(value))
    def test_root_symlink_refuses(self):
        (self.project/'link').symlink_to(self.base)
        with self.assertRaisesRegex(ValueError,'symlink'):self.plan()
    def test_other_root_items_are_explicitly_flagged(self):
        (self.project/'_DIT_NOTES').mkdir();(self.project/'Interviews').mkdir()
        self.assertEqual(self.plan()['other_root_items_to_review'],['Interviews','_DIT_NOTES'])
    def test_atomic_new_output_never_replaces_an_existing_or_link(self):
        out=self.base/'proposal.json';module.save_new(out,self.plan());before=out.read_bytes()
        with self.assertRaises(ValueError):module.save_new(out,{'different':True})
        self.assertEqual(out.read_bytes(),before)
        target=self.base/'target';target.write_text('preserve');link=self.base/'link';link.symlink_to(target)
        with self.assertRaises(ValueError):module.save_new(link,{})
        self.assertEqual(target.read_text(),'preserve')
        self.assertFalse(list(self.base.glob('.rotation-*')))
    def test_unknown_operator_records_survive_and_pending_handover_refuses(self):
        self.ledger['verification_records']=[{'day':'20261115_D01','result':'operator evidence'}]
        self.ledger['closed_sets'][0]['task_uuid']='keep-this-task'
        self.assertEqual(self.plan()['proposed_ledger']['verification_records'],self.ledger['verification_records'])
        self.assertEqual(self.plan()['proposed_ledger']['closed_sets'][0]['task_uuid'],'keep-this-task')
        self.ledger['pending_rotation']={'next':'B003'}
        with self.assertRaisesRegex(ValueError,'pending'):self.plan()
    def test_dotdot_output_cannot_bypass_project_boundary(self):
        outer=self.base/'outer';outer.mkdir();ledger=self.base/'ledger.json';ledger.write_text(json.dumps(self.ledger))
        result=subprocess.run([sys.executable,str(SCRIPT),'--project',str(self.project),'--ledger',str(ledger),'--next-label','B003','--output',str(outer/'../Shoot/proposal.json')],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse((self.project/'proposal.json').exists())
    def test_cli_does_not_write_inside_project_or_modify_sources(self):
        ledger=self.base/'ledger.json';ledger.write_text(json.dumps(self.ledger));before={str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        args=[sys.executable,str(SCRIPT),'--project',str(self.project),'--ledger',str(ledger),'--next-label','B003','--output']
        failed=subprocess.run(args+[str(self.project/'proposal.json')],capture_output=True,text=True)
        self.assertNotEqual(failed.returncode,0);self.assertFalse((self.project/'proposal.json').exists())
        done=subprocess.run(args+[str(self.base/'proposal.json')],capture_output=True,text=True)
        self.assertEqual(done.returncode,0,done.stderr)
        self.assertFalse(json.loads(done.stdout)['applied'])
        self.assertEqual({str(p):p.read_bytes() for p in self.project.rglob('*') if p.is_file()},before)
        self.assertEqual(json.loads(ledger.read_text()),self.ledger)

if __name__=='__main__':unittest.main()
