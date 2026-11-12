"""Read-only, bounded macOS volume/backing-device identity for backup rotation.

Volume UUID is the persistent registration key. ``physical`` also contains whole
BSD disk identifiers for same-session independence checks; those identifiers are
not persistent drive identities. SoftRAID resolves only through its explicit,
UUID-matched driver provider list; unknown virtual topology fails closed.
"""
from __future__ import annotations
import plistlib
from pathlib import Path
import re
import subprocess
import time


def mac_identity(path, runner=subprocess.run, timeout=12):
    target=Path(path).expanduser().resolve(strict=True)
    if target.is_file():target=target.parent
    device=target.stat().st_dev
    deadline=time.monotonic()+timeout
    calls=0
    def command(argv):
        nonlocal calls
        remaining=deadline-time.monotonic();calls+=1
        if remaining<=0 or calls>64:raise RuntimeError('Volume identity query budget exceeded')
        try:
            response=runner(argv,capture_output=True,timeout=min(4,remaining),check=False)
            if response.returncode:raise ValueError('diskutil query failed')
            data=plistlib.loads(response.stdout)
            if not isinstance(data,(dict,list)) or isinstance(data,dict) and any(str(k).lower().startswith('error') and v for k,v in data.items()):raise ValueError('Invalid device response')
            return data
        except (OSError,ValueError,plistlib.InvalidFileException,subprocess.SubprocessError) as exc:
            raise RuntimeError('Unable to establish backing-device identity') from exc
    def query(*args):
        result=command(['/usr/sbin/diskutil',*args])
        if not isinstance(result,dict):raise RuntimeError('Invalid diskutil response')
        return result
    info=None
    for candidate in (target,*target.parents):
        try:data=query('info','-plist',str(candidate))
        except RuntimeError:
            if calls>=64 or time.monotonic()>=deadline:raise
            continue
        mount=data.get('MountPoint');uuid=data.get('VolumeUUID')
        if not isinstance(mount,str) or not mount.startswith('/') or not isinstance(uuid,str) or not uuid.strip():continue
        mounted=Path(mount).resolve(strict=True)
        if not mounted.is_dir() or mounted.stat().st_dev!=device:continue
        info=data;break
    if info is None:raise RuntimeError('No mounted volume UUID found')
    filesystem=str(info.get('FilesystemType') or '').lower()
    if filesystem not in ('apfs','hfs','exfat','msdos'):raise RuntimeError('Unsupported local filesystem for backup rotation')
    volume=info.get('DeviceIdentifier')
    if not isinstance(volume,str) or not re.fullmatch(r'disk\d+(?:s\d+)*',volume):raise RuntimeError('Unknown local disk identifier')
    stores=[]
    if filesystem=='apfs':
        for entry in info.get('APFSPhysicalStores',[]):
            if isinstance(entry,dict):stores.append(entry.get('APFSPhysicalStore') or entry.get('DeviceIdentifier'))
        if not stores:
            container=info.get('APFSContainerReference') or info.get('ParentWholeDisk')
            data=query('apfs','list','-plist')
            matches=[c for c in data.get('Containers',[]) if c.get('ContainerReference')==container]
            if len(matches)!=1:raise RuntimeError('Unknown APFS container topology')
            stores=[s.get('DeviceIdentifier') for s in matches[0].get('PhysicalStores',[])]
    else:stores=[volume]
    if not stores or len(stores)>8 or any(not isinstance(s,str) or not re.fullmatch(r'disk\d+(?:s\d+)*',s) for s in stores):raise RuntimeError('Unknown physical stores')
    physical=set();pending=sorted(set(stores));visited=set();registry=None
    while pending:
        store=pending.pop(0)
        if store in visited:raise RuntimeError('Cyclic or duplicate backing topology')
        visited.add(store)
        backing=query('info','-plist',store)
        whole=backing.get('DeviceIdentifier') if backing.get('WholeDisk') is True else backing.get('ParentWholeDisk')
        if not isinstance(whole,str) or not re.fullmatch(r'disk\d+',whole):raise RuntimeError('Unknown whole-disk backing device')
        if whole!=store:backing=query('info','-plist',whole)
        if backing.get('WholeDisk') is not True or backing.get('DeviceIdentifier')!=whole:raise RuntimeError('Invalid whole-disk topology')
        protocol=str(backing.get('BusProtocol') or '').lower()
        if backing.get('VirtualOrPhysical')=='Virtual' and backing.get('IORegistryEntryName')=='SoftRAID_Media':
            # Observed SoftRAID driver contract: a class-scoped registry entry
            # explicitly names its provider partitions. Match both live BSD name
            # and volume UUID; never infer member disks from numbering/enclosure.
            if registry is None:registry=command(['/usr/sbin/ioreg','-a','-r','-c','com_softraid_driver_SoftRAID_Media'])
            if not isinstance(registry,list):raise RuntimeError('Invalid SoftRAID registry')
            matches=[r for r in registry if isinstance(r,dict) and r.get('IOObjectClass')=='com_softraid_driver_SoftRAID_Media' and r.get('BSD Name')==whole]
            if len(matches)!=1:raise RuntimeError('Unknown SoftRAID provider mapping')
            raid=matches[0];raid_uuid=backing.get('DiskUUID')
            if not isinstance(raid_uuid,str) or not raid_uuid or str(raid.get('UUID','')).upper()!=raid_uuid.upper():raise RuntimeError('SoftRAID registry UUID mismatch')
            providers=raid.get('SoftRAID Provider Array')
            if not isinstance(providers,list) or not providers or len(providers)>16 or any(not isinstance(s,str) or not re.fullmatch(r'disk\d+(?:s\d+)*',s) for s in providers) or len(set(providers))!=len(providers):raise RuntimeError('Invalid SoftRAID provider list')
            if any(s in visited or s in pending for s in providers):raise RuntimeError('Cyclic SoftRAID topology')
            physical.add('softraid:'+raid_uuid.upper());pending.extend(providers)
            continue
        if backing.get('VirtualOrPhysical')=='Virtual' or backing.get('RAIDMaster') or backing.get('RAIDSlice') or not protocol or protocol in ('disk image','virtual interface'):raise RuntimeError('Virtual or RAID backing topology requires explicit support')
        physical.add('disk:'+whole)
        for key in ('MediaUUID','DiskUUID'):
            value=backing.get(key)
            if isinstance(value,str) and value.strip():physical.add(key.lower()+':'+value.strip().upper())
    return {'uuid':info['VolumeUUID'].strip().upper(),'mount':str(Path(info['MountPoint']).resolve(strict=True)),'physical':sorted(physical),'filesystem':filesystem}
