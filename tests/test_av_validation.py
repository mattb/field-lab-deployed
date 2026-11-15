"""Code-blind use-case checks for cross-recording common-voice validation."""
import math
import unittest

from ditnotes.av_validation import common_voice


def unit(*values):
    norm=math.sqrt(sum(v*v for v in values))
    return [v/norm for v in values]


def row(content, local, vector, revision='fixture-ecapa-v1'):
    return {'content_id':content,'local_speaker':local,'embedding':vector,'model_revision':revision}


class CommonVoiceValidation(unittest.TestCase):
    def fixture(self):
        # The recurring interviewer changes diarizer labels; other identities
        # are different in every recording. Clip c has two interviewees.
        basis=[[int(i==j) for j in range(8)] for i in range(8)]
        return [row('a','0',basis[0]),row('a','1',basis[1]),
                row('b','1',unit(1,.05,0,0,0,0,0,0)),row('b','0',basis[2]),
                row('c','2',unit(1,0,.05,0,0,0,0,0)),row('c','0',basis[3]),row('c','1',basis[4]),
                row('d','0',unit(1,0,0,.05,0,0,0,0)),row('d','1',basis[5])]

    def test_global_match_survives_local_label_swap_and_three_speaker_exception(self):
        result=common_voice(self.fixture())
        self.assertEqual({c:r['local_speaker'] for c,r in result['matches'].items()}, {'a':'0','b':'1','c':'2','d':'0'})
        for match in result['matches'].values():
            self.assertGreaterEqual(match['cosine'],.72)
            self.assertGreaterEqual(match['margin'],.25)

    def test_input_order_cannot_choose_the_other_local_label(self):
        rows=self.fixture()
        expected={c:r['local_speaker'] for c,r in common_voice(rows)['matches'].items()}
        actual={c:r['local_speaker'] for c,r in common_voice(list(reversed(rows)))['matches'].items()}
        self.assertEqual(actual,expected)

    def test_no_recurring_voice_abstains(self):
        rows=[row(str(i),'0',[int(i==j) for j in range(6)]) for i in range(6)]
        self.assertFalse(common_voice(rows)['matches'])

    def test_many_windows_from_one_recording_cannot_self_validate(self):
        rows=[row('same-file',str(i),unit(1,.001*i,0)) for i in range(10)]
        self.assertFalse(common_voice(rows)['matches'])

    def test_two_recordings_are_insufficient(self):
        rows=[r for r in self.fixture() if r['content_id'] in ('a','b')]
        self.assertFalse(common_voice(rows)['matches'])

    def test_within_recording_ambiguous_voice_abstains(self):
        rows=self.fixture()+[row('ambiguous','0',[1,0,0,0,0,0,0,0]),row('ambiguous','1',[1,0,0,0,0,0,0,0])]
        self.assertNotIn('ambiguous',common_voice(rows)['matches'])

    def test_incompatible_embedding_namespaces_refuse(self):
        rows=self.fixture();rows[-1]['model_revision']='different-embedding-model'
        with self.assertRaises(ValueError):common_voice(rows)

    def test_incompatible_dimensions_refuse(self):
        rows=self.fixture();rows[-1]['embedding']=[1,0,0]
        with self.assertRaises(ValueError):common_voice(rows)

    def test_nonfinite_vector_refuses(self):
        rows=self.fixture();rows[-1]['embedding'][0]=float('nan')
        with self.assertRaises(ValueError):common_voice(rows)



class ApplyPlanSafety(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from ditnotes.project_store import ProjectStore
        self.tmp=tempfile.TemporaryDirectory();self.project=Path(self.tmp.name)
        self.notes=self.project/'_DIT_NOTES';self.notes.mkdir();self.db=self.notes/'project.sqlite3'
        with ProjectStore(self.db,'fixture-project') as store:
            self.face=store.assign_face('clip',1,[1,0],'face-fixture',1,bbox=[.1,.1,.8,.8])['face_id']
            store.name_face(self.face,'Alex')
            self.voice=store.assign_speaker('clip','speaker0',0,4,[1,0],'voice-fixture',1)['person_id']
            store.ensure_unified_people()
            self.base=store.edit_revision
        self.plan={'project_id':'fixture-project','base_revision':self.base,'links':[{'face_id':self.face,'voice_id':self.voice,'name':'Alex','basis':'Synthetic cross-validated evidence'}],'sources':[]}

    def tearDown(self):self.tmp.cleanup()

    def store(self):
        from ditnotes.project_store import ProjectStore
        return ProjectStore(self.db,'fixture-project')

    def applied_count(self):
        with self.store() as store:return store.db.execute("SELECT COUNT(*) FROM audit WHERE operation='av_validation_applied'").fetchone()[0]

    def run_killed(self,phase):
        import json,subprocess,sys
        path=self.project/'plan.json';path.write_text(json.dumps(self.plan))
        script="""import json,os,signal,sys
# Disposable contract fixture: subprocess does not inherit the CI parent mock.
from unittest.mock import patch
patch('ditnotes.sqlite_safety.require_safe_sqlite').start()
from ditnotes.av_validation import apply_plan
def checkpoint(phase):
 if phase==sys.argv[3]:os.kill(os.getpid(),signal.SIGKILL)
apply_plan(sys.argv[1],json.load(open(sys.argv[2])),checkpoint=checkpoint)
"""
        result=subprocess.run([sys.executable,'-c',script,str(self.project),str(path),phase],capture_output=True,text=True)
        self.assertEqual(result.returncode,-9,result.stderr)

    def test_atomic_success_keeps_raw_evidence_and_is_idempotent(self):
        from ditnotes.av_validation import apply_plan
        from deploy.people import legacy_fingerprints
        with self.store() as store:before=legacy_fingerprints(store.db)
        report=apply_plan(self.project,self.plan)
        self.assertEqual(len(report['applied']),1)
        with self.store() as store:
            self.assertEqual(before,legacy_fingerprints(store.db))
            self.assertEqual(store.person_for_evidence('face',self.face),store.person_for_evidence('voice',self.voice))
        self.assertEqual(apply_plan(self.project,self.plan)['job_id'],report['job_id'])
        self.assertEqual(self.applied_count(),1)

    def test_sigkill_before_commit_rolls_back_every_link_and_retry_succeeds(self):
        from ditnotes.av_validation import apply_plan
        self.run_killed('before_commit');self.assertEqual(self.applied_count(),0)
        with self.store() as store:
            self.assertNotEqual(store.person_for_evidence('face',self.face),store.person_for_evidence('voice',self.voice))
            self.assertEqual(store.edit_revision,self.base)
            self.assertEqual(store.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(len(apply_plan(self.project,self.plan)['applied']),1)

    def test_sigkill_after_commit_repairs_missing_receipt_without_reapplying(self):
        from ditnotes.av_validation import apply_plan
        self.run_killed('database_committed');self.assertEqual(self.applied_count(),1)
        self.assertFalse(list((self.notes/'av-validation').glob('*.receipt.json')))
        report=apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),1)
        self.assertEqual(len(list((self.notes/'av-validation').glob('*.receipt.json'))),1)
        self.assertEqual(len(report['applied']),1)

    def test_stale_revision_refuses_operator_overwrite(self):
        from ditnotes.av_validation import apply_plan
        with self.store() as store:store.note_unified_person(store.person_for_evidence('face',self.face),'Saved by operator')
        with self.assertRaisesRegex(ValueError,'changed'):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)

    def test_prior_rejection_is_not_silently_overridden(self):
        from ditnotes.av_validation import apply_plan
        with self.store() as store:
            store.reject_person_pair(store.person_for_evidence('face',self.face),store.person_for_evidence('voice',self.voice));self.plan['base_revision']=store.edit_revision
        with self.assertRaisesRegex(ValueError,'rejection'):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)

    def test_changed_sealed_analysis_refuses_apply(self):
        from ditnotes.av_validation import apply_plan
        source=self.notes/'source.json';source.write_text('new data')
        self.plan['sources']=[{'path':'source.json','sha256':'0'*64}]
        with self.assertRaisesRegex(ValueError,'source changed'):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)

    def test_second_bad_link_rolls_back_first_good_link(self):
        from ditnotes.av_validation import apply_plan
        self.plan['links'].append({'face_id':self.face,'voice_id':'missing-voice','name':'Alex','basis':'Bad synthetic second link'})
        with self.assertRaises((KeyError,ValueError)):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)
        with self.store() as store:self.assertNotEqual(store.person_for_evidence('face',self.face),store.person_for_evidence('voice',self.voice))

    def test_overlapping_distinct_voices_cannot_be_one_person(self):
        from ditnotes.av_validation import apply_plan
        with self.store() as store:
            other=store.assign_speaker('clip','speaker1',1,3,[0,1],'voice-fixture',1)['person_id']
            store.merge_unified_people(store.person_for_evidence('voice',self.voice),store.person_for_evidence('face',self.face),'Existing reviewed link',chosen_name='Alex')
            self.plan['links'][0]['voice_id']=other;self.plan['base_revision']=store.edit_revision
        with self.assertRaisesRegex(ValueError,'Overlapping distinct'):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)

    def test_missing_registry_is_not_created_as_a_workaround(self):
        from ditnotes.av_validation import apply_plan
        other=self.project/'empty';other.mkdir()
        with self.assertRaisesRegex(ValueError,'missing'):apply_plan(other,self.plan)
        self.assertFalse((other/'_DIT_NOTES/project.sqlite3').exists())

    def test_still_interviewer_exception_labels_audio_without_claiming_visible_identity(self):
        from ditnotes.av_validation import apply_plan
        self.plan['links']=[]
        self.plan['voice_labels']=[{'voice_id':self.voice,'name':'Elle','basis':'Explicit self-introduction; visible still is someone else'}]
        report=apply_plan(self.project,self.plan)
        self.assertEqual(len(report['named_voices']),1)
        with self.store() as store:
            face=store.person_for_evidence('face',self.face);voice=store.person_for_evidence('voice',self.voice)
            self.assertNotEqual(face,voice)
            person=next(p for p in store.unified_people() if p['id']==voice)
            self.assertEqual(person['name'],'Elle')
            self.assertEqual({m['modality'] for m in person['members']},{'voice'})

    def test_voice_only_label_cannot_overwrite_an_existing_name(self):
        from ditnotes.av_validation import apply_plan
        with self.store() as store:
            store.rename_unified_person(store.person_for_evidence('voice',self.voice),'Existing operator name');self.plan['base_revision']=store.edit_revision
        self.plan['links']=[];self.plan['voice_labels']=[{'voice_id':self.voice,'name':'Elle','basis':'Conflicting guess'}]
        with self.assertRaisesRegex(ValueError,'conflicts'):apply_plan(self.project,self.plan)
        self.assertEqual(self.applied_count(),0)


if __name__=='__main__':unittest.main()
