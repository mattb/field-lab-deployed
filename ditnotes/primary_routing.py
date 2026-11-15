"""Resolve trusted shoot profiles without changing the active ingest pointer.

Queue payloads retain their original UUID/root/path. Reconnection may rebase a
known job's event path under the same UUID, never under the current primary.
"""
import copy
import json
from pathlib import Path
from .ingest_events import mac_volume_info

class OfflinePrimary(RuntimeError):pass
class CutoverPending(RuntimeError):pass
class UnknownPrimary(ValueError):pass


def registry_path(root,config):
    explicit=config.get('shoot_registry')
    return Path(explicit) if explicit else (Path(root)/'config.json').resolve().parent/'shoot.json'


def read_registry(root,config):
    path=registry_path(root,config)
    if path.is_symlink():raise ValueError('Shoot registry is a symlink')
    if not path.exists():
        if config.get('shoot_registry'):raise ValueError('Configured shoot registry is missing; preserve configuration for recovery')
        return None
    state=json.loads(path.read_text())
    if not isinstance(state,dict) or state.get('schema')!=1 or not isinstance(state.get('profiles'),dict) or state.get('active_primary') not in state['profiles']:
        raise ValueError('Invalid shoot registry; preserve it for recovery')
    return state


def ensure_not_cutting_over(root,config,validation_intent=None):
    state=read_registry(root,config)
    if state and state.get('pending_cutover'):
        pending=state['pending_cutover'];target=state['profiles'][pending['to_primary']]
        if validation_intent!=pending['id'] or 'backup_bound' not in pending['steps'] or config.get('ingest_root')!=target['project_root'] or str(config.get('volume_uuid','')).upper()!=target['volume_uuid']:
            raise CutoverPending('Primary cutover is pending; use its explicit validation command or reconcile it')
        config['_cutover_validation']=pending['id']
        return state
    if state:
        active=state['profiles'][state['active_primary']]
        if str(config.get('volume_uuid','')).upper()!=str(active['volume_uuid']).upper() or config.get('ingest_root')!=active['project_root']:
            raise CutoverPending('Active primary and Field Lab configuration differ; reconcile cutover before starting')
    return state


def config_for_profile(config,profile):
    mount=Path(profile['volume_mount']);folder=Path(profile['ingest_folder']);project=Path(profile['project_root'])
    if not mount.is_absolute() or folder.is_absolute() or '..' in folder.parts or project!=mount/folder or project==mount:
        raise ValueError('Invalid registered project scope')
    return {**config,'volume_uuid':profile['volume_uuid'],'volume_mount':str(mount),
            'ingest_folder':str(folder),'ingest_root':str(project),'primary_id':profile['id'],
            '_profile_roots':[str(project),*profile.get('previous_roots',[])]}


def available(config,probe=mac_volume_info):
    mount=Path(config['volume_mount']);root=Path(config['ingest_root'])
    if not mount.is_dir() or not root.is_dir():return False
    if any(p.is_symlink() for p in [root,*root.parents] if p==mount or mount in p.parents):return False
    try:
        for path in (mount,root):
            info=probe(path)
            if str(info.get('VolumeUUID','')).upper()!=str(config['volume_uuid']).upper() or Path(info.get('MountPoint','')).resolve()!=mount.resolve():return False
        return root.stat().st_dev==mount.stat().st_dev
    except (OSError,ValueError,RuntimeError):return False


def profile_configs(root,config,*,probe=mac_volume_info):
    state=read_registry(root,config)
    if state is None:return [(None,config)]
    profiles=state['profiles']
    if state.get('pending_cutover') and config.get('_cutover_validation')==state['pending_cutover']['id']:
        identity=state['pending_cutover']['to_primary'];profiles={identity:profiles[identity]}
    return [(identity,config_for_profile(config,profile)) for identity,profile in profiles.items()]


def for_payload(root,config,payload,*,probe=mac_volume_info):
    state=read_registry(root,config)
    if state is None:return config
    if state.get('pending_cutover') and config.get('_cutover_validation')!=state['pending_cutover']['id']:raise CutoverPending('Primary cutover pending')
    candidates=[profile for profile in state['profiles'].values() if str(profile['volume_uuid']).upper()==str(payload.get('volume_uuid','')).upper()]
    if len(candidates)!=1:raise UnknownPrimary('Queued ingest is not owned by a registered primary')
    profile=candidates[0]
    if state.get('pending_cutover') and profile['id']!=state['pending_cutover']['to_primary']:raise OfflinePrimary('Validation processes only the pending target primary')
    result=config_for_profile(config,profile)
    old_root=Path(payload.get('ingest_root',''));old_destination=Path(payload.get('destination',''))
    if not old_root.is_absolute() or not old_destination.is_absolute() or '..' in old_root.parts or '..' in old_destination.parts or old_destination==old_root or not old_destination.is_relative_to(old_root):
        raise UnknownPrimary('Queued ingest has an invalid recorded scope')
    # A changed mount may rebase only the same ingest-folder suffix, not an arbitrary root.
    roots={profile['project_root'],*profile.get('previous_roots',[])}
    if str(old_root) not in roots:raise UnknownPrimary('Queued ingest root was never registered for its primary')
    if not available(result,probe):raise OfflinePrimary('Connect primary '+profile.get('label',profile['id'])+' to resume its queued work')
    return result


def event_for_payload(payload,config):
    event=copy.deepcopy(payload['event'])
    # Single-primary installations retain their original event validator.
    # Registry ownership/rebase checks apply only to registered profiles.
    if not config.get('primary_id'):return event
    old_root=Path(payload.get('ingest_root',config['ingest_root']))
    old_destination=Path(payload.get('destination',event.get('FileCopyCompleted_destinationPath','')))
    event_destination=Path(event.get('FileCopyCompleted_destinationPath',''))
    if (not old_root.is_absolute() or not old_destination.is_absolute() or
            '..' in old_root.parts or '..' in old_destination.parts or
            event_destination!=old_destination or old_destination==old_root or
            not old_destination.is_relative_to(old_root)):
        raise UnknownPrimary('Queued event and recorded destination scope disagree')
    if old_root!=Path(config['ingest_root']):
        relative=old_destination.relative_to(old_root)
        if '..' in relative.parts or not relative.parts:raise UnknownPrimary('Invalid reconnect scope')
        event['FileCopyCompleted_destinationPath']=str(Path(config['ingest_root'])/relative)
    return event


def for_event(root,config,event,*,probe=mac_volume_info):
    state=read_registry(root,config)
    if state is None:return config
    if state.get('pending_cutover') and config.get('_cutover_validation')!=state['pending_cutover']['id']:raise CutoverPending('Primary cutover pending; event retained for reconciliation')
    destination=Path(event.get('FileCopyCompleted_destinationPath',''))
    candidates=[]
    for identity,profile in state['profiles'].items():
        candidate=config_for_profile(config,profile);project=Path(candidate['ingest_root'])
        roots=[project,*[Path(p) for p in profile.get('previous_roots',[])]]
        if destination.is_absolute() and '..' not in destination.parts and any(destination!=root and destination.is_relative_to(root) for root in roots):candidates.append(candidate)
    if len(candidates)!=1:raise UnknownPrimary('OffShoot event does not belong to one registered primary')
    candidate=candidates[0]
    if state.get('pending_cutover') and candidate['primary_id']!=state['pending_cutover']['to_primary']:
        raise OfflinePrimary('Validation processes only the pending target primary; event retained')
    if not available(candidate,probe):raise OfflinePrimary('OffShoot event primary is unavailable; event retained')
    # A saved event may refer to a mount name that subsequently changed. Never
    # reinterpret a new transfer to a replacement disk at that old name as work
    # on the registered primary merely because its relative folder matches.
    current=Path(candidate['ingest_root'])
    aliases=[Path(path) for path in candidate['_profile_roots'] if Path(path)!=current]
    for alias in aliases:
        if destination!=alias and destination.is_relative_to(alias):
            if any(path.is_symlink() for path in (destination,*destination.parents)):
                raise UnknownPrimary('Historical event path uses a symlink')
            observed=destination if destination.exists() else alias
            if observed.exists():
                try:info=probe(observed)
                except (OSError,ValueError,RuntimeError) as exc:
                    raise OfflinePrimary('Historical event mount cannot be verified; event retained') from exc
                if str(info.get('VolumeUUID','')).upper()!=str(candidate['volume_uuid']).upper():
                    raise UnknownPrimary('Historical event mount is occupied by another volume')
    return candidate


def rebase_event(config,event):
    result=copy.deepcopy(event);destination=Path(result.get('FileCopyCompleted_destinationPath',''))
    roots=[Path(root) for root in config.get('_profile_roots',[config['ingest_root']])]
    matches=[root for root in roots if destination.is_absolute() and destination!=root and destination.is_relative_to(root)]
    if len(matches)!=1:raise UnknownPrimary('Event scope is not an exact registered primary root')
    result['FileCopyCompleted_destinationPath']=str(Path(config['ingest_root'])/destination.relative_to(matches[0]))
    return result
