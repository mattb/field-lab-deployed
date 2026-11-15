import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ditnotes.premiere_transcript import convert_tracks, project_transcript, transcript_summary


def track():
    return {'stream_index': 0, 'start_seconds': 10, 'language': 'en',
            'sentences': [{'text': ' Sea ice, changes.', 'start': 10, 'end': 12,
                'tokens': [{'text': ' Se', 'start': 10, 'end': 10.2, 'confidence': .8},
                           {'text': 'a', 'start': 10.2, 'end': 10.4, 'confidence': .9},
                           {'text': ' ice', 'start': 10.6, 'end': 11, 'confidence': .95},
                           {'text': ',', 'start': 11, 'end': 11, 'confidence': 1},
                           {'text': ' changes', 'start': 11.2, 'end': 11.9, 'confidence': .9},
                           {'text': '.', 'start': 11.9, 'end': 12, 'confidence': 1}]}],
            'diarization': {'model': {'revision': 'saved-revision'}, 'token_speakers': [
                {'sentence_index': 0, 'token_index': i, 'local_speaker': '0', 'overlap': False} for i in range(6)]},
            'speaker_assignments': [{'local_speaker': '0', 'person_id': 'person-a'}]}


class TranscriptTests(unittest.TestCase):
    people = [{'id': 'person-a', 'name': 'Morgan', 'merged_into': None}]

    def convert(self, tracks=None, content='content', people=None, observations=()):
        return convert_tracks(tracks or [track()], 20, content_id=content,
                              people=self.people if people is None else people, observations=observations)

    def test_bpe_words_keep_actual_source_spans_not_sentence_interpolation(self):
        doc, status = self.convert()
        words = [w for s in doc['segments'] for w in s['words']]
        self.assertEqual([w['text'] for w in words], ['Sea', 'ice,', 'changes.'])
        self.assertEqual([(w['start'], round(w['duration'], 3)) for w in words], [(10, .4), (10.6, .4), (11.2, .8)])
        self.assertEqual(words[0]['confidence'], .8)
        self.assertTrue(words[-1]['eos'])
        self.assertEqual(status['tokens'], 6)
        self.assertEqual(status['timestamp_basis'], 'source_media_seconds')
        # Track offset10 has ALREADY been applied by saved Parakeet worker.
        self.assertEqual(words[0]['start'], 10)

    def test_identity_global_across_clips_and_current_name_and_merge_followed(self):
        first, _ = self.convert(content='clip-a')
        second, _ = self.convert(content='clip-b')
        self.assertEqual(first['speakers'], second['speakers'])
        people = [{'id': 'person-a', 'name': None, 'merged_into': 'person-b'},
                  {'id': 'person-b', 'name': 'Casey', 'merged_into': None}]
        merged, _ = self.convert(people=people)
        self.assertEqual(merged['speakers'][0]['name'], 'Casey')
        self.assertNotEqual(first['speakers'][0]['id'], merged['speakers'][0]['id'])

    def test_current_observation_reassignment_overrides_old_alignment_identity(self):
        people = self.people + [{'id': 'person-b', 'name': 'Casey', 'merged_into': None}]
        observations = [{'local_speaker': 'stream-0:diar-saved-revisi:0', 'person_id': 'person-b'}]
        doc, _ = self.convert(people=people, observations=observations)
        self.assertEqual(doc['speakers'][0]['name'], 'Casey')

    def test_overlap_or_ambiguous_subword_is_not_named_as_a_known_person(self):
        item = track()
        item['diarization']['token_speakers'][0]['overlap'] = True
        doc, status = self.convert([item])
        self.assertEqual(status['unassigned_words'], 1)
        self.assertEqual({r['name'] for r in doc['speakers']}, {'Morgan', 'Unassigned voice'})
        self.assertNotEqual(doc['segments'][0]['speaker'], doc['segments'][1]['speaker'])

    def test_unvoiced_zero_duration_punctuation_does_not_erase_a_word_speaker(self):
        item = track()
        item['diarization']['token_speakers'][3]['local_speaker'] = None
        doc, status = self.convert([item])
        self.assertEqual(status['unassigned_words'], 0)
        self.assertEqual([row['name'] for row in doc['speakers']], ['Morgan'])
        self.assertFalse(status['language_default_used'])
        item.pop('language'); item['tags'] = {'language': 'und'}
        self.assertTrue(self.convert([item])[1]['language_default_used'])

    def test_unlinked_local_voices_are_not_global_people(self):
        item = track(); item.pop('speaker_assignments')
        a, _ = self.convert([item], content='a', people=[])
        b, _ = self.convert([item], content='b', people=[])
        self.assertEqual(a['speakers'][0]['name'], 'Voice 0 (unlinked)')
        self.assertNotEqual(a['speakers'][0]['id'], b['speakers'][0]['id'])

    def test_missing_timing_is_unavailable_not_fabricated(self):
        item = track(); item['sentences'][0].pop('tokens')
        doc, status = self.convert([item])
        self.assertIsNone(doc)
        self.assertEqual(status['reason'], 'word_timing_unavailable')

    def test_invalid_source_time_or_text_fails_without_mutating_saved_input(self):
        for mutation in ('nan', 'reversed', 'outside', 'text', 'multiword'):
            item = track(); token = item['sentences'][0]['tokens'][0]
            if mutation == 'nan': token['start'] = float('nan')
            if mutation == 'reversed': token['end'] = 9
            if mutation == 'outside': token['end'] = 21
            if mutation == 'text': item['sentences'][0]['text'] = 'Other sentence.'
            if mutation == 'multiword': token['text'] = ' Se a'; item['sentences'][0]['text'] = ' Se aa ice, changes.'
            before = copy.deepcopy(item)
            with self.assertRaises(ValueError, msg=mutation): self.convert([item])
            self.assertEqual(str(item), str(before))

    def test_multiple_streams_sort_real_time_without_erasing_overlapping_speech(self):
        a = track(); b = track(); b['stream_index'] = 1
        doc, status = self.convert([a, b])
        self.assertEqual(status['words'], 6)
        self.assertEqual([s['start'] for s in doc['segments']], [10, 10])
        self.assertEqual(transcript_summary(doc)['word_count'], 6)


class SavedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.notes = self.root / '_DIT_NOTES'; self.folder = self.notes / '.objects' / 'content' / 'profile'
        self.folder.mkdir(parents=True); self.path = self.folder / 'result.json'
        self.path.write_text(json.dumps({'tracks': [track()]}))
        self.asset = {'id': 'asset', 'content_id': 'content', 'duration': 20,
                      'result_url': '/notes/.objects/content/profile/result.json'}

    def tearDown(self): self.temp.cleanup()

    def project(self, people=None):
        return project_transcript(self.root, self.asset, people=people or TranscriptTests.people)

    def test_selected_object_and_matching_alignment_only_cache_tracks_and_names(self):
        one, _ = self.project()
        alignment = self.notes / 'content-identity-alignment.json'
        renamed = track(); renamed['speaker_assignments'][0]['person_id'] = 'person-b'
        alignment.write_text(json.dumps({'content_id': 'content', 'profile_id': 'stale', 'tracks': [renamed]}))
        self.assertEqual(self.project()[0], one)
        alignment.write_text(json.dumps({'content_id': 'content', 'profile_id': 'profile', 'tracks': [renamed]}))
        people = TranscriptTests.people + [{'id': 'person-b', 'name': 'Casey', 'merged_into': None}]
        doc, _ = self.project(people)
        self.assertEqual(doc['speakers'][0]['name'], 'Casey')
        people[-1]['name'] = 'Renamed voice'
        self.assertEqual(self.project(people)[0]['speakers'][0]['name'], 'Renamed voice')
        self.assertEqual(json.loads(self.path.read_text())['tracks'][0]['sentences'][0]['text'], ' Sea ice, changes.')

    def test_corrupt_atomic_candidate_recovers_without_overwriting_source(self):
        before = self.path.read_bytes(); self.project()
        self.path.write_text('{truncated')
        with self.assertRaises(json.JSONDecodeError): self.project()
        self.path.write_bytes(before)
        self.assertEqual(self.project()[1]['state'], 'ready')

    def test_foreign_or_escape_or_symlink_result_refused(self):
        original = self.asset['result_url']
        for url in ('/notes/.objects/foreign/profile/result.json', '/notes/.objects/content/../result.json', '/external.json'):
            self.asset['result_url'] = url
            with self.assertRaises(ValueError): self.project()
        self.asset['result_url'] = original
        with tempfile.TemporaryDirectory() as outside:
            foreign = Path(outside) / 'foreign.json'; foreign.write_bytes(self.path.read_bytes())
            self.path.unlink(); self.path.symlink_to(foreign)
            with self.assertRaises(ValueError): self.project()
