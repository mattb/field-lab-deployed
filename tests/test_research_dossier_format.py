"""Model rendering preserves canonical transcript evidence without rounding."""
from copy import deepcopy
import json
import unittest

from ditnotes.research_agent import dossier_for_model, dossier_from_model


class ResearchDossierFormatTests(unittest.TestCase):
    def dossier(self):
        return {'interviews':[
            {'id':'i1', 'label':'Recording A', 'passages':[
                {'id':'e1', 'seconds':1.2345678901, 'end':9.8765432109, 'stream':0,
                 'voice_groups':['v1'], 'text':'Question? Exact words, including "quotes" and | delimiters.'},
                {'id':'e2', 'seconds':10.0000000001, 'end':100.123456789, 'stream':None,
                 'voice_groups':['v1', 'v2'], 'speaker_name':'Saved label', 'text':'A qualified answer.\nAnother line.'}]},
            {'id':'i2', 'label':'Recording B', 'passages':[
                {'id':'e3', 'seconds':1000.012345, 'end':1009.999999, 'stream':1,
                 'voice_groups':[], 'text':'Different recording; no named speaker.'}]}],
            'coverage':{'read_passages':3, 'pending':2}, 'source_note':'Machine transcripts only.',
            'collection_status':{'stop_reason':'collection_budget_exhausted'}}

    def test_compact_columnar_round_trip_keeps_all_original_values_and_fields(self):
        original = self.dossier()
        before = deepcopy(original)
        rendered = dossier_for_model(original)
        packed = json.loads(rendered)
        self.assertEqual(packed['dossier_format'], 'columnar-v1')
        self.assertEqual(packed['passage_columns'], ['id', 'seconds', 'end', 'stream', 'voice_groups', 'text'])
        self.assertEqual(packed['interviews'][0]['passages'][1][-1], {'speaker_name':'Saved label'})
        self.assertEqual(dossier_from_model(rendered), original)
        self.assertEqual(dossier_from_model(packed), original)
        self.assertEqual(original, before)
        self.assertIn('1.2345678901', rendered)  # No display-time rounding.
        self.assertNotIn('"id": ', rendered)

    def test_old_canonical_and_empty_dossiers_round_trip_without_invented_fields(self):
        old = {'interviews':[{'id':'i1', 'label':'Recording', 'passages':[{'id':'e1', 'seconds':1, 'text':'Original.'}]}],
               'coverage':{}, 'source_note':'Original only.'}
        self.assertEqual(dossier_from_model(old), old)
        self.assertEqual(dossier_from_model(dossier_for_model(old)), old)
        empty = {'interviews':[], 'coverage':{}, 'source_note':'No collected passages.'}
        self.assertEqual(dossier_from_model(dossier_for_model(empty)), empty)

    def test_unknown_original_fields_and_absent_versus_null_are_preserved(self):
        dossier = self.dossier()
        first, second = dossier['interviews'][0]['passages']
        first['future_context'] = {'condition':None}
        second['speaker_name'] = None
        restored = dossier_from_model(dossier_for_model(dossier))
        self.assertEqual(restored, dossier)
        self.assertNotIn('speaker_name', restored['interviews'][0]['passages'][0])
        self.assertIsNone(restored['interviews'][0]['passages'][1]['speaker_name'])

    def test_invalid_columnar_width_or_duplicate_fields_fail_closed(self):
        packed = json.loads(dossier_for_model(self.dossier()))
        packed['passage_columns'].append('id')
        with self.assertRaises(ValueError):
            dossier_from_model(packed)
        packed = json.loads(dossier_for_model(self.dossier()))
        packed['interviews'][0]['passages'][0].append({'id':'forged'})
        with self.assertRaises(ValueError):
            dossier_from_model(packed)
        packed = json.loads(dossier_for_model(self.dossier()))
        packed['interviews'][0]['passages'][0].pop()
        with self.assertRaises(ValueError):
            dossier_from_model(packed)


if __name__ == '__main__':
    unittest.main()
