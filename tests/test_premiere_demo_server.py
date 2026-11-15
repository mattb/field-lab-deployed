import json,tempfile,unittest
from pathlib import Path
from development.serve_premiere_demo import serve


class DemoServerSafety(unittest.TestCase):
 def test_real_project_is_rejected_before_opening_any_metadata_database(self):
  with tempfile.TemporaryDirectory() as directory:
   project=Path(directory);notes=project/'_DIT_NOTES';notes.mkdir()
   (notes/'catalog.json').write_text(json.dumps({'project':{'id':'real-project'},'assets':[]}))
   before=sorted(str(p.relative_to(project)) for p in project.rglob('*'))
   with self.assertRaisesRegex(ValueError,'synthetic'):serve(project,0,'Field Lab Demo.prproj')
   self.assertEqual(before,sorted(str(p.relative_to(project)) for p in project.rglob('*')))
