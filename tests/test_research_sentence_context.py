"""Original ASR sentence siblings are atomic evidence, not joined testimony."""
import unittest
from unittest.mock import Mock, patch

from ditnotes.research import ResearchAnswer, ResearchClaim
from test_research_tools import fixture


def row(sentence, part, text, *, source='one', stream=0, start=10):
    return dict(id=f'{source}:speech:{sentence}:{part}', assetId=source, kind='speech',
                stream=stream, seconds=start, end=start+5, text=text)


class ResearchSentenceContextTests(unittest.TestCase):
    def tools(self, rows):
        tools = fixture()
        tools.speech = {}
        for item in rows:
            tools.speech.setdefault(item['assetId'], []).append(item)
        tools.by_id = {r['id']:r for r in rows}
        tools.pool = {}
        tools.notify = Mock()
        return tools

    def test_one_chunk_reads_all_same_sentence_qualifiers_not_other_stream_or_source(self):
        rows = [row(0, 0, 'An apparent effect.'), row(0, 1, 'This does not establish a cause.'),
                row(0, 2, 'A different audio stream.', stream=1),
                row(0, 0, 'Another source.', source='two'), row(1, 0, 'A different sentence.')]
        tools = self.tools(rows)
        tools.pool[rows[0]['id']] = rows[0]
        result = tools.read_passages([rows[0]['id']], 0)
        self.assertEqual([r['text'] for r in result['passages']], [rows[0]['text'], rows[1]['text']])
        self.assertEqual(tools.read_ids, {r['id'] for r in rows[:2]})
        answer = ResearchAnswer(outcome='answered', answer='An account.',
            claims=[ResearchClaim(text='A qualified account', evidence_ids=[r['id'] for r in rows[:2]],
                                  quotes=['An apparent effect. This does not establish a cause.'])],
            result_ids=[r['id'] for r in rows[:2]])
        with self.assertRaisesRegex(ValueError, 'quotation does not match'):
            tools.validate_answer(answer)  # Admission never joins quote strings.

    def test_legacy_unmatched_ids_are_individual_rows(self):
        rows = [dict(row(0, 0, 'Legacy original.'), id='one:speech:0'),
                dict(row(0, 1, 'Another legacy original.'), id='one:speech:1')]
        tools = self.tools(rows)
        tools.pool[rows[0]['id']] = rows[0]
        result = tools.read_passages([rows[0]['id']], 0)
        self.assertEqual(len(result['passages']), 1)
        self.assertEqual(tools.read_ids, {rows[0]['id']})

    def test_opening_soft_target_finishes_sentence_and_reports_expanded_counts(self):
        rows = [row(0, part, ' '.join(['setup']*75)) for part in range(4)]
        rows += [row(1, 0, ' '.join(['claim']*60), start=20),
                 row(1, 1, 'Not established. '+' '.join(['qualification']*58), start=20),
                 row(2, 0, 'Beyond the orientation target.', start=30)]
        tools = self.tools(rows)
        result = tools.read_interview_openings(['one'])
        self.assertEqual(result['interviews'][0]['word_count'], 420)
        self.assertEqual(result['interviews'][0]['passage_count'], 6)
        self.assertEqual(tools.read_ids, {r['id'] for r in rows[:6]})
        tools = self.tools(rows)
        tools.MAX_READ_WORDS = 400
        refused = tools.read_interview_openings(['one'])
        self.assertTrue(refused['budget_exhausted'])
        self.assertEqual(tools.read_ids, set())
        self.assertFalse(getattr(tools, '_opening_cohort', set()))

    def test_expanded_union_is_preflighted_before_any_batch_or_partial_read(self):
        rows = [row(0, part, 'original') for part in range(31)]
        tools = self.tools(rows)
        tools.MAX_READ_PASSAGES = 30
        with patch.object(tools, 'read_passages', wraps=tools.read_passages) as reads:
            refused = tools._read_context_batches({rows[0]['id']:rows[0]})
        reads.assert_not_called()
        self.assertTrue(refused['budget_exhausted'])
        self.assertEqual(tools.read_ids, set())
        self.assertEqual(tools.pool, {})
        tools.notify.assert_not_called()
        tools = self.tools(rows)
        original_read = tools.read_passages
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError('injected later batch failure')
            return original_read(*args, **kwargs)
        with patch.object(tools, 'read_passages', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'later batch'):
                tools._read_context_batches({rows[0]['id']:rows[0]})
        self.assertEqual(tools.read_ids, set())
        self.assertEqual(getattr(tools, '_model_read_ids', set()), set())
        self.assertEqual(tools.pool, {})
        self.assertEqual(tools.read_budget()['passages'], 0)

    def test_auto_context_keeps_whole_qualifier_and_omits_oversized_sentence_honestly(self):
        for count, words in ((2, 2), (16, 85), (241, 1)):
            with self.subTest(count=count, words=words):
                rows = [row(0, part, ' '.join(['not-established']*words)) for part in range(count)]
                tools = self.tools(rows)
                tools.call = lambda **_: {'rows':[rows[0]]}
                tools.infer = lambda action, values, query='': {'vectors':[[1.0]+[0.0]*383]} if action == 'embed_text' else {'scores':[1]*len(values)}
                result = tools.search_interviews('qualified claim', ['one'], read_context=True)
                if count == 2:
                    self.assertEqual(tools.read_ids, {r['id'] for r in rows})
                    self.assertEqual(len(result['context']), 2)
                else:
                    self.assertEqual(result['context'], [])
                    self.assertEqual(tools.read_ids, set())
                    self.assertIn('candidate may remain unread', result['context_limit_note'])
                    # A specific read can admit the whole sentence within the
                    # separate run budget; per-search omission is not absence.
                    tools.read_passages([rows[0]['id']], 0)
                    self.assertEqual(tools.read_ids, {r['id'] for r in rows})


if __name__ == '__main__':
    unittest.main()
