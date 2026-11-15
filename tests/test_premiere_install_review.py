"""Independent packaging review probes using temporary, invented plugin payloads."""
import json
import sqlite3
import unittest
from deploy import premiere
from tests import test_premiere_packaging as fixtures


class InstallReviewTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PackagingTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_extra_privileged_manifest_permission_is_rejected(self):
        fixture = self.fixture
        path = fixture.source / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['requiredPermissions']['clipboard'] = 'readAndWrite'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            fixture.staged()

    def test_private_payload_cannot_hide_in_ccx_signature_directory(self):
        fixture = self.fixture
        candidate = fixture.staged()
        ccx = fixture.ccx(candidate, 'META-INF/operator-database.sqlite')
        with self.assertRaises(ValueError):
            premiere.attach(fixture.home, candidate['candidate_id'], ccx)

    def test_reused_candidate_must_reobserve_adobe_registration_after_external_drift(self):
        fixture = self.fixture
        candidate = fixture.packaged()
        fixture.install(candidate)
        base = premiere.root(fixture.home)
        state = premiere.read_state(base)
        state['runtime_verified'] = {'candidate_id': candidate['candidate_id'], 'at': 1}
        premiere.manager.atomic_json(base / 'state.json', state)
        calls = []
        def missing(command):
            calls.append(command)
            return {'returncode': 0, 'stdout': '', 'stderr': ''}
        try:
            premiere.install(fixture.home, candidate['candidate_id'], True,
                             running=lambda: False, runner=missing)
        except ValueError:
            pass
        self.assertIn(['--list', 'all'], calls)
        self.assertFalse(premiere.read_state(base).get('runtime_verified'))
        self.assertFalse(any(command[0] == '--install' for command in calls),
                         'Drift review must not implicitly reinstall or remove anything')


    def test_saved_file_cannot_override_unreconciled_project_operation(self):
        fixture = self.fixture
        candidate = fixture.packaged()
        fixture.install(candidate)
        record, _ = premiere.verified_package(fixture.home, candidate['candidate_id'])
        receipt = fixture.project / '_DIT_NOTES/editorial/bridge/receipt.json'
        receipt.parent.mkdir(parents=True)
        packet = {'protocol': 1, 'status': 'saved', 'operation_id': 'fresh-operation',
                  'desired_hash': 'desired', 'plugin_build': record['plugin_build'],
                  'project_id': 'project', 'native_project_path': '/native/project.prproj',
                  'readback': {'native_project_guid': 'guid'}}
        fixture.register_receipt(receipt, packet)
        with sqlite3.connect(receipt.parents[2] / 'project.sqlite3') as database:
            database.execute("UPDATE premiere_bridge_operations SET status='applied'")
        with self.assertRaises(ValueError):
            premiere.verify_runtime(fixture.home, candidate['candidate_id'], receipt,
                                    'fresh-operation')
        self.assertFalse(premiere.read_state(premiere.root(fixture.home)).get('runtime_verified'))

    def test_copied_evidence_path_does_not_establish_installed_runtime(self):
        fixture = self.fixture
        candidate = fixture.packaged()
        fixture.install(candidate)
        record, _ = premiere.verified_package(fixture.home, candidate['candidate_id'])
        receipt = fixture.base / 'copied-receipt.json'
        receipt.write_text(json.dumps({'protocol': 1, 'status': 'saved',
            'operation_id': 'fake-operation', 'plugin_build': record['plugin_build'],
            'project_id': 'project', 'native_project_path': '/native/project.prproj',
            'readback': {'native_project_guid': 'guid'}}))
        with self.assertRaises(ValueError):
            premiere.verify_runtime(fixture.home, candidate['candidate_id'], receipt,
                                    'fake-operation')


if __name__ == '__main__':
    unittest.main()
