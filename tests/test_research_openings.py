"""Opening context is bounded original evidence, using the shared read ledger."""
import unittest
from unittest.mock import Mock

from test_research_tools import fixture


class ResearchOpeningTests(unittest.TestCase):
    def tools(self, words=85, count=10):
        tools = fixture()
        for key in ('one', 'two'):
            tools.speech[key] = [{**tools.speech[key][0], 'id': f'{key}:speech:{i}',
                'text': ' '.join([f'{key}_{i}']*words), 'seconds': i*10, 'end': i*10+9}
                for i in range(count)]
        tools.by_id = {row['id']: row for rows in tools.speech.values() for row in rows}
        tools.pool = {}
        tools.notify = Mock()
        tools.infer = Mock(side_effect=AssertionError('No inference in source opening reads'))
        tools.call = Mock(side_effect=AssertionError('No new index or search in opening reads'))
        return tools

    def test_word_budget_includes_complete_final_original_passage_and_times(self):
        tools = self.tools()
        result = tools.read_interview_openings(['one'])
        self.assertEqual(result['interviews'][0]['word_count'], 425)
        self.assertEqual(result['interviews'][0]['passage_count'], 5)
        self.assertEqual(len(result['passages']), 5)
        aliases = tools.provenance_aliases()['evidence']
        for i, row in enumerate(result['passages']):
            canonical = aliases[row['id']]
            self.assertEqual(canonical, f'one:speech:{i}')
            self.assertEqual(row['text'], tools.by_id[canonical]['text'])
            self.assertEqual(row['seconds'], i*10)
            self.assertEqual(row['end'], i*10+9)
            self.assertIn('stream', row)
            self.assertNotIn('interview_label', row)
            self.assertEqual(tools.compact(tools.by_id[canonical])['end'], i*10+9)
        self.assertLessEqual(sum(len(row['text'].split()) for row in result['passages']), 450)
        self.assertEqual(tools.read_interviews, {'one'})
        self.assertEqual(tools.searched_interviews, set())
        tools.notify.assert_called_once_with('Reading interview context', {'passages': 5, 'interviews': 1})
        tools.infer.assert_not_called()
        tools.call.assert_not_called()

    def test_exact_budget_and_short_interview_do_not_add_or_invent_passages(self):
        exact = self.tools(words=50)
        self.assertEqual(exact.read_interview_openings(['one'])['interviews'][0]['word_count'], 350)
        short = self.tools(words=8, count=2)
        result = short.read_interview_openings(['two'])
        self.assertEqual(len(result['passages']), 2)
        self.assertEqual(result['interviews'][0]['word_count'], 16)

    def test_aliased_and_canonical_interviews_deduplicate_and_share_read_tracking(self):
        tools = self.tools()
        tools.model_coverage()
        first = tools.read_interview_openings(['i1', 'one', 'i2'])
        self.assertEqual(len(first['interviews']), 2)
        self.assertEqual(len(first['passages']), 10)
        again = tools.read_interview_openings(['one', 'two'])
        self.assertEqual(again['passages'], [])
        self.assertEqual(len(again['previously_read_ids']), 10)
        focused = tools.read_passages(['one:speech:0'], 0)
        self.assertEqual(focused['passages'], [])
        self.assertEqual(len(focused['previously_read_ids']), 1)
        self.assertEqual(len(tools.read_ids), 10)
        self.assertEqual(tools.read_interviews, {'one', 'two'})

    def test_bad_scope_and_zero_speech_do_not_partially_mark_context_read(self):
        for values in ([], ['one']*13, 'one', ['i999'], ['later'], ['one', 'zero']):
            with self.subTest(values=values):
                tools = self.tools()
                tools.assets['zero'] = {'id': 'zero', 'status': 'ready'}
                tools.speech['zero'] = []
                with self.assertRaises(ValueError):
                    tools.read_interview_openings(values)
                self.assertEqual(tools.read_ids, set())
                self.assertEqual(tools.read_interviews, set())

    def test_oversized_original_fails_without_truncation_or_partial_read(self):
        tools = self.tools(words=451)
        original = tools.by_id['one:speech:0']['text']
        with self.assertRaisesRegex(ValueError, 'exceeds the context budget'):
            tools.read_interview_openings(['one'])
        self.assertEqual(tools.by_id['one:speech:0']['text'], original)
        self.assertEqual(tools.read_ids, set())


if __name__ == '__main__':
    unittest.main()
