"""Contextual search windows retain the original transcript as source evidence."""
import copy
import unittest
from ditnotes.search_projection import _transcript_passages, project_documents


class EmptyStore:
    project_id='synthetic'
    edit_revision=0
    def people(self):return []
    def unified_people(self,ensure=False):return []
    def faces(self):return []
    def asset_tag_assignments(self):return []
    def observations(self,content):return []
    def face_observations(self,content):return []
    def annotations(self,content):return []


class SearchPassages(unittest.TestCase):
    def test_short_acknowledgements_acquire_question_and_answer_context(self):
        source=[{'start':0,'end':2,'text':'What is life like on the station?'},
                {'start':2,'end':3,'text':"Yeah that's an experience."},
                {'start':3,'end':12,'text':'We cook dinner together and everyone cleans the kitchen before returning to our rooms.'}]
        windows=_transcript_passages(source)
        self.assertEqual(len(windows),1)
        self.assertIn('station',windows[0]['text'])
        self.assertIn('cook dinner',windows[0]['text'])
        self.assertEqual((windows[0]['start'],windows[0]['end']),(0,12))
        self.assertEqual(windows[0]['sentence_indices'],[0,1,2])

    def test_streams_and_real_gaps_do_not_blend_unrelated_context(self):
        source=[{'start':0,'end':3,'text':'We cook dinner at the station.','stream':0},
                {'start':1,'end':4,'text':'The laboratory instrument was broken.','stream':1},
                {'start':20,'end':24,'text':'Now the helicopter is landing.','stream':0}]
        windows=_transcript_passages(source)
        self.assertEqual(len(windows),3)
        self.assertEqual([(p['start'],p['end'],p['stream']) for p in windows],[(0,3,0),(1,4,1),(20,24,0)])
        self.assertFalse(any('dinner' in p['text'] and 'helicopter' in p['text'] for p in windows))

    def test_long_passages_are_bounded_with_modest_overlap_and_no_fabricated_times(self):
        source=[{'start':10,'end':80,'text':' '.join('word'+str(i) for i in range(250))}]
        windows=_transcript_passages(source)
        self.assertEqual([len(p['text'].split()) for p in windows],[100,100,80])
        for p in windows:self.assertEqual((p['start'],p['end']),(10,80))
        for a,b in zip(windows,windows[1:]):self.assertEqual(a['text'].split()[-15:],b['text'].split()[:15])
        self.assertEqual(set(source[0]['text'].split()),{w for p in windows for w in p['text'].split()})

    def test_isolated_filler_is_not_an_independent_semantic_result(self):
        self.assertEqual(_transcript_passages([{'start':0,'end':1,'text':'Yeah, okay.'}]),[])
        self.assertEqual(_transcript_passages([{'start':0,'end':1,'text':'Fire!'}])[0]['text'],'Fire!')

    def test_unordered_overlapping_sentences_use_their_actual_enclosing_bounds(self):
        source=[{'start':4,'end':8,'text':'Dinner is ready.'}, {'start':0,'end':10,'text':'We share kitchen duties at the station.'}]
        window=_transcript_passages(source)[0]
        self.assertEqual((window['start'],window['end']),(0,10))
        self.assertEqual(window['sentence_indices'],[1,0])

    def test_projection_does_not_modify_catalog_transcript_and_keeps_existing_kind(self):
        catalog={'assets':[{'id':'a','content_id':'sha','name':'interview.mp4','relative_path':'day/interview.mp4','transcript':[{'start':0,'end':2,'text':'What happens at the base?'},{'start':2,'end':7,'text':'We cook dinner and share the cleaning.'}]}]}
        before=copy.deepcopy(catalog)
        first=project_documents(catalog,EmptyStore())
        self.assertEqual(catalog,before)
        self.assertEqual(first,project_documents(catalog,EmptyStore()))
        speech=[d for d in first['documents'] if d['kind']=='speech']
        self.assertEqual(len(speech),1)
        self.assertTrue(speech[0]['embed'])
        self.assertEqual(speech[0]['source'],'ai_transcript')
        self.assertEqual((speech[0]['seconds'],speech[0]['end']),(0,7))
