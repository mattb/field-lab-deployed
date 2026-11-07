"""Opt-in lateral candidates reuse one shared search and remain read-required."""
import unittest
from unittest.mock import patch
from test_research_tools import fixture


class CohortCrossCheckTests(unittest.TestCase):
    def tools(self):
        t = fixture()
        for key in ('one', 'two', 'three', 'four', 'five', 'outside'):
            t.assets[key] = {'id': key, 'status': 'ready', 'name': key+'.mp4'}
            t.speech[key] = [dict(id=f'{key}:speech:{i}', assetId=key, seconds=i*10, end=i*10+9,
                text=' '.join([key]*85), kind='speech') for i in range(8)]
        t.by_id = {r['id']: r for rows in t.speech.values() for r in rows}
        t.pool = {}
        t.configure_request('Compare accounts across interviews')
        t.read_interview_openings(['one', 'two', 'three', 'four', 'five'])
        t.calls, t.inferences = [], []
        def search(**kwargs):
            t.calls.append(kwargs)
            return {'rows': [r for key in kwargs['asset_ids'] for r in t.speech[key][5:7]]}
        def infer(action, values, query=''):
            t.inferences.append(action)
            return {'vectors': [[1.0]+[0.0]*383]} if action == 'embed_text' else {'scores': [1]*len(values)}
        t.call, t.infer = search, infer
        return t

    def test_opt_in_scope_cap_cost_and_read_requirement(self):
        t = self.tools()
        default = t.search_interviews('shared concept', ['one'])
        self.assertNotIn('cross_checks', default)
        self.assertEqual(t.calls[-1]['asset_ids'], ['one'])
        t.calls.clear(); t.inferences.clear()
        result = t.search_interviews('shared concept', ['one'], cohort_cross_check=True)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(len(result['cross_checks']), 3)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(t.inferences, ['embed_text', 'rerank'])
        self.assertEqual(t.calls[0]['asset_ids'], ['one', 'two', 'three', 'four'])
        self.assertNotIn('outside', t.searched_interviews)
        candidate = result['cross_checks'][0]['id']
        with self.assertRaises(ValueError): t.open_sources([candidate])
        self.assertEqual(len(t.read_passages([candidate], 0)['passages']), 1)
        self.assertEqual(len(t.open_sources([candidate])['sources']), 1)
        t.requires_diversity = False
        self.assertEqual(t.search_interviews('concept', ['one'], cohort_cross_check=True)['cross_checks'], [])
        with self.assertRaises(ValueError): t.search_interviews('concept', ['one'], cohort_cross_check='yes')

    def test_opt_in_context_returns_cross_candidate_originals_and_full_canonical_provenance(self):
        t = self.tools()
        before = set(t.read_ids)
        result = t.search_interviews('shared concept', ['one'], cohort_cross_check=True, read_context=True)
        aliases = t.provenance_aliases()['evidence']
        returned = {aliases[row['id']] for row in result['context']}
        self.assertEqual(t.read_ids-before, returned)
        for candidate in result['results']+result['cross_checks']:
            self.assertIn(aliases[candidate['id']], t.read_ids)
            self.assertNotIn('text', candidate)  # Full text appears once in context.
        for row in result['context']:
            canonical = aliases[row['id']]
            self.assertEqual(row['text'], t.by_id[canonical]['text'])
            full = t.compact(t.by_id[canonical])
            self.assertEqual(full['snapshot_revision'], 'fixture-snapshot')
            self.assertEqual(full['end'], t.by_id[canonical]['end'])
            self.assertEqual(full['id'], canonical)
        self.assertEqual(t.inferences, ['embed_text', 'rerank'])
        again = t.search_interviews('shared concept', ['one'], cohort_cross_check=True, read_context=True)
        self.assertEqual(again['context'], [])
        self.assertTrue(again['previously_read_ids'])

    def test_context_batches_at_most_thirty_and_rolls_back_a_failed_later_batch(self):
        t = self.tools()
        for key in ('six', 'seven', 'eight'):
            t.assets[key] = {'id': key, 'status': 'ready', 'name': key+'.mp4'}
            t.speech[key] = [{**row, 'id': f'{key}:speech:{i}', 'assetId': key}
                            for i, row in enumerate(t.speech['one'])]
        t.by_id = {r['id']: r for rows in t.speech.values() for r in rows}
        t.call = lambda **kwargs: {'rows':[r for key in kwargs['asset_ids'] for r in t.speech[key][4:8]]}
        with patch.object(t, 'read_passages', wraps=t.read_passages) as reads:
            result = t.search_interviews('concept', sources_per_interview=4, read_context=True)
        self.assertGreaterEqual(len(reads.call_args_list), 2)
        self.assertTrue(all(len(call.args[0]) <= 30 for call in reads.call_args_list))
        self.assertTrue(result['context'])
        # Exercise rollback with genuinely fresh first-batch reads.
        t.read_ids.clear(); t.read_interviews.clear(); t._model_read_ids.clear()
        old_read, old_model = set(t.read_ids), set(t._model_read_ids)
        actual_read, attempts = t.read_passages, []
        def fail_second(*args, **kwargs):
            attempts.append(1)
            if len(attempts) == 2: raise RuntimeError('injected second context-batch failure')
            return actual_read(*args, **kwargs)
        with patch.object(t, 'read_passages', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'second context-batch'):
                t.search_interviews('concept', sources_per_interview=4, read_context=True)
        self.assertEqual(t.read_ids, old_read)
        self.assertEqual(t._model_read_ids, old_model)

    def test_timestamp_context_includes_qualifier_thirty_five_seconds_earlier_not_other_stream(self):
        t = fixture()
        rows = [dict(id=f'one:speech:{i}', assetId='one', seconds=start, end=start+2,
                     stream=stream, kind='speech', text=text)
                for i, (start, stream, text) in enumerate([
                    (0, 0, 'Too early.'), (15, 0, 'This applies only under the stated condition.'),
                    (25, 0, 'A short question.'), (35, 0, 'Another short utterance.'),
                    (45, 0, 'The immediate setup.'), (50, 0, 'A qualified answer.'),
                    (51, 1, 'Different stream must not be mixed.'), (98, 0, 'Too late.')])]
        t.speech = {'one': rows}; t.by_id = {r['id']:r for r in rows}; t.pool = {}
        t.call = lambda **_: {'rows':[rows[5]]}
        t.infer = lambda action, values, query='': {'vectors':[[1.0]+[0.0]*383]} if action=='embed_text' else {'scores':[1]}
        result = t.search_interviews('condition', ['one'], read_context=True)
        returned = {t.provenance_aliases()['evidence'][r['id']]:r for r in result['context']}
        self.assertIn(rows[1]['id'], returned)
        self.assertNotIn(rows[0]['id'], returned)
        self.assertNotIn(rows[6]['id'], returned)
        self.assertNotIn(rows[7]['id'], returned)
        for key, row in returned.items():
            self.assertEqual(row['text'], t.by_id[key]['text'])
            self.assertEqual(t.compact(t.by_id[key])['stream'], 0)

    def test_pathological_overlapping_asr_chunks_are_capped_explicitly_without_text_truncation(self):
        t = fixture()
        rows = [dict(id=f'one:speech:{i}', assetId='one', seconds=50, end=52, stream=0,
                     kind='speech', text=' '.join([f'chunk{i}']*85)) for i in range(400)]
        t.speech = {'one':rows}; t.by_id = {r['id']:r for r in rows}; t.pool = {}
        t.call = lambda **_: {'rows':[rows[0]]}
        t.infer = lambda action, values, query='': {'vectors':[[1.0]+[0.0]*383]} if action=='embed_text' else {'scores':[1]}
        result = t.search_interviews('condition', ['one'], read_context=True)
        self.assertIn('Some original context was omitted', result['context_limit_note'])
        self.assertLessEqual(sum(len(r['text'].split()) for r in result['context']), 1200)
        self.assertIn(rows[0]['id'], t.read_ids)
        aliases = t.provenance_aliases()['evidence']
        for row in result['context']:
            self.assertEqual(row['text'], t.by_id[aliases[row['id']]]['text'])


if __name__ == '__main__': unittest.main()
