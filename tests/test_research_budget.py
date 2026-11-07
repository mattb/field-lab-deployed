"""Run-level original-evidence budgets admit complete unions or nothing."""
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest.mock import patch

from ditnotes.research_agent import original_dossier
from test_research_tools import fixture


class ResearchReadBudgetTests(unittest.TestCase):
    def tools(self, count=200, words=85, interviews=1):
        t = fixture()
        t._ledger_lock()  # Production construction initializes this lock too.
        t.assets, t.speech = {}, {}
        for n in range(interviews):
            key = 'source'+str(n)
            t.assets[key] = {'id':key, 'name':key+'.mp4', 'status':'ready'}
            t.speech[key] = [dict(id=f'{key}:speech:{i}', assetId=key, kind='speech', stream=0,
                seconds=50, end=52, text=' '.join([f'chunk{i}']*words)) for i in range(count)]
        t.by_id = {r['id']:r for rows in t.speech.values() for r in rows}
        t.catalog = {'assets':list(t.assets.values()), 'search_projection':{'documents':list(t.by_id.values())}}
        t.pool = {}
        return t

    def selected(self, t, rows):
        return {row['id']:row for row in rows}

    def test_word_limit_refuses_whole_request_and_rereads_are_free(self):
        t = self.tools()
        rows = t.speech['source0']
        accepted = self.selected(t, rows[:141])  # 11,985 complete words.
        t._read_original_passages(accepted, 'Fixture read')
        old = set(t.read_ids)
        refused = t._read_original_passages(self.selected(t, rows[141:143]), 'Fixture read')
        self.assertTrue(refused['budget_exhausted'])
        self.assertEqual(refused['passages'], [])
        self.assertEqual(t.read_ids, old)
        self.assertEqual(t.read_budget()['words'], 11985)
        again = t._read_original_passages(accepted, 'Fixture read')
        self.assertEqual(again['passages'], [])
        self.assertEqual(len(again['previously_read_ids']), 141)
        self.assertEqual(t.read_budget()['words'], 11985)
        self.assertTrue(t.interview_coverage()['read_budget']['exhausted'])
        self.assertIn('not evidence of absence', original_dossier(t)['coverage']['read_budget']['note'])

    def test_passage_count_limit_protects_fragmented_asr_without_cutting_text(self):
        t = self.tools(count=801, words=1)
        rows = t.speech['source0']
        result = t._read_original_passages(self.selected(t, rows[:800]), 'Fixture read')
        self.assertEqual(len(result['passages']), 800)
        refused = t._read_original_passages(self.selected(t, rows[800:]), 'Fixture read')
        self.assertTrue(refused['budget_exhausted'])
        self.assertEqual(t.read_budget()['passages'], 800)
        self.assertEqual(t.by_id[rows[800]['id']]['text'], rows[800]['text'])

    def test_search_preflights_entire_context_before_any_thirty_id_batch(self):
        t = self.tools(count=80, words=1)
        t.MAX_READ_PASSAGES = 40
        t.call = lambda **_: {'rows':[t.speech['source0'][0]]}
        t.infer = lambda action, values, query='': {'vectors':[[1.0]+[0.0]*383]} if action=='embed_text' else {'scores':[1]}
        with patch.object(t, 'read_passages', wraps=t.read_passages) as reads:
            result = t.search_interviews('concept', ['source0'], read_context=True)
        reads.assert_not_called()
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['context'], [])
        self.assertEqual(t.read_ids, set())
        self.assertEqual(result['coverage']['read_passages'], 0)
        self.assertIn('processing limit', result['note'])

    def test_nine_openings_plus_one_eight_thousand_word_context_fit(self):
        t = self.tools(interviews=9)
        openings = t.read_interview_openings(list(t.speech))
        self.assertFalse(openings.get('budget_exhausted', False))
        result = t._read_original_passages(self.selected(t, t.speech['source0'][5:99]), 'Fixture context')
        self.assertFalse(result.get('budget_exhausted', False))
        self.assertEqual(t.read_budget()['words'], 3825+7990)
        self.assertEqual(len(t.read_interviews), 9)

    def test_refused_openings_do_not_claim_orientation_and_failure_rolls_back_counts(self):
        t = self.tools(count=80, words=1)
        t.MAX_READ_PASSAGES = 20
        refused = t.read_interview_openings(['source0'])
        self.assertTrue(refused['budget_exhausted'])
        self.assertFalse(getattr(t, '_opening_cohort', set()))
        self.assertEqual(t.read_interviews, set())
        t = self.tools(count=80, words=1)
        calls, original = [], t.read_passages
        def second_fails(*args, **kwargs):
            calls.append(1)
            if len(calls)==2: raise RuntimeError('injected batch failure')
            return original(*args, **kwargs)
        with patch.object(t, 'read_passages', side_effect=second_fails):
            with self.assertRaisesRegex(RuntimeError, 'batch failure'):
                t._read_context_batches(self.selected(t, t.speech['source0']))
        self.assertEqual(t.read_budget()['words'], 0)
        self.assertEqual(t.read_budget()['passages'], 0)
        self.assertFalse(t.read_budget()['exhausted'])
        with patch.object(t, 'notify', side_effect=RuntimeError('injected publication failure')):
            with self.assertRaisesRegex(RuntimeError, 'publication failure'):
                t._read_original_passages(self.selected(t, t.speech['source0'][:1]), 'Fixture read')
        self.assertEqual(t.read_budget()['words'], 0)

    def test_concurrent_external_admission_cannot_oversubscribe(self):
        t = self.tools(count=2, words=1)
        t.MAX_READ_PASSAGES = 1
        barrier = threading.Barrier(2)
        def read(row):
            barrier.wait(timeout=5)
            return t._read_original_passages({row['id']:row}, 'Fixture concurrent read')
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(read, t.speech['source0']))
        self.assertEqual(sum(bool(r.get('budget_exhausted')) for r in results), 1)
        self.assertEqual(t.read_budget()['passages'], 1)


if __name__ == '__main__': unittest.main()
