"""Research-only contextual ranking preserves direct hits and original reads."""
import unittest

from test_research_tools import fixture


def row(source, sentence, part, text, start, stream=0):
    return dict(id=f'{source}:speech:{sentence}:{part}', assetId=source, kind='speech',
                text=text, seconds=start, end=start+2, stream=stream)


class ResearchWindowRerankTests(unittest.TestCase):
    def tools(self, rows, candidates, score):
        tools = fixture()
        tools.speech = {}
        for item in rows:
            tools.speech.setdefault(item['assetId'], []).append(item)
            tools.assets.setdefault(item['assetId'], {'id':item['assetId'], 'name':item['assetId']+'.mp4', 'status':'ready'})
        tools.by_id = {r['id']:r for r in rows}
        tools.pool = {}
        calls, reranks = [], []
        def native(**kwargs):
            calls.append(kwargs)
            return {'rows':candidates}
        def infer(action, values, query=''):
            if action == 'embed_text': return {'vectors':[[1.0]+[0.0]*383]}
            reranks.append((query, list(values)))
            return {'scores':[score(text) for text in values]}
        tools.call, tools.infer = native, infer
        return tools, calls, reranks

    def test_context_rescues_fragment_and_cannot_erase_existing_pass(self):
        rows = [row('one', 0, 0, 'Ambiguous fragment.', 100),
                row('one', 0, 1, 'The complete qualified explanation.', 100),
                row('two', 0, 0, 'Direct match.', 100),
                row('two', 1, 0, 'Distracting neighboring exchange.', 110),
                row('three', 0, 0, 'Unrelated material.', 100)]
        def score(text):
            if text == 'Direct match.': return 1
            if 'complete qualified explanation' in text and 'Ambiguous fragment.' in text: return 2
            return -9
        tools, calls, reranks = self.tools(rows, [rows[0], rows[2], rows[4]], score)
        result = tools.search_interviews('literal concept', sources_per_interview=1)
        aliases = tools.provenance_aliases()['interviews']
        self.assertEqual({aliases[r['asset_id']] for r in result['results']}, {'one', 'two'})
        self.assertEqual(len(reranks), 1)
        self.assertEqual(calls[0]['per_interview'], 12)
        self.assertEqual(tools.read_ids, set())
        self.assertEqual(getattr(tools, '_model_read_ids', set()), set())
        for neighbor in (rows[1], rows[3]):
            with self.assertRaisesRegex(ValueError, 'Read original'):
                tools.open_sources([neighbor['id']])
        for result_row in result['results']:
            canonical = tools.provenance_aliases()['evidence'][result_row['id']]
            self.assertEqual(result_row['text'], tools.by_id[canonical]['text'])

    def test_one_inference_deduplicates_bare_and_shared_sentence_windows(self):
        rows = [row('one', 0, 0, 'A fragment.', 10), row('one', 0, 1, 'Its qualifier.', 10)]
        tools, _, reranks = self.tools(rows, rows, lambda text:1)
        tools.search_interviews('concept', sources_per_interview=2)
        self.assertEqual(len(reranks), 1)
        texts = reranks[0][1]
        self.assertEqual(set(texts), {'A fragment.', 'Its qualifier.', 'A fragment. Its qualifier.'})
        self.assertEqual(len(texts), len(set(texts)))
        self.assertEqual(tools.read_ids, set())

    def test_ranking_context_never_crosses_source_stream_or_time_window(self):
        rows = [row('one', 0, 0, 'Ambiguous fragment.', 100),
                row('one', 1, 0, 'Ordinary same-stream context.', 120),
                row('one', 2, 0, 'MAGIC wrong stream.', 101, stream=1),
                row('two', 0, 0, 'MAGIC wrong source.', 100),
                row('one', 3, 0, 'MAGIC outside time window.', 200)]
        tools, _, reranks = self.tools(rows, [rows[0]], lambda text:10 if 'MAGIC' in text else -9)
        result = tools.search_interviews('concept', ['one'])
        self.assertEqual(result['results'], [])
        self.assertFalse(any('MAGIC' in text for text in reranks[0][1]))
        self.assertTrue(any('same-stream context' in text for text in reranks[0][1]))
        self.assertEqual(tools.read_ids, set())

    def test_ranking_does_not_bypass_full_original_read_budget(self):
        rows = [row('one', 0, 0, 'fragment', 10), row('one', 0, 1, 'qualification', 10)]
        tools, _, _ = self.tools(rows, [rows[0]], lambda text:1)
        tools.MAX_READ_WORDS = 1
        result = tools.search_interviews('concept', ['one'], read_context=True)
        self.assertTrue(result['budget_exhausted'])
        self.assertEqual(result['context'], [])
        self.assertEqual(tools.read_ids, set())
        self.assertEqual(tools.read_interviews, set())
        self.assertEqual(tools.read_budget()['words'], 0)

    def test_oversized_sentence_is_not_cut_to_make_ranking_window(self):
        rows = [row('one', 0, i, ' '.join([f'part{i}']*200), 10) for i in range(2)]
        tools, _, reranks = self.tools(rows, [rows[0]], lambda text:1)
        result = tools.search_interviews('concept', ['one'], read_context=True)
        self.assertEqual(reranks[0][1], [rows[0]['text']])  # Original bare path only.
        self.assertEqual(tools.read_ids, {r['id'] for r in rows})
        self.assertEqual({r['text'] for r in result['context']}, {r['text'] for r in rows})


if __name__ == '__main__':
    unittest.main()
