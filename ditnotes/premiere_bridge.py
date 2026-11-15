"""Offline, project-bound Premiere synchronization journal.

No Adobe process or source media is modified here. The UXP adapter consumes
immutable typed snapshots, reports native readback, then explicitly acknowledges
that Premiere saved. SQLite is authoritative; JSON files are replayable exchange
projections, never a second queue. Runtime needs no model, agent or network.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import threading
import time
import uuid
from urllib.parse import unquote

from .atomic import atomic_json
from .project_store import ProjectStore
from .premiere_transcript import project_transcript, transcript_summary

PROTOCOL = 1
# Bump when deterministic metadata/native mappings change, even if the source
# catalog and human edits do not. This is independent of AI inference profiles:
# a bridge upgrade reprojects metadata rather than re-running expensive models.
PREMIERE_PROJECTION_VERSION = 3
MAX_ASSETS = 10000
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _seconds(value, duration):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid source time')
    if value < 0 or value > duration + .001:
        raise ValueError('Source time exceeds clip duration')
    return float(value)


def _range(start, end, duration):
    start = _seconds(start, duration)
    end = start if end is None else _seconds(end, duration)
    if end < start:
        raise ValueError('Source range is reversed')
    return start, end


def _local_path(project, relative):
    if not isinstance(relative, str) or not relative or '\x00' in relative:
        raise ValueError('Invalid media path')
    path = project / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Media must be inside this project')
    # A mounted/linked outside source must not gain authority through a manifest.
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(project) or not resolved.is_file():
        raise ValueError('Media must be a regular file inside this project')
    return str(resolved)


def build_snapshot(project, catalog, store):
    """Project the same authoritative human entities used by the web/search UI."""
    project = Path(project).resolve(strict=True)
    if catalog.get('project', {}).get('id') != store.project_id:
        raise ValueError('Catalog and metadata project identities differ')
    source_assets = catalog.get('assets', [])
    if not isinstance(source_assets, list) or len(source_assets) > MAX_ASSETS:
        raise ValueError('Invalid asset count')
    tags = {}
    for tag in store.asset_tag_assignments():
        tags.setdefault(tag['content_id'], []).append({'id': tag['id'], 'name': tag['name']})
    all_people = store.people()
    people = {p['id']: p for p in all_people if not p['merged_into']}
    faces = {p['id']: p for p in store.faces() if not p['merged_into']}
    assets = []
    seen = set()
    for item in source_assets:
        asset_id = str(item['id'])
        if asset_id in seen:
            raise ValueError('Duplicate asset identity')
        seen.add(asset_id)
        content_id = str(item.get('content_id') or asset_id)
        duration = _seconds(item.get('duration', 0), float('inf'))
        if duration <= 0:
            raise ValueError('Asset duration is not ready')
        path = _local_path(project, item['relative_path'])
        notes = []
        markers = []
        for note in store.annotations(content_id):
            notes.append({k: note[k] for k in ('id', 'text', 'start', 'end', 'source', 'metadata')})
            if note['start'] is not None:
                start, end = _range(note['start'], note['end'], duration)
                markers.append({'id': 'note:' + note['id'], 'name': note['text'][:100],
                                'start': start, 'end': end, 'comments': note['text'], 'source': note['source']})
        # Explicit ranges are real outlines. Sparse highlights remain point notes;
        # a sampled still is never promoted into a claim about continuous footage.
        outline = item.get('outline') or []
        for index, chunk in enumerate(outline):
            start, end = _range(chunk['start'], chunk.get('end'), duration)
            text = str(chunk.get('text', chunk.get('note', '')))
            markers.append({'id': 'outline:' + str(chunk.get('id', index)), 'name': text[:100],
                            'start': start, 'end': end, 'comments': text, 'source': 'ai_visual'})
        if not outline:
            for index, highlight in enumerate(item.get('highlights', [])):
                start, end = _range(highlight.get('seconds', 0), None, duration)
                text = str(highlight.get('note', ''))
                if text:
                    markers.append({'id': 'highlight:' + str(index), 'name': text[:100], 'start': start,
                                    'end': end, 'comments': text, 'source': 'ai_visual_sample'})
        transcript = item.get('transcript', [])
        if isinstance(transcript, dict):
            transcript = transcript.get('sentences', [])
        transcript = [dict(row) for row in transcript]
        for row in transcript:
            row['start'], row['end'] = _range(row['start'], row.get('end'), duration)
            if not isinstance(row.get('text'), str):
                raise ValueError('Invalid transcript text')
        observations = store.observations(content_id)
        visible = sorted({row['face_id'] for row in store.face_observations(content_id)})
        speakers = sorted({row['person_id'] for row in observations})
        native_transcript, transcript_status = project_transcript(project, item, people=all_people, observations=observations)
        assets.append({'asset_id': asset_id, 'content_id': content_id, 'path': path,
                       'name': str(item.get('name') or Path(path).name), 'relative_path': item['relative_path'], 'duration': duration,
                       'kind': item.get('kind', 'video'), 'tags': tags.get(content_id, []),
                       'notes': notes, 'markers': markers, 'transcript': transcript,
                       # Native words must be provided with actual source timing. No
                       # fake per-word timestamps are synthesized from sentence text.
                       'transcript_json': native_transcript, 'transcript_status': transcript_status,
                       'visible_people': [{'id': f, 'name': faces[f].get('name')} for f in visible if f in faces],
                       'speakers': [{'id': p, 'name': people[p].get('name')} for p in speakers if p in people]})
    return {'project_id': store.project_id, 'project_root': str(project), 'projection_version': PREMIERE_PROJECTION_VERSION, 'assets': sorted(assets, key=lambda a: a['asset_id'])}


class PremiereBridge:
    def __init__(self, project, catalog_provider=None):
        self.project = Path(project).resolve(strict=True)
        self.notes = self.project / '_DIT_NOTES'
        self.exchange = self.notes / 'editorial' / 'bridge'
        self.catalog_provider = catalog_provider
        self._catalog_signature = None
        self._catalog_value = None

    def catalog(self):
        if self.catalog_provider:
            return self.catalog_provider()
        path = self.notes / 'catalog.json'
        stat = path.stat()
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if signature != self._catalog_signature:
            self._catalog_value = json.loads(path.read_text())
            self._catalog_signature = signature
        return self._catalog_value

    def _store(self):
        identity = self.catalog().get('project', {}).get('id')
        if not isinstance(identity, str) or not identity or identity == 'waiting-for-ingest':
            raise ValueError('An indexed project identity is required')
        store = ProjectStore(self.notes / 'project.sqlite3', project_id=identity)
        try:
            with store._transaction():
                store.db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_receipts (receipt_hash TEXT PRIMARY KEY, operation_id TEXT NOT NULL, received_at REAL NOT NULL)')
                receipt_columns = {row['name'] for row in store.db.execute('PRAGMA table_info(premiere_bridge_receipts)')}
                if 'receipt' not in receipt_columns:
                    store.db.execute('ALTER TABLE premiere_bridge_receipts ADD COLUMN receipt TEXT')
                store.db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_events (id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL)')
                store.db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
                store.db.execute('''CREATE TABLE IF NOT EXISTS premiere_bridge_operations (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL UNIQUE,
                    desired_hash TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    receipt TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
                columns = {row['name'] for row in store.db.execute('PRAGMA table_info(premiere_bridge_operations)')}
                if 'operation_kind' not in columns:
                    store.db.execute("ALTER TABLE premiere_bridge_operations ADD COLUMN operation_kind TEXT NOT NULL DEFAULT 'snapshot'")
                if 'request_key' not in columns:
                    store.db.execute('ALTER TABLE premiere_bridge_operations ADD COLUMN request_key TEXT')
                store.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS premiere_bridge_request_key ON premiere_bridge_operations(request_key) WHERE request_key IS NOT NULL')
                store.db.execute('CREATE TABLE IF NOT EXISTS premiere_bridge_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
                if not store.db.execute("SELECT 1 FROM premiere_bridge_meta WHERE key='receipt_history_seeded'").fetchone():
                    # Upgrade an already paired prototype without forgetting its
                    # latest receipts when explicit retry resets an old error.
                    for row in store.db.execute('SELECT operation_id,receipt FROM premiere_bridge_operations WHERE receipt IS NOT NULL'):
                        store.db.execute('INSERT OR IGNORE INTO premiere_bridge_receipts(receipt_hash,operation_id,received_at,receipt) VALUES(?,?,?,?)',
                                         (digest(json.loads(row['receipt'])), row['operation_id'], time.time(), row['receipt']))
                    store.db.execute("INSERT INTO premiere_bridge_meta VALUES('receipt_history_seeded','1')")
                if not store.db.execute("SELECT 1 FROM premiere_bridge_meta WHERE key='receipt_packets_seeded'").fetchone():
                    for row in store.db.execute('SELECT operation_id,receipt FROM premiere_bridge_operations WHERE receipt IS NOT NULL'):
                        packet = row['receipt']
                        # Backfill only an exact, still-known packet whose hash
                        # matches the historical ledger key. Other old hash-only
                        # rows stay NULL: their lost JSON cannot be fabricated.
                        store.db.execute('UPDATE premiere_bridge_receipts SET receipt=? WHERE receipt_hash=? AND receipt IS NULL',
                                         (packet, digest(json.loads(packet))))
                    store.db.execute("INSERT INTO premiere_bridge_meta VALUES('receipt_packets_seeded','1')")
        except BaseException:
            store.close()
            raise
        return store

    @staticmethod
    def _config(store):
        row = store.db.execute('SELECT value FROM premiere_bridge_config WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def prepare(self, native_project_path):
        """Explicit pairing; does not launch/configure Premiere or touch media."""
        path = Path(native_project_path).resolve()
        if path.suffix.lower() != '.prproj' or not path.parent.is_dir():
            raise ValueError('Choose a .prproj path in an existing directory')
        if not path.is_relative_to(self.project):
            raise ValueError('Premiere project must be inside this Field Lab project without symlinks')
        with self._store() as store:
            token = secrets.token_urlsafe(32)
            with store._transaction():
                config = self._config(store)
                if config and config['native_project_path'] != str(path):
                    raise ValueError('Bridge is already paired to another Premiere project')
                config = {'protocol': PROTOCOL, 'project_id': store.project_id,
                          'native_project_path': str(path), 'token_hash': hashlib.sha256(token.encode()).hexdigest()}
                store.db.execute('INSERT OR REPLACE INTO premiere_bridge_config VALUES(1,?)', (canonical(config),))
            return {k: v for k, v in config.items() if k != 'token_hash'} | {'token': token, 'exchange_directory': str(self.exchange)}

    def authorize(self, token):
        with self._store() as store:
            config = self._config(store)
            if not config or not isinstance(token, str) or not hmac.compare_digest(config['token_hash'], hashlib.sha256(token.encode()).hexdigest()):
                raise PermissionError('Premiere bridge pairing is required')
            return {k: v for k, v in config.items() if k != 'token_hash'}

    def queue_snapshot(self):
        """Commit desired state first. Repeat unchanged state is a no-op."""
        catalog = self.catalog()
        with self._store() as store:
            with store._transaction():
                config = self._config(store)
                if not config:
                    raise ValueError('Pair a Premiere project first')
                desired = build_snapshot(self.project, catalog, store)
                desired['native_project_path'] = config['native_project_path']
                desired_hash = digest(desired)
                previous = store.db.execute("SELECT * FROM premiere_bridge_operations WHERE operation_kind='snapshot' ORDER BY revision DESC LIMIT 1").fetchone()
                if previous and previous['desired_hash'] == desired_hash:
                    result = self._operation(previous)
                else:
                    baseline = store.db.execute("SELECT receipt FROM premiere_bridge_operations WHERE status='saved' ORDER BY revision DESC LIMIT 1").fetchone()
                    desired['base_readback'] = json.loads(baseline['receipt']).get('readback') if baseline else None
                    operation_id = 'op_' + uuid.uuid4().hex
                    now = time.time()
                    cursor = store.db.execute('''INSERT INTO premiere_bridge_operations
                        (operation_id,desired_hash,payload,status,created_at,updated_at) VALUES(?,?,?,'queued',?,?)''',
                        (operation_id, desired_hash, canonical(desired), now, now))
                    revision = cursor.lastrowid
                    row = store.db.execute('SELECT * FROM premiere_bridge_operations WHERE revision=?', (revision,)).fetchone()
                    result = self._operation(row)
        self.publish()
        return result

    def queue_command(self, kind, ranges, name=None, request_id=None):
        """Queue bounded native navigation/selects in the SAME durable outbox.

        The assets are resolution/readback evidence only. A command adapter must
        never apply the accompanying metadata snapshot as part of navigation.
        An optional client request ID makes a lost HTTP acknowledgement retryable
        without manufacturing another selects sequence.
        """
        if kind not in ('reveal', 'selects'):
            raise ValueError('Unknown Premiere command')
        if not isinstance(ranges, list) or not ranges or len(ranges) > 200 or (kind == 'reveal' and len(ranges) != 1):
            raise ValueError('Reveal requires one range; selects accepts 1–200 ranges')
        if name is None:
            name = 'Field Lab result' if kind == 'reveal' else 'Field Lab selects'
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
            raise ValueError('Invalid native output name')
        if request_id is not None and (not isinstance(request_id, str) or not request_id or len(request_id) > 200):
            raise ValueError('Invalid command request ID')
        catalog = self.catalog()
        with self._store() as store:
            with store._transaction():
                config = self._config(store)
                if not config:
                    raise ValueError('Pair a Premiere project first')
                prior = store.db.execute("SELECT 1 FROM premiere_bridge_operations WHERE operation_kind='snapshot' AND status='saved' LIMIT 1").fetchone()
                if not prior:
                    raise ValueError('Save the initial Premiere library synchronization before opening results')
                desired = build_snapshot(self.project, catalog, store)
                known = {asset['asset_id']: asset for asset in desired['assets']}
                validated = []
                for selected in ranges:
                    if not isinstance(selected, dict) or set(selected) != {'asset_id', 'start', 'end'}:
                        raise ValueError('A command range requires asset_id, start and end only')
                    asset_id = selected['asset_id']
                    if not isinstance(asset_id, str) or asset_id not in known:
                        raise ValueError('Command asset is not in the project')
                    start, end = _range(selected['start'], selected['end'], known[asset_id]['duration'])
                    if end <= start:
                        raise ValueError('A native result range must have positive duration')
                    validated.append({'asset_id': asset_id, 'start': start, 'end': end})
                command = {'kind': kind, 'ranges': validated, 'name': name.strip()}
                request_key = store.project_id + ':' + request_id if request_id is not None else None
                previous = store.db.execute('SELECT * FROM premiere_bridge_operations WHERE request_key=?', (request_key,)).fetchone() if request_key else None
                if previous:
                    if json.loads(previous['payload']).get('command') != command:
                        raise ValueError('The command request ID was already used for a different request')
                    result = self._operation(previous)
                else:
                    desired['native_project_path'] = config['native_project_path']
                    desired['command'] = command
                    desired_hash = digest(desired)
                    baseline = store.db.execute("SELECT receipt FROM premiere_bridge_operations WHERE status='saved' ORDER BY revision DESC LIMIT 1").fetchone()
                    desired['base_readback'] = json.loads(baseline['receipt']).get('readback') if baseline else None
                    operation_id = 'op_' + uuid.uuid4().hex
                    now = time.time()
                    cursor = store.db.execute("""INSERT INTO premiere_bridge_operations
                        (operation_id,desired_hash,payload,status,created_at,updated_at,operation_kind,request_key)
                        VALUES(?,?,?,'queued',?,?,'command',?)""",
                        (operation_id, desired_hash, canonical(desired), now, now, request_key))
                    result = self._operation(store.db.execute('SELECT * FROM premiere_bridge_operations WHERE revision=?', (cursor.lastrowid,)).fetchone())
        self.publish()
        return result

    @staticmethod
    def _operation(row):
        result = json.loads(row['payload']) | {'protocol': PROTOCOL, 'operation_id': row['operation_id'],
                 'revision': row['revision'], 'desired_hash': row['desired_hash']}
        if row['status'] == 'queued' and row['receipt']:
            previous = json.loads(row['receipt'])
            if previous['status'] in ('conflict', 'error') and isinstance(previous.get('readback'), dict):
                # retry() deliberately retains the failed receipt. The native
                # adapter may repair only its own partial output whose CURRENT
                # state still exactly matches this observed failed state. This
                # evidence is not permission to overwrite subsequent edits.
                # It is delivery context, outside immutable desired/hash state.
                result['previous_attempt_readback'] = previous['readback']
        return result

    def next_operation(self, token, native_project_path):
        config = self.authorize(token)
        if native_project_path != config['native_project_path']:
            raise ValueError('Open the paired Premiere project before syncing')
        with self._store() as store:
            row = store.db.execute("SELECT * FROM premiere_bridge_operations WHERE status NOT IN ('saved','cancelled') ORDER BY revision LIMIT 1").fetchone()
            if not row:
                return None
            if row['status'] in ('conflict', 'error'):
                return None
            return self._operation(row)

    def acknowledge(self, receipt, token=None):
        """An acknowledgement is evidence from the adapter, not inferred save."""
        if token is not None:
            self.authorize(token)
        if not isinstance(receipt, dict) or len(canonical(receipt).encode()) > MAX_RECEIPT_BYTES:
            raise ValueError('Invalid Premiere receipt')
        required = {'protocol', 'project_id', 'operation_id', 'desired_hash', 'native_project_path', 'status'}
        if not required <= receipt.keys() or receipt['protocol'] != PROTOCOL or receipt['status'] not in ('saved', 'applied', 'conflict', 'error'):
            raise ValueError('Invalid Premiere receipt')
        with self._store() as store:
            with store._transaction():
                config = self._config(store)
                if not config or receipt['project_id'] != store.project_id or receipt['native_project_path'] != config['native_project_path']:
                    raise ValueError('Receipt belongs to another project')
                row = store.db.execute('SELECT * FROM premiere_bridge_operations WHERE operation_id=?', (receipt['operation_id'],)).fetchone()
                if not row or row['desired_hash'] != receipt['desired_hash']:
                    raise ValueError('Receipt does not match an operation')
                receipt_hash = digest(receipt)
                seen = store.db.execute('SELECT 1 FROM premiere_bridge_receipts WHERE receipt_hash=?', (receipt_hash,)).fetchone()
                if row['status'] in ('cancelled', 'saved'):
                    # Preserve even a late attempt as evidence without changing
                    # a terminal decision or pretending cancellation is a save.
                    if not seen:
                        store.db.execute('INSERT INTO premiere_bridge_receipts(receipt_hash,operation_id,received_at,receipt) VALUES(?,?,?,?)',
                                         (receipt_hash, receipt['operation_id'], time.time(), canonical(receipt)))
                    return self._status(store)
                earlier = store.db.execute("SELECT 1 FROM premiere_bridge_operations WHERE revision<? AND status NOT IN ('saved','cancelled') LIMIT 1", (row['revision'],)).fetchone()
                if earlier:
                    raise ValueError('An earlier operation is not saved')
                if seen or row['receipt'] == canonical(receipt):
                    # A retry changes queue status, not acknowledgement history.
                    # Delayed file/network delivery of any previously consumed
                    # receipt must not put that retry back into an old error.
                    return self._status(store)
                if receipt['status'] in ('applied', 'saved'):
                    readback = receipt.get('readback')
                    if not isinstance(readback, dict) or not isinstance(readback.get('assets'), list):
                        raise ValueError('Native asset readback is required before acknowledging changes')
                    projected = json.loads(row['payload'])
                    projected_assets = {asset['asset_id']: asset for asset in projected['assets']}
                    expected = {key: asset['path'] for key, asset in projected_assets.items()}
                    actual = readback['assets']
                    if any(not isinstance(asset, dict) for asset in actual):
                        raise ValueError('Invalid native asset readback')
                    ids = [asset.get('asset_id') for asset in actual]
                    if len(ids) != len(expected) or set(ids) != set(expected):
                        raise ValueError('Native readback must account for every projected asset exactly once')
                    if any(asset.get('path') != expected[asset['asset_id']] for asset in actual):
                        raise ValueError('Native readback source path differs from the projected asset')
                    if projected.get('projection_version', 0) >= 3 and not projected.get('command'):
                        for asset in actual:
                            transcript = projected_assets[asset['asset_id']].get('transcript_json')
                            if transcript is None:
                                continue
                            summary = asset.get('transcript_summary')
                            export_hash = asset.get('transcript_export_hash')
                            if asset.get('transcript_verified') is not True or not isinstance(summary, dict) or summary.get('verified') is not True:
                                raise ValueError('Verified native transcript readback is required')
                            if not isinstance(export_hash, str) or len(export_hash) != 64 or any(c not in '0123456789abcdef' for c in export_hash):
                                raise ValueError('Native transcript fingerprint is required')
                            if type(summary.get('word_count')) is not int:
                                raise ValueError('Native transcript word count must be an integer')
                            expected_summary = transcript_summary(transcript)
                            for key in ('word_count', 'first_text', 'last_text'):
                                if summary.get(key) != expected_summary[key]:
                                    raise ValueError('Native transcript word readback differs from projected text')
                            for key in ('first_start', 'last_end'):
                                value = summary.get(key)
                                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value - expected_summary[key]) > .05:
                                    raise ValueError('Native transcript timing readback differs from source timing')

                store.db.execute('INSERT INTO premiere_bridge_receipts(receipt_hash,operation_id,received_at,receipt) VALUES(?,?,?,?)',
                                 (receipt_hash, receipt['operation_id'], time.time(), canonical(receipt)))
                store.db.execute('UPDATE premiere_bridge_operations SET status=?,receipt=?,updated_at=? WHERE operation_id=?',
                                 (receipt['status'], canonical(receipt), time.time(), receipt['operation_id']))
                result = self._status(store)
        self.publish()
        return result

    def retry(self, operation_id):
        """Explicitly retry after the operator resolves a conflict or host error."""
        with self._store() as store:
            with store._transaction():
                row = store.db.execute('SELECT * FROM premiere_bridge_operations WHERE operation_id=?', (operation_id,)).fetchone()
                if not row:
                    raise ValueError('Unknown operation')
                if row['status'] == 'cancelled':
                    raise ValueError('A cancelled request cannot be retried; create a new request')
                if row['status'] != 'saved':
                    store.db.execute("UPDATE premiere_bridge_operations SET status='queued',updated_at=? WHERE operation_id=?", (time.time(), operation_id))
        self.publish()
        return self.status()

    def operation_status(self, operation_id):
        """Authoritative state for one request; absence from pending is not save."""
        with self._store() as store:
            row = store.db.execute('SELECT operation_id,revision,operation_kind,status,receipt FROM premiere_bridge_operations WHERE operation_id=?', (operation_id,)).fetchone()
            if not row:
                raise ValueError('Unknown Premiere operation')
            packet = json.loads(row['receipt']) if row['receipt'] else {}
            message = packet.get('message', '')
            if row['status'] == 'cancelled':
                event = store.db.execute("SELECT payload FROM premiere_bridge_events WHERE operation_id=? AND event_type='cancelled' ORDER BY id DESC LIMIT 1", (operation_id,)).fetchone()
                message = json.loads(event['payload'])['reason'] if event else 'Request cancelled; Premiere changes kept'
            return {'operation_id': row['operation_id'], 'revision': row['revision'],
                    'operation_kind': row['operation_kind'], 'status': row['status'], 'message': message}

    def receipt_history(self, operation_id):
        """Read immutable attempt packets; unavailable legacy packets are honest."""
        with self._store() as store:
            if not store.db.execute('SELECT 1 FROM premiere_bridge_operations WHERE operation_id=?', (operation_id,)).fetchone():
                raise ValueError('Unknown operation')
            return [{'receipt_hash': row['receipt_hash'], 'operation_id': row['operation_id'],
                     'received_at': row['received_at'], 'available': row['receipt'] is not None,
                     'receipt': json.loads(row['receipt']) if row['receipt'] is not None else None}
                    for row in store.db.execute('SELECT * FROM premiere_bridge_receipts WHERE operation_id=? ORDER BY received_at,receipt_hash', (operation_id,))]

    def cancel(self, operation_id, reason):
        """Stop a command without deleting or reverting any Premiere changes."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('A cancellation reason is required')
        with self._store() as store:
            with store._transaction():
                row = store.db.execute('SELECT * FROM premiere_bridge_operations WHERE operation_id=?', (operation_id,)).fetchone()
                if not row or row['operation_kind'] != 'command':
                    raise ValueError('Only a navigation/selects command can be cancelled')
                if row['status'] == 'cancelled':
                    return self._status(store)
                if row['status'] not in ('queued', 'error', 'conflict'):
                    raise ValueError('Only a queued or failed command can be cancelled')
                now = time.time()
                event = {'operation_id': operation_id, 'reason': reason.strip(), 'cancelled_at': now,
                         'native_changes': 'Retained; review any partial Premiere output'}
                store.db.execute("INSERT INTO premiere_bridge_events(operation_id,event_type,payload,created_at) VALUES(?,'cancelled',?,?)",
                                 (operation_id, canonical(event), now))
                store.db.execute("UPDATE premiere_bridge_operations SET status='cancelled',updated_at=? WHERE operation_id=?", (now, operation_id))
        self.publish()
        return self.status()

    @staticmethod
    def _status(store):
        config = PremiereBridge._config(store)
        rows = store.db.execute('SELECT revision,operation_id,status,receipt,operation_kind,updated_at FROM premiere_bridge_operations ORDER BY revision').fetchall()
        saved = max((r['revision'] for r in rows if r['status'] == 'saved'), default=0)
        applied = max((r['revision'] for r in rows if r['status'] in ('saved', 'applied')), default=0)
        pending = [{'revision': r['revision'], 'operation_id': r['operation_id'], 'status': r['status'], 'operation_kind': r['operation_kind'],
                    'message': (json.loads(r['receipt']) if r['receipt'] else {}).get('message', '')} for r in rows if r['status'] not in ('saved', 'cancelled')]
        snapshot = store.db.execute("SELECT revision,payload,updated_at FROM premiere_bridge_operations WHERE operation_kind='snapshot' AND status='saved' ORDER BY revision DESC LIMIT 1").fetchone()
        summary = None
        if snapshot:
            assets = json.loads(snapshot['payload'])['assets']
            summary = {'clips': len(assets), 'tag_bins': len({tag['id'] for asset in assets for tag in asset['tags']}),
                       'saved_at': snapshot['updated_at'], 'revision': snapshot['revision']}
        latest_receipt = next((json.loads(r['receipt']) for r in reversed(rows) if r['receipt']), None)
        diagnostic = {key: latest_receipt[key] for key in ('operation_id', 'status', 'message', 'readback', 'plugin_build') if key in latest_receipt} if latest_receipt else None
        cancellation = store.db.execute("SELECT payload FROM premiere_bridge_events WHERE event_type='cancelled' ORDER BY id DESC LIMIT 1").fetchone()
        cancel_event = json.loads(cancellation['payload']) if cancellation else None
        return {'protocol': PROTOCOL, 'project_id': store.project_id, 'paired': bool(config), 'last_receipt': diagnostic, 'snapshot_summary': summary, 'cancel_event': cancel_event,
                'native_project_path': config['native_project_path'] if config else None,
                'exchange_directory': str(store.path.parent / 'editorial' / 'bridge') if config else None,
                'desired_revision': max((r['revision'] for r in rows), default=0),
                'applied_revision': applied, 'saved_revision': saved, 'pending': pending,
                'last_saved_at': max((r['updated_at'] for r in rows if r['status'] == 'saved'), default=None),
                'state': pending[0]['status'] if pending else ('cancelled' if rows and rows[-1]['status'] == 'cancelled' else 'saved' if saved else 'unpaired' if not config else 'ready')}

    def status(self):
        with self._store() as store:
            store.db.execute('BEGIN')
            try:
                return self._status(store)
            finally:
                store.db.rollback()

    def publish(self):
        """Reconcile replayable journal files at every startup/poll after failure."""
        with self._store() as store:
            with store._transaction():
                config = self._config(store)
                if not config:
                    return
                for parent in (self.notes, self.notes / 'editorial', self.exchange):
                    if parent.is_symlink():
                        raise ValueError('Bridge exchange directory cannot be a symlink')
                row = store.db.execute("SELECT * FROM premiere_bridge_operations WHERE status NOT IN ('saved','cancelled') ORDER BY revision LIMIT 1").fetchone()
                payload = self._operation(row) if row and row['status'] in ('queued', 'applied') else None
                # Serialize publishers with queue/ack writers so an old snapshot
                # cannot replace a newer exchange projection after a fast save.
                atomic_json(self.exchange / 'request.json', payload)
                atomic_json(self.exchange / 'status.json', self._status(store))

    def reconcile(self):
        """Import an atomic plugin receipt, then recreate missing exchange output."""
        receipt = self.exchange / 'receipt.json'
        if receipt.is_file():
            if receipt.is_symlink() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
                raise ValueError('Invalid receipt file')
            self.acknowledge(json.loads(receipt.read_text()))
        self.publish()
        return self.status()


class PremiereJournalWorker:
    """A deterministic journal reconciler; only explicitly paired projects run.

    Close is bounded. An in-flight SQLite transaction can finish after close,
    but no next polling cycle starts. Its normal transaction is interruption-safe.
    The worker never configures Premiere, pairs a project, or invokes a model.
    """
    def __init__(self, project, catalog_provider=None, interval=2.0):
        self.bridge = PremiereBridge(project, catalog_provider)
        self.interval = max(.05, float(interval))
        self.stop = threading.Event()
        self.thread = None
        self.error = None
        self.last_checked_at = None
        self.next_check_at = None
        self._source_signature = None
        self._receipt_signature = object()

    @staticmethod
    def _file_signature(path):
        try:
            value = path.stat()
            return (value.st_ino, value.st_size, value.st_mtime_ns)
        except FileNotFoundError:
            return None

    def _input_signature(self):
        catalog = self.bridge.catalog()
        with self.bridge._store() as store:
            revision = store.edit_revision
        files = []
        for asset in catalog.get('assets', []):
            content = str(asset.get('content_id') or asset.get('id'))
            url = asset.get('result_url')
            if isinstance(url, str) and url.startswith('/notes/.objects/') and '..' not in Path(url).parts:
                files.append(self._file_signature(self.bridge.notes / unquote(url.removeprefix('/notes/'))))
            files.append(self._file_signature(self.bridge.notes / (content + '-identity-alignment.json')))
        # Published catalogs have a generation; arbitrary providers must either
        # publish it or accept hashing their much smaller sentence-only catalog.
        provider = catalog.get('catalog_revision') if self.bridge.catalog_provider else self.bridge._catalog_signature
        if self.bridge.catalog_provider and provider is None:
            provider = digest(catalog)
        return (PREMIERE_PROJECTION_VERSION, revision, provider, tuple(files))

    def tick(self):
        if self.stop.is_set():
            return
        # Do not manufacture metadata storage for an unindexed project.
        if not (self.bridge.notes / 'project.sqlite3').is_file():
            return
        if not self.bridge.status()['paired'] or self.stop.is_set():
            return
        receipt_signature = self._file_signature(self.bridge.exchange / 'receipt.json')
        missing_projection = not all((self.bridge.exchange / name).is_file() for name in ('request.json', 'status.json'))
        if receipt_signature != self._receipt_signature or missing_projection:
            self.bridge.reconcile()
            self._receipt_signature = receipt_signature
        if self.stop.is_set():
            return
        signature = self._input_signature()
        if signature != self._source_signature:
            self.bridge.queue_snapshot()
            self._source_signature = signature
        self.error = None

    def _loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as error:
                # The durable outbox/metadata remain intact; retry next cycle.
                # Surface diagnostics through worker.status rather than logging
                # private metadata or marking an unsuccessful save as complete.
                self.error = str(error)
            self.last_checked_at = time.time()
            self.next_check_at = self.last_checked_at + self.interval
            self.stop.wait(self.interval)

    def start(self):
        if self.thread is not None:
            return self
        self.thread = threading.Thread(target=self._loop, name='premiere-journal', daemon=True)
        self.thread.start()
        return self

    def close(self, timeout=3.0):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=max(0, float(timeout)))

    def status(self):
        return {'running': bool(self.thread and self.thread.is_alive() and not self.stop.is_set()),
                'error': self.error, 'last_checked_at': self.last_checked_at,
                'next_check_at': self.next_check_at if not self.stop.is_set() else None, 'interval_seconds': self.interval}
