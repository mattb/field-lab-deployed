"""Useful distant qualifications should survive duplicate nearby search hits."""
import unittest

from test_research_tools import fixture


def row(sentence, text, start, stream=0):
    return dict(id=f'one:speech:{sentence}:0', assetId='one', kind='speech',
                text=text, seconds=start, end=start+5, stream=stream)


class ResearchContextSelectionTests(unittest.TestCase):
    def tools(self):
        tools = fixture()
        rows = [row(0, 'The long series provides a stable baseline.', 100),
                row(1, 'Repeated measurements make this comparison possible.', 110),
                row(2, 'An instrument change can mimic an environmental shift.', 300),
                row(3, 'The dining room opens at noon.', 600)]
        tools.speech = {'one': rows}
        tools.by_id = {item['id']:item for item in rows}
        tools.call = lambda **kwargs: {'rows':rows}
        def infer(action, values, query=''):
            if action == 'embed_text':
                return {'vectors':[[1.0]+[0.0]*383]}
            def score(text):
                if 'stable baseline' in text: return 8
                if 'Repeated measurements' in text: return 7
                if 'instrument change' in text: return 6
                return -9
            return {'scores':[score(text) for text in values]}
        tools.infer = infer
        return tools, rows

    def test_second_window_reads_distant_qualification_not_another_nearby_hit(self):
        tools, rows = self.tools()
        result = tools.search_interviews('long data record', ['one'],
                                         sources_per_interview=2, read_context=True)
        aliases = tools.provenance_aliases()['evidence']
        self.assertEqual([aliases[item['id']] for item in result['results']],
                         [rows[0]['id'], rows[2]['id']])
        self.assertIn(rows[2]['id'], tools.read_ids)
        self.assertEqual(next(item['text'] for item in result['context']
                              if aliases[item['id']] == rows[2]['id']), rows[2]['text'])
        self.assertNotIn(rows[3]['id'], tools.pool)  # Below the existing relevance gate.
        self.assertNotIn(rows[3]['id'], tools.read_ids)

    def test_single_source_request_retains_best_hit_and_does_not_expand_recall(self):
        tools, rows = self.tools()
        result = tools.search_interviews('long data record', ['one'],
                                         sources_per_interview=1, read_context=True)
        aliases = tools.provenance_aliases()['evidence']
        self.assertEqual([aliases[item['id']] for item in result['results']], [rows[0]['id']])
        self.assertNotIn(rows[2]['id'], tools.read_ids)

    def test_overlapping_hits_fill_unused_slots_without_dropping_requested_sources(self):
        tools, rows = self.tools()
        self.assertEqual(tools._select_context_hits([(8, rows[0]), (7, rows[1])], 2),
                         [(8, rows[0]), (7, rows[1])])

    def test_overlapping_times_in_distinct_streams_are_not_duplicate_dialogue(self):
        tools, rows = self.tools()
        other_stream = row(4, 'An independently recorded channel.', 105, stream=1)
        self.assertEqual(tools._select_context_hits(
            [(8, rows[0]), (7, rows[1]), (6, other_stream)], 2),
            [(8, rows[0]), (6, other_stream)])


if __name__ == '__main__':
    unittest.main()
