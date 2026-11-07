"""Synthetic source boundaries, exact quotation and publish-budget checks."""
import unittest

from ditnotes.research import ResearchAnswer, ResearchClaim
from ditnotes.research_agent import original_dossier
from test_research_tools import fixture


class ResearchSourceIntegrityTests(unittest.TestCase):
    def test_exact_quote_cannot_splice_recordings_or_nonadjacent_chunks(self):
        for second in ('two:speech:0', 'one:speech:2', 'one:speech:1'):
            with self.subTest(second=second):
                tools = fixture()
                first = 'one:speech:0'
                tools.by_id[first]['text'] = 'alpha'
                tools.by_id[second]['text'] = 'beta'
                tools.interview_coverage()
                tools.pool[second] = tools.by_id[second]
                tools.read_passages([first, second], 0)
                answer = ResearchAnswer(outcome='answered', answer='An account.',
                    claims=[ResearchClaim(text='A claim', evidence_ids=[first, second], quotes=['alpha beta'])],
                    result_ids=[first, second])
                with self.assertRaisesRegex(ValueError, 'quotation does not match'):
                    tools.validate_answer(answer)
                # Whitespace normalization within a complete original is valid.
                tools.by_id[first]['text'] = 'alpha  gamma'
                answer.claims[0].quotes = ['alpha gamma']
                tools.validate_answer(answer)

    def test_neighbor_context_stays_in_same_audio_stream(self):
        tools = fixture()
        rows = [dict(id=f'one:speech:{i}', assetId='one', seconds=start, end=start+1,
                     stream=stream, text=text, kind='speech')
                for i, (start, stream, text) in enumerate([
                    (0, 0, 'Question?'), (1, 1, 'Unrelated alternate stream.'),
                    (2, 0, 'Answer.'), (3, 1, 'Another alternate stream.')])]
        tools.speech = {'one': rows}
        tools.by_id = {r['id']: r for r in rows}
        tools.pool = {rows[0]['id']: rows[0]}
        result = tools.read_passages([rows[0]['id']], 1)
        self.assertEqual([r['text'] for r in result['passages']], ['Question?', 'Answer.'])
        self.assertEqual(tools.read_ids, {rows[0]['id'], rows[2]['id']})
        self.assertTrue(all(r['stream'] == 0 for r in result['passages']))

    def test_dossier_preserves_whole_originals_streams_and_visible_time_gaps(self):
        tools = fixture()
        first, later = tools.speech['one'][0], tools.speech['one'][2]
        first.update(stream=0, seconds=0, end=2)
        later.update(stream=1, seconds=1000, end=1003)
        tools.pool = {r['id']: r for r in (first, later)}
        tools.read_passages([first['id'], later['id']], 0)
        dossier = original_dossier(tools, {'stop_reason':'collection_budget_exhausted'})
        passages = dossier['interviews'][0]['passages']
        self.assertEqual([r['text'] for r in passages], [first['text'], later['text']])
        self.assertEqual([(r['stream'], r['seconds'], r['end']) for r in passages],
                         [(0, 0, 2), (1, 1000, 1003)])
        self.assertNotIn(tools.speech['one'][1]['text'], str(dossier))
        self.assertIn('not necessarily adjacent dialogue', dossier['source_note'])
        self.assertEqual(dossier['collection_status']['stop_reason'], 'collection_budget_exhausted')

    def test_model_retries_source_union_over_forty_before_publication(self):
        tools = fixture()
        rows = [dict(id=f'one:speech:{i}', assetId='one', seconds=i*2, end=i*2+1,
                     stream=0, text=f'Original passage {i}.', kind='speech') for i in range(41)]
        tools.speech = {'one': rows}
        tools.by_id = {r['id']:r for r in rows}
        tools.pool = dict(tools.by_id)
        tools.read_ids = set(tools.by_id)
        tools.read_interviews = {'one'}
        answer = ResearchAnswer(outcome='answered', answer='An account [e1].',
            claims=[ResearchClaim(text='An account', evidence_ids=list(tools.by_id))])
        tools.model_passage(rows[0])  # Issue the answer's valid inline marker.
        with self.assertRaisesRegex(ValueError, 'at most forty'):
            tools.validate_model_answer(answer)
        self.assertEqual(answer.result_ids, [])  # A failed retry is transactional.
        self.assertEqual(tools.shown, [])
        answer.claims[0].evidence_ids.pop()
        validated = tools.validate_model_answer(answer)
        tools.commit_answer(validated)
        self.assertEqual(len(tools.shown), 40)


if __name__ == '__main__':
    unittest.main()
