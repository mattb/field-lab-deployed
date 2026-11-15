"""Durable multi-primary shoot ownership and cutover intent.

The registry never copies footage or edits OffShoot/CCC settings. External app
configuration/readback belongs to the cutover coordinator. Reconnecting a volume
never changes the explicit active-primary pointer.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import plistlib
import re
import time
import tempfile
import uuid
import unicodedata

from .atomic import atomic_json
from .backup_volumes import mac_identity

SCHEMA = 1
CUTOVER_STAGES = ('quiescent', 'destination_prepared', 'offshoot_configured',
                  'field_configured', 'backup_bound', 'accepted')
RESERVED_CONFIG = ('volume_uuid', 'volume_mount', 'ingest_folder', 'ingest_root')
ACCEPTANCE_CHECKS = ('offshoot_destination_observed', 'verified_ingest', 'ai_index_checked',
                     'backup_copy_verified', 'restore_checked')


def _validate_acceptance(state, pending, evidence):
    profile = state['profiles'][pending['to_primary']]
    backup = profile['backup']
    label = backup['current_set']
    current = backup['sets'].get(label)
    bound = pending['steps'].get('backup_bound', {})
    observed = evidence.get('observed_at')
    artifacts = evidence.get('artifacts')
    prior_times = [pending['steps'].get(stage, {}).get('observed_at') for stage in CUTOVER_STAGES[:-1]]
    if (not current or current.get('needs_review') or
            evidence.get('kind') != 'acceptance' or
            evidence.get('method') != 'operator-observed-acceptance' or
            evidence.get('primary_id') != profile['id'] or
            evidence.get('volume_uuid') != profile['volume_uuid'] or
            evidence.get('task_uuid') != current['task_uuid'] or
            bound.get('task_uuid') != current['task_uuid'] or bound.get('set_label') != label or
            type(observed) not in (int, float) or not math.isfinite(observed) or
            not pending['created_at'] <= observed <= time.time() or
            any(type(value) not in (int, float) or not math.isfinite(value) or
                not pending['created_at'] <= value <= observed for value in prior_times) or
            any(evidence.get(key) is not True for key in ACCEPTANCE_CHECKS) or
            not isinstance(artifacts, list) or not artifacts or
            any(not isinstance(item, (str, dict)) or not item for item in artifacts)):
        raise ValueError('Fresh target-specific ingest/index/CCC/restore acceptance evidence is required')


def _accepted_target(state, primary_id):
    pending = state.get('pending_cutover')
    return bool(pending and pending['to_primary'] == primary_id and 'accepted' in pending['steps'])


def _safe(path, *, exists=True):
    path = Path(path).expanduser().absolute()
    if '..' in path.parts or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Path must not use traversal or symlinks')
    if exists and not path.exists():
        raise ValueError('Required path is unavailable: ' + str(path))
    return path.resolve(strict=exists)


def _relative(value):
    path = Path(value)
    if path.is_absolute() or not path.parts or '..' in path.parts or any(p.startswith('.') for p in path.parts):
        raise ValueError('Expected a non-hidden relative project/destination folder')
    return path.as_posix()


def _identity(probe, mount):
    mount = _safe(mount)
    if not mount.is_dir():
        raise ValueError('Volume mount must be an existing directory')
    info = probe(mount)
    volume_uuid = str(info.get('uuid', '')).strip().upper()
    actual_mount = str(_safe(info.get('mount', '')))
    if not volume_uuid or actual_mount != str(mount):
        raise ValueError('Expected the actual mounted volume with a stable UUID')
    if not isinstance(info.get('physical'), list) or not info['physical']:
        raise ValueError('Physical backing identity is required')
    return {**info, 'uuid': volume_uuid, 'mount': actual_mount}


class ShootRegistry:
    def __init__(self, home, probe=mac_identity, checkpoint=None):
        self.home = _safe(home)
        if not self.home.is_dir():
            raise ValueError('Application home must exist')
        self.shared = _safe(self.home / 'shared', exists=False)
        self.shared.mkdir(exist_ok=True)
        self.path = self.shared / 'shoot.json'
        self.probe = probe
        self.checkpoint = checkpoint or (lambda _phase: None)

    @contextmanager
    def _locked(self):
        descriptor = os.open(self.shared / 'shoot.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'a+') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('Another primary-profile operation is running') from None
            yield

    def _load(self):
        _safe(self.path)
        value = json.loads(self.path.read_text())
        if not isinstance(value, dict) or value.get('schema') != SCHEMA or not isinstance(value.get('revision'),int) or value['revision'] < 0:
            raise ValueError('Unsupported shoot registry; preserve it for recovery')
        profiles = value.get('profiles')
        if not isinstance(profiles, dict) or not profiles or value.get('active_primary') not in profiles:
            raise ValueError('Invalid shoot primary registry')
        uuids = set()
        tasks = set()
        for identity, profile in profiles.items():
            if not re.fullmatch(r'[a-f0-9]{32}', identity) or profile.get('id') != identity:
                raise ValueError('Invalid stable primary ID')
            volume_uuid = profile.get('volume_uuid')
            if not isinstance(volume_uuid, str) or not volume_uuid or volume_uuid in uuids:
                raise ValueError('Duplicate/invalid primary volume UUID')
            uuids.add(volume_uuid)
            folder = _relative(profile['ingest_folder'])
            mount = _safe(profile['volume_mount'], exists=False)
            if str(mount / folder) != profile['project_root']:
                raise ValueError('Primary project root does not match volume/folder')
            aliases = profile.get('previous_roots', [])
            if not isinstance(aliases, list) or len(set(aliases)) != len(aliases):
                raise ValueError('Invalid registered root aliases')
            for alias in aliases:
                if not isinstance(alias, str) or Path(alias).parts[-len(Path(folder).parts):] != Path(folder).parts:
                    raise ValueError('Invalid registered root alias')
                _safe(alias, exists=False)
            backup = profile.get('backup', {})
            sets = backup.get('sets', {})
            if not isinstance(sets, dict) or backup.get('current_set') is not None and backup['current_set'] not in sets:
                raise ValueError('Invalid current backup set')
            for label, record in sets.items():
                if not re.fullmatch(r'B[0-9]{3,}', label):
                    raise ValueError('Invalid backup set label')
                task = str(uuid.UUID(record['task_uuid'])).upper()
                if task in tasks:
                    raise ValueError('A CCC task cannot belong to multiple primary sets')
                tasks.add(task)
        pending = value.get('pending_cutover')
        if pending:
            if pending.get('from_primary') not in profiles or pending.get('to_primary') not in profiles or pending['from_primary'] == pending['to_primary']:
                raise ValueError('Invalid pending cutover')
            if pending['from_primary'] != value['active_primary']:
                raise ValueError('Pending cutover conflicts with active primary')
        return value

    def read(self):
        return copy.deepcopy(self._load())

    @property
    def state(self):
        return self.read()

    snapshot = read

    def _save(self, state, phase):
        state['revision'] = state.get('revision', 0) + 1
        state['updated_at'] = time.time()
        descriptor, temporary = tempfile.mkstemp(prefix='.shoot-', dir=self.shared)
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(state, stream, indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            descriptor = os.open(self.shared, os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
        self.checkpoint(phase)

    def _profile(self, info, folder, label):
        folder = _relative(folder)
        root = _safe(Path(info['mount']) / folder, exists=False)
        return {'id': uuid.uuid4().hex, 'label': str(label or Path(info['mount']).name),
                'volume_uuid': info['uuid'], 'volume_mount': info['mount'],
                'ingest_folder': folder, 'project_root': str(root),
                'physical_identity': info['physical'], 'previous_roots': [],
                'backup': {'current_set': None, 'sets': {}}, 'registered_at': time.time()}

    def initialize(self, config=None, name='Shoot'):
        if config is None:
            config = json.loads(_safe(self.shared / 'config.json').read_text())
        info = _identity(self.probe, config['volume_mount'])
        if info['uuid'] != str(config['volume_uuid']).upper():
            raise ValueError('Configured primary UUID does not match mounted volume')
        profile = self._profile(info, config['ingest_folder'], config.get('volume_name'))
        if config.get('ingest_root') and profile['project_root'] != str(_safe(config['ingest_root'], exists=False)):
            raise ValueError('Configured project root differs from volume/folder')
        with self._locked():
            if self.path.exists():
                current = self._load()
                matches = [p for p in current['profiles'].values() if p['volume_uuid'] == info['uuid'] and p['project_root'] == profile['project_root']]
                if not matches:
                    raise ValueError('Existing shoot differs; refusing reinitialization')
                return copy.deepcopy(current)
            state = {'schema': SCHEMA, 'shoot_id': uuid.uuid4().hex, 'name': str(name),
                     'active_primary': profile['id'], 'profiles': {profile['id']: profile},
                     'pending_cutover': None, 'revision': 0, 'history': [], 'created_at': time.time()}
            self._save(state, 'initialized')
            return copy.deepcopy(state)

    def register_primary(self, volume, ingest_folder='Ingests', label=None):
        info = _identity(self.probe, volume)
        profile = self._profile(info, ingest_folder, label)
        with self._locked():
            state = self._load()
            matches = [p for p in state['profiles'].values() if p['volume_uuid'] == info['uuid']]
            if matches:
                if matches[0]['project_root'] != profile['project_root']:
                    raise ValueError('Volume already registered with another project root; reconcile first')
                return copy.deepcopy(matches[0])
            state['profiles'][profile['id']] = profile
            self._save(state, 'primary_registered')
            return copy.deepcopy(profile)

    def ledger_path(self, primary_id):
        if primary_id not in self._load()['profiles']:
            raise ValueError('Unknown primary')
        return self.shared / 'runtime/backup-date-rotation' / primary_id / 'assignments.json'

    def profile_identity(self, primary_id, mount=None):
        profile = self._load()['profiles'][primary_id]
        info = _identity(self.probe, mount or profile['volume_mount'])
        if info['uuid'] != profile['volume_uuid']:
            raise ValueError('Mounted volume UUID differs from registered primary')
        return info

    def availability(self, primary_id, candidate_mounts=None):
        profile = self._load()['profiles'][primary_id]
        paths = [Path(profile['volume_mount'])]
        if candidate_mounts is not None:
            paths.extend(Path(path) for path in candidate_mounts)
        for path in dict.fromkeys(paths):
            try:
                info = _identity(self.probe, path)
                if info['uuid'] == profile['volume_uuid']:
                    return {'online': True, 'volume_mount': info['mount'],
                            'project_root': str(Path(info['mount']) / profile['ingest_folder']),
                            'volume_uuid': info['uuid']}
            except (OSError, ValueError, RuntimeError):
                pass
        return {'online': False, 'volume_uuid': profile['volume_uuid'], 'project_root': profile['project_root']}

    def refresh_mount(self, primary_id, mount):
        info = self.profile_identity(primary_id, mount)
        with self._locked():
            state = self._load()
            if state.get('pending_cutover'):
                raise ValueError('Reconcile pending cutover before changing profile paths')
            profile = state['profiles'][primary_id]
            old_root = profile['project_root']
            if primary_id == state['active_primary'] and old_root != str(Path(info['mount']) / profile['ingest_folder']):
                raise ValueError('Active primary mount changes require coordinated configuration reconciliation')
            profile.update(volume_mount=info['mount'], project_root=str(Path(info['mount']) / profile['ingest_folder']))
            if old_root != profile['project_root']:
                for record in profile['backup']['sets'].values():
                    record['needs_review'] = True
                profile['previous_roots'] = list(dict.fromkeys(profile.get('previous_roots', []) + [old_root]))
            self._save(state, 'mount_refreshed')
            return copy.deepcopy(profile)

    def register_backup(self, primary_id, label, record, reviewed=False):
        if not reviewed or not isinstance(record.get('configuration_evidence'), dict):
            raise ValueError('Actual supported-UI configuration readback evidence is required')
        if not re.fullmatch(r'B[0-9]{3,}', label):
            raise ValueError('Expected backup label B001 etc')
        task = str(uuid.UUID(record['task_uuid'])).upper()
        evidence = record['configuration_evidence']
        with self._locked():
            state = self._load()
            profile = state['profiles'][primary_id]
            if _accepted_target(state, primary_id):
                raise ValueError('Accepted cutover freezes target backup configuration until completion')
            info = _identity(self.probe, record['destination_mount'])
            if info['uuid'] != str(record['destination_uuid']).upper():
                raise ValueError('Backup disk UUID mismatch')
            if info['uuid'] in {p['volume_uuid'] for p in state['profiles'].values()}:
                raise ValueError('A shoot primary cannot be its own independent backup')
            primary = self.profile_identity(primary_id)
            if set(primary['physical']).intersection(info['physical']):
                raise ValueError('Backup must be on independent physical storage')
            relative = _relative(record['destination_relative'])
            destination = str(_safe(Path(info['mount']) / relative, exists=False))
            if record.get('source') != profile['project_root']:
                raise ValueError('CCC must read this primary original project root')
            if evidence.get('task_uuid', '').upper() != task or evidence.get('source') != profile['project_root'] or evidence.get('destination') != destination or not evidence.get('observed_at'):
                raise ValueError('Configuration evidence does not match actual task endpoints')
            if evidence.get('method') not in ('supported-ui', 'supported-api-readback'):
                raise ValueError('Unsupported configuration evidence method')
            for other_id, other in state['profiles'].items():
                for other_label, existing in other['backup']['sets'].items():
                    if other_id == primary_id and other_label == label:continue
                    if existing['task_uuid'] == task:
                        raise ValueError('CCC task already belongs to another primary/set')
                    if existing.get('destination_uuid') == info['uuid']:
                        old_relative=tuple(unicodedata.normalize('NFD',part).casefold() for part in Path(existing['destination_relative']).parts)
                        new_relative=tuple(unicodedata.normalize('NFD',part).casefold() for part in Path(relative).parts)
                        if old_relative[:len(new_relative)]==new_relative or new_relative[:len(old_relative)]==old_relative:
                            raise ValueError('Backup destinations overlap on the same physical volume')
            value = {**copy.deepcopy(record), 'task_uuid': task,
                     'destination_uuid': info['uuid'], 'destination_mount': info['mount'],
                     'destination_relative': relative, 'destination': destination,
                     'physical_identity': info['physical'], 'needs_review': False}
            existing = profile['backup']['sets'].get(label)
            if existing:
                stable = ('task_uuid', 'destination_uuid', 'destination_relative')
                allowed_sources = {profile['project_root'], *profile.get('previous_roots', [])}
                if any(existing.get(key) != value.get(key) for key in stable) or existing.get('source') not in allowed_sources:
                    raise ValueError('Backup set identity is immutable; register a new set')
                value = {**existing, **value}
            profile['backup']['sets'][label] = value
            self._save(state, 'backup_registered')
            return copy.deepcopy(value)

    def set_current_backup(self, primary_id, label):
        with self._locked():
            state = self._load()
            profile = state['profiles'][primary_id]
            record = profile['backup']['sets'][label]
            if _accepted_target(state, primary_id) and label != profile['backup']['current_set']:
                raise ValueError('Accepted cutover freezes target backup selection until completion')
            if record.get('needs_review'):
                raise ValueError('Backup task needs source-path review')
            profile['backup']['current_set'] = label
            self._save(state, 'backup_selected')
            return copy.deepcopy(record)

    def target_config(self, existing_config=None):
        state = self._load()
        pending = state.get('pending_cutover')
        if not pending:
            raise ValueError('No pending primary cutover')
        profile = state['profiles'][pending['to_primary']]
        value = copy.deepcopy(existing_config if existing_config is not None else pending['before_config'])
        value.update(volume_uuid=profile['volume_uuid'], volume_mount=profile['volume_mount'],
                     ingest_folder=profile['ingest_folder'], ingest_root=profile['project_root'],
                     primary_id=profile['id'], active_primary_id=profile['id'],
                     shoot_id=state['shoot_id'], shoot_registry=str(self.path))
        return value

    def begin_cutover(self, target_id, existing_config=None):
        with self._locked():
            state = self._load()
            if target_id not in state['profiles']:
                raise ValueError('Unknown target primary')
            if state.get('pending_cutover'):
                if state['pending_cutover']['to_primary'] != target_id:
                    raise ValueError('Another cutover is pending')
                return copy.deepcopy(state['pending_cutover'])
            if target_id == state['active_primary']:
                return {'status': 'already_active', 'to_primary': target_id}
            self.profile_identity(target_id)
            before = copy.deepcopy(existing_config if existing_config is not None else json.loads(_safe(self.shared / 'config.json').read_text()))
            old = state['profiles'][state['active_primary']]
            if str(before.get('volume_uuid', '')).upper() != old['volume_uuid'] or before.get('ingest_root') != old['project_root']:
                raise ValueError('Actual configuration differs from active primary; reconcile first')
            pointer = self.shared / 'runtime/backup-shortcut/current.plist'
            backup = plistlib.loads(_safe(pointer).read_bytes()) if pointer.exists() else None
            pending = {'id': uuid.uuid4().hex, 'from_primary': state['active_primary'],
                       'to_primary': target_id, 'steps': {}, 'created_at': time.time(),
                       'before_config': before, 'before_backup': backup,
                       'required_stages': list(CUTOVER_STAGES),
                       'recovery': 'Read actual app/config/task state; resume matching stages. Never assume an unrecorded external operation failed.'}
            state['pending_cutover'] = pending
            self._save(state, 'cutover_started')
            return copy.deepcopy(pending)

    def record_cutover(self, stage, evidence):
        if stage not in CUTOVER_STAGES:
            raise ValueError('Unknown cutover stage')
        if not isinstance(evidence, dict) or not evidence.get('observed_at') or evidence.get('kind') not in ('readback', 'acceptance'):
            raise ValueError('Observed readback/acceptance evidence is required')
        with self._locked():
            state = self._load()
            pending = state.get('pending_cutover')
            if not pending:
                raise ValueError('No pending cutover')
            if 'accepted' in pending['steps']:
                if evidence == pending['steps'].get(stage):
                    return copy.deepcopy(pending)
                raise ValueError('Accepted cutover stages are frozen until completion')
            if evidence.get('primary_id') != pending['to_primary']:
                raise ValueError('Evidence belongs to another primary')
            index = CUTOVER_STAGES.index(stage)
            if any(prior not in pending['steps'] for prior in CUTOVER_STAGES[:index]):
                raise ValueError('Earlier cutover stages need reconciliation')
            if stage == 'accepted':
                _validate_acceptance(state, pending, evidence)
            pending['steps'][stage] = copy.deepcopy(evidence)
            self._save(state, 'cutover_' + stage)
            return copy.deepcopy(pending)

    def complete_cutover(self, observed_config, observed_backup):
        with self._locked():
            state = self._load()
            pending = state.get('pending_cutover')
            if pending and any(stage not in pending['steps'] for stage in CUTOVER_STAGES):
                raise ValueError('Cutover acceptance/readback is incomplete')
            target_id = pending['to_primary'] if pending else state['active_primary']
            profile = state['profiles'][target_id]
            if pending:
                _validate_acceptance(state, pending, pending['steps']['accepted'])
            self.profile_identity(profile['id'])
            if pending:
                target = self.target_config()
            else:
                # An interrupted caller may resume after the atomic completion.
                target = {**observed_config, 'volume_uuid':profile['volume_uuid'],
                          'volume_mount':profile['volume_mount'], 'ingest_folder':profile['ingest_folder'],
                          'ingest_root':profile['project_root'], 'primary_id':profile['id'],
                          'active_primary_id':profile['id'], 'shoot_id':state['shoot_id'],
                          'shoot_registry':str(self.path)}
            keys = (*RESERVED_CONFIG, 'primary_id', 'active_primary_id', 'shoot_id', 'shoot_registry')
            actual_config = json.loads(_safe(self.shared / 'config.json').read_text())
            if any(observed_config.get(key) != target.get(key) or actual_config.get(key) != target.get(key) for key in keys):
                raise ValueError('Actual Field Lab configuration does not match target')
            backup = profile['backup']
            current = backup['sets'].get(backup['current_set'])
            pointer = self.shared / 'runtime/backup-shortcut/current.plist'
            actual_backup = plistlib.loads(_safe(pointer).read_bytes()) if pointer.exists() else None
            expected_binding = {'primary_id':profile['id'], 'source_mount':profile['volume_mount'],
                                'source_uuid':profile['volume_uuid'], 'mode':'start'}
            if current:
                expected_binding.update(task_uuid=current['task_uuid'], destination_mount=current['destination_mount'],
                                        destination_uuid=current['destination_uuid'])
            if not current or current.get('needs_review') or not observed_backup or not actual_backup or any(
                    any(value.get(key) != expected for key, expected in expected_binding.items())
                    for value in (observed_backup, actual_backup)):
                raise ValueError('Actual Backup binding does not match target current set')
            if not pending:
                return copy.deepcopy(state)
            state['active_primary'] = profile['id']
            state['history'].append({**copy.deepcopy(pending), 'completed_at': time.time()})
            state['pending_cutover'] = None
            self._save(state, 'cutover_completed')
            return copy.deepcopy(state)

    def cancel_cutover(self, observed_config, external_unchanged_evidence=None):
        with self._locked():
            state = self._load()
            pending = state.get('pending_cutover')
            if not pending:
                return {'cancelled': False}
            evidence = external_unchanged_evidence or {}
            if evidence.get('kind') != 'readback' or not isinstance(evidence.get('observed_at'),(int,float)) or evidence['observed_at'] < pending['created_at'] or evidence.get('offshoot_project_root') != pending['before_config']['ingest_root'] or evidence.get('backup_task_uuid') != (pending.get('before_backup') or {}).get('task_uuid'):
                raise ValueError('Fresh OffShoot and Backup before-state readback is required to cancel')
            if any(stage in pending['steps'] for stage in CUTOVER_STAGES[1:]):
                raise ValueError('External steps may have occurred; reconcile instead of cancelling')
            actual_config=json.loads(_safe(self.shared/'config.json').read_text())
            pointer=self.shared/'runtime/backup-shortcut/current.plist'
            actual_backup=plistlib.loads(_safe(pointer).read_bytes()) if pointer.exists() else None
            if actual_backup != pending.get('before_backup'):
                raise ValueError('Actual Backup pointer already changed; reconcile instead of cancelling')
            keys=(*RESERVED_CONFIG,'primary_id','active_primary_id','shoot_id','shoot_registry')
            if any(observed_config.get(key) != pending['before_config'].get(key) or actual_config.get(key) != pending['before_config'].get(key) for key in keys):
                raise ValueError('Configuration already changed; reconcile instead of cancelling')
            state['history'].append({**copy.deepcopy(pending), 'cancelled_at': time.time()})
            state['pending_cutover'] = None
            self._save(state, 'cutover_cancelled')
            return {'cancelled': True}
