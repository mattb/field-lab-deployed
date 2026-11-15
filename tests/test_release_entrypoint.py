"""Latest-release discovery and self-contained manifest use disposable fixtures."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from deploy.resolve_release import select,resolve,validate_manifest


def release(tag,time,**extra):return {'tag_name':tag,'published_at':time,'draft':False,'prerelease':'-' in tag,**extra}

class ReleaseEntry(unittest.TestCase):
    def test_latest_published_prerelease_wins_over_stable_and_draft(self):
        rows=[release('v0.1.0','2026-11-01T00:00:00Z'),release('v0.1.0-alpha.9','2026-11-15T20:00:00Z'),release('v0.1.0-alpha.10','2026-11-15T22:00:00Z'),release('v0.1.0-alpha.11',None,draft=True)]
        self.assertEqual(select(rows)['tag'],'v0.1.0-alpha.10')
        self.assertEqual(select(list(reversed(rows)))['tag'],'v0.1.0-alpha.10')
    def test_invalid_missing_or_unpublished_tags_cannot_be_selected(self):
        for rows in [[],[release('../main','2026-11-15T20:00:00Z')],[release('v0.1.0',None)],[release('v0.1.0','invalid')]]:
            with self.assertRaises(ValueError):select(rows)
    def test_pagination_is_not_stable_latest_endpoint(self):
        calls=[]
        def opener(request,timeout):
            calls.append(request.full_url)
            batch=[release('v0.1.0-alpha.1','2026-11-01T00:00:00Z')]*100 if len(calls)==1 else [release('v0.1.0-alpha.10','2026-11-15T22:00:00Z')]
            return io.StringIO(json.dumps(batch))
        self.assertEqual(resolve(opener)['tag'],'v0.1.0-alpha.10')
        self.assertEqual(len(calls),2);self.assertTrue(all('/releases?' in url and '/latest' not in url for url in calls))
    def test_bad_api_response_and_network_failure_never_guess(self):
        with self.assertRaises(ValueError):resolve(lambda *a,**k:io.StringIO('{}'))
        def failed(*args,**kwargs):raise OSError('offline')
        with self.assertRaises(OSError):resolve(failed)
    def test_manifest_tag_and_local_procedures_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();(root/'docs').mkdir();(root/'docs/UPDATE.md').write_text('entry');(root/'docs/backup.md').write_text('migration')
            value={'schema':1,'tag':'v0.1.0-alpha.10','update_entrypoint':'docs/UPDATE.md','workflow_migrations':[{'id':'ccc-date-filter-v1','procedure':'docs/backup.md'}]}
            path=root/'FIELD-LAB-RELEASE.json';path.write_text(json.dumps(value))
            self.assertEqual(validate_manifest(root,value['tag']),value)
            with self.assertRaises(ValueError):validate_manifest(root,'v0.1.0-alpha.9')
            value['workflow_migrations'][0]['procedure']='../outside.md';path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):validate_manifest(root,value['tag'])
    def test_missing_or_symlink_procedure_cannot_escape_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();outside=root/'other';outside.write_text('outside');(root/'docs').mkdir();(root/'docs/entry').symlink_to(outside)
            value={'schema':1,'tag':'v0.1.0-alpha.10','update_entrypoint':'docs/entry','workflow_migrations':[]}
            (root/'FIELD-LAB-RELEASE.json').write_text(json.dumps(value))
            with self.assertRaises(ValueError):validate_manifest(root,value['tag'])
    def test_current_release_is_self_contained_in_source_seal(self):
        from deploy.manager import source_files
        root=Path(__file__).resolve().parents[1]
        manifest=validate_manifest(root,'v0.1.0-alpha.10');files=source_files(root)
        for path in ['FIELD-LAB-RELEASE.json','deploy/resolve_release.py',manifest['update_entrypoint'],*[row['procedure'] for row in manifest['workflow_migrations']]]:
            self.assertIn(path,files)

if __name__=='__main__':unittest.main()
