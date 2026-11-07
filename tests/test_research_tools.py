"""Research evidence validation uses original transcript sources, not answer text."""
import json
from pathlib import Path
import subprocess
import unittest

from ditnotes.research import ResearchTools, ResearchAnswer, ResearchClaim


def fixture():
    t = ResearchTools.__new__(ResearchTools)
    t.assets = {'one': {'id': 'one', 'name': 'one.mp4', 'relative_path': 'Interview One/one.mp4', 'status': 'ready'},
                'two': {'id': 'two', 'name': 'two.mp4', 'relative_path': 'Interview Two/two.mp4', 'status': 'ready'},
                'later': {'id': 'later', 'name': 'later.mp4', 'status': 'pending'}}
    t.speech = {key: [dict(id=key+':speech:'+str(i), assetId=key, text=text, seconds=i*10, end=i*10+9,
                            personNames=[], speakerIds=['unknown'], kind='speech', title=key+'.mp4')
                       for i, text in enumerate(['The interviewer asks about uncertainty.', 'The researcher describes uncertainty.', 'A qualified explanation follows.'])]
                for key in ('one', 'two')}
    t.by_id = {r['id']: r for rows in t.speech.values() for r in rows}
    t.catalog = {'assets': list(t.assets.values()), 'search_projection': {'documents': list(t.by_id.values())}}
    t.pool = {}
    t.shown = []
    t.read_ids = set()
    t.read_interviews = set()
    t.searched_interviews = set()
    t.snapshot_revision = 'fixture-snapshot'
    t.evidence_checks = 0
    t.requires_diversity = False
    t.notify = lambda *_: None
    return t


class EvidenceTests(unittest.TestCase):
    def test_model_context_is_lean_and_repeated_context_is_referenced(self):
        t = fixture()
        inventory = t.model_coverage()
        self.assertEqual(len(inventory['interviews']), 2)
        self.assertNotIn('duration_seconds', inventory['interviews'][0])
        first = t.read_passages(['one:speech:0'])
        second = t.read_passages(['one:speech:0'])
        self.assertEqual(len(first['passages']), 2)
        self.assertEqual(second['passages'], [])
        self.assertEqual(second['previously_read_ids'], [r['id'] for r in first['passages']])
        self.assertEqual(first['passages'][0]['text'], t.by_id['one:speech:0']['text'])
        self.assertNotIn('snapshot_revision', first['passages'][0])
        opened = t.open_sources(['one:speech:0'])
        self.assertEqual(set(opened['sources'][0]), {'id', 'citation_url'})
        self.assertEqual(t.shown[0]['snapshot_revision'], t.snapshot_revision)

    def test_numbered_all_and_both_require_requested_source_count(self):
        t = fixture()
        # A nine-interview project may ask for a four-source comparison.
        t.speech.update({str(i): [] for i in range(7)})
        for prompt in ('Use evidence from all four interviews.',
                       'Use evidence from all 4 completed interviews.'):
            t.configure_request(prompt)
            self.assertEqual(t.minimum_interviews, 4)
            self.assertFalse(t.requires_corpus)
        t.configure_request('Use evidence from all nine available interviews.')
        self.assertEqual(t.minimum_interviews, 9)
        self.assertTrue(t.requires_corpus)
        t.configure_request('Use evidence from both completed interviews.')
        self.assertEqual(t.minimum_interviews, 2)

    def test_numbered_all_minimum_rejects_fewer_sources_but_allows_qualified_partial(self):
        t = fixture()
        t.configure_request('Use evidence from all four interviews.')
        t.interview_coverage()
        t.read_passages(['one:speech:0', 'two:speech:0'])
        answer = ResearchAnswer(outcome='answered', answer='Comparison',
            claims=[ResearchClaim(text='Comparison', evidence_ids=['one:speech:0', 'two:speech:0'])],
            result_ids=['one:speech:0', 'two:speech:0'])
        with self.assertRaisesRegex(ValueError, 'at least 4'):
            t.validate_answer(answer)
        answer.outcome = 'partial'
        answer.limitations = ['Only two of the requested four interviews were reviewed.']
        t.validate_answer(answer)

    def test_saved_sources_pin_content_identity_and_snapshot_for_safe_playback(self):
        t = fixture()
        t.assets['one']['content_id'] = 'original-bytes'
        t.interview_coverage()
        t.read_passages(['one:speech:0'], 0)
        t.open_sources(['one:speech:0'])
        source = t.shown[0]
        self.assertEqual(source['content_id'], 'original-bytes')
        self.assertEqual(source['snapshot_revision'], 'fixture-snapshot')
        # Later catalog mutations cannot rewrite a previously emitted citation.
        t.assets['one']['content_id'] = 'replacement-bytes'
        self.assertEqual(source['content_id'], 'original-bytes')
        t.pool['one:speech:0']['contentId'] = 'explicit-transcript-content'
        self.assertEqual(t.compact(t.pool['one:speech:0'])['content_id'], 'explicit-transcript-content')

    def test_inventory_distinguishes_recording_label_from_speaker_and_incomplete_coverage(self):
        t = fixture()
        value = t.interview_coverage()
        self.assertEqual((value['ready'], value['total'], value['pending']), (2, 3, 1))
        self.assertEqual(value['read_interview_ids'], [])
        row = t.read_passages(['one:speech:0'], 0)['passages'][0]
        self.assertEqual(row['interview_label'], 'Interview One')
        self.assertNotIn('speaker_name', row)
        self.assertNotIn('attribution_note', row)

    def test_neighbor_context_preserves_exact_original_ids_and_times(self):
        t = fixture()
        t.pool['one:speech:1'] = t.by_id['one:speech:1']
        value = t.read_passages(['one:speech:1'])
        aliases = t.provenance_aliases()['evidence']
        self.assertEqual([aliases[r['id']] for r in value['passages']], ['one:speech:0', 'one:speech:1', 'one:speech:2'])
        self.assertEqual(value['passages'][1]['seconds'], 10)
        self.assertEqual(value['passages'][1]['end'], 19)
        self.assertNotIn('two:speech:0', t.read_ids)

    def test_unread_or_unknown_sources_cannot_support_claims(self):
        t = fixture()
        answer = ResearchAnswer(outcome='answered', answer='A claim', claims=[ResearchClaim(text='A claim', evidence_ids=['one:speech:1'])], result_ids=['one:speech:1'])
        with self.assertRaisesRegex(ValueError, 'read transcript'):
            t.validate_answer(answer)
        with self.assertRaisesRegex(ValueError, 'Read a passage'):
            t.read_passages(['invented'])
        with self.assertRaisesRegex(ValueError, 'Read original'):
            t.open_sources(['one:speech:1'])

    def test_quotes_must_match_cited_transcript_but_summary_can_paraphrase(self):
        t = fixture()
        t.interview_coverage()
        t.read_passages(['one:speech:0'])
        answer = ResearchAnswer(outcome='answered', answer='Uncertainty is discussed.', claims=[ResearchClaim(text='Uncertainty is discussed.', evidence_ids=['one:speech:1'], quotes=['describes uncertainty'])], result_ids=['one:speech:1'])
        t.validate_answer(answer)
        answer.claims[0].quotes = ['proved absolute certainty']
        with self.assertRaisesRegex(ValueError, 'quotation'):
            t.validate_answer(answer)

    def test_cross_interview_and_corpus_claims_require_diverse_and_reviewed_sources(self):
        t = fixture()
        t.configure_request('Compare accounts across the interviews')
        t.interview_coverage()
        t.read_passages(['one:speech:0'])
        answer = ResearchAnswer(outcome='answered', answer='A comparison', claims=[ResearchClaim(text='A comparison', evidence_ids=['one:speech:1'])], result_ids=['one:speech:1'])
        with self.assertRaisesRegex(ValueError, 'at least 2'):
            t.validate_answer(answer)
        t.read_passages(['two:speech:0'])
        answer.claims[0].evidence_ids.append('two:speech:1')
        answer.result_ids.append('two:speech:1')
        answer.scope = 'corpus'
        with self.assertRaisesRegex(ValueError, 'corpus-wide'):
            t.validate_answer(answer)
        t.searched_interviews = {'one', 'two'}
        answer.limitations = ['One pending interview was not reviewed.']
        t.validate_answer(answer)
        result = t.commit_answer(answer)
        self.assertEqual(len(result['results']), 2)
        self.assertEqual(result['coverage']['pending'], 1)

    def test_unsupported_response_does_not_publish_irrelevant_sources(self):
        t = fixture()
        answer = ResearchAnswer(outcome='not_supported', answer='No support in the indexed subset.')
        t.validate_answer(answer)
        answer.result_ids = ['one:speech:1']
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            t.validate_answer(answer)

    def test_abstention_cannot_hide_an_invented_citation_link(self):
        t = fixture()
        answer = ResearchAnswer(outcome='not_supported', answer='No support. [source](#shot=invented&t=1)')
        with self.assertRaisesRegex(ValueError, 'ungrounded source link'):
            t.validate_answer(answer)

    def test_search_balances_sources_and_uses_raw_rerank_scores(self):
        t = fixture()
        t.call = lambda **_: {'rows': list(t.by_id.values())}
        t.infer = lambda action, values, query='': {'vectors': [[1.0]+[0.0]*383]} if action == 'embed_text' else {'scores': [9, 8, 7, 1, -7, -8]}
        value = t.search_interviews('uncertainty', sources_per_interview=1)
        aliases = t.provenance_aliases()['interviews']
        self.assertEqual({aliases[r['asset_id']] for r in value['results']}, {'one', 'two'})
        self.assertEqual(len(value['results']), 2)
        self.assertEqual(t.searched_interviews, {'one', 'two'})
        self.assertEqual(t.read_ids, set())

    def test_global_request_cannot_bypass_coverage_with_focused_scope(self):
        t = fixture()
        t.configure_request('Find recurring themes across the interviews')
        t.interview_coverage()
        t.read_passages(['one:speech:0', 'two:speech:0'])
        answer = ResearchAnswer(outcome='answered', answer='Shared themes', scope='focused',
            claims=[ResearchClaim(text='Shared themes', evidence_ids=['one:speech:1', 'two:speech:1'])],
            result_ids=['one:speech:1', 'two:speech:1'])
        with self.assertRaisesRegex(ValueError, 'corpus-wide'):
            t.validate_answer(answer)
        answer.outcome = 'partial'
        answer.limitations = ['This comparison covers only the retrieved accounts; other interviews are pending or unreviewed.']
        t.validate_answer(answer)

    def test_requested_minimum_three_interviews_is_not_satisfied_by_two(self):
        t = fixture()
        t.configure_request('Compare evidence from at least three interviews')
        t.interview_coverage()
        t.read_passages(['one:speech:0', 'two:speech:0'])
        answer = ResearchAnswer(outcome='answered', answer='Shared themes',
            claims=[ResearchClaim(text='Shared themes', evidence_ids=['one:speech:1', 'two:speech:1'])],
            result_ids=['one:speech:1', 'two:speech:1'])
        with self.assertRaisesRegex(ValueError, 'at least 3'):
            t.validate_answer(answer)

    def test_coverage_polling_starts_no_subprocess_or_model(self):
        from ditnotes.research import catalog_coverage
        from unittest.mock import patch
        t = fixture()
        with patch('subprocess.Popen', side_effect=AssertionError('must not create a process')):
            value = catalog_coverage(t.catalog, 'pinned-revision')
        self.assertEqual(value['snapshot_revision'], 'pinned-revision')
        self.assertEqual((value['ready'], value['total']), (2, 3))


class NativeOramaResearch(unittest.TestCase):
    def test_shared_native_index_groups_speech_and_excludes_visual_and_pending(self):
        root = Path(__file__).resolve().parents[1]
        if not (root/'search/node_modules/@orama/orama').is_dir():
            self.skipTest('Run npm ci in search before native Orama tests')
        code = '''
import {createIndexes,searchResearch} from './indexes.mjs';
const docs=[...['one','two','pending'].flatMap(assetId=>[0,1,2].map(i=>({id:assetId+i,assetId,title:assetId,path:assetId+'/file.mp4',kind:'speech',text:'uncertainty in science',assetKind:'video'}))),{id:'visual',assetId:'one',title:'one',path:'one/file.mp4',kind:'visual',text:'uncertainty in science',assetKind:'video'}];
const indexes=createIndexes(docs);const r=await searchResearch(indexes.text,'uncertainty',['one','two'],null,1);console.log(JSON.stringify(r));
'''
        result = subprocess.run(['node', '--input-type=module'], cwd=root/'search', input=code, text=True, capture_output=True, check=True)
        value = json.loads(result.stdout)
        self.assertEqual({g['interview_id'] for g in value['groups']}, {'one', 'two'})
        self.assertEqual(len(value['rows']), 2)
        self.assertNotIn('visual', [r['id'] for r in value['rows']])


if __name__ == '__main__':
    unittest.main()
