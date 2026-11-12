"""Pure preflight validation for a dedicated CCC rotation task.

CCC invokes its preflight with actual source and destination as $1/$2; a nonzero
exit aborts copying. Do not execute this user-writable module as root from CCC:
production shell guards must contain secured standalone code and fixed endpoints.
Source snapshots are deliberately not accepted as equivalent paths.
"""
from pathlib import Path
from .backup_volumes import mac_identity


def _canonical(path):
    p=Path(path).expanduser().absolute()
    if '..' in p.parts:raise ValueError('Parent traversal refused')
    for ancestor in (p,*p.parents):
        if ancestor.is_symlink():raise ValueError('Symlink endpoint refused')
    resolved=p.resolve(strict=True)
    if not resolved.is_dir():raise ValueError('CCC endpoint must be a directory')
    return resolved


def preflight(source,destination,expected_source,expected_destination,expected_uuid,
              *,expected_source_uuid=None,volume_probe=mac_identity):
    """Fail closed unless actual CCC endpoints match reviewed dedicated paths."""
    actual_source=_canonical(source);actual_destination=_canonical(destination)
    wanted_source=_canonical(expected_source);wanted_destination=_canonical(expected_destination)
    if actual_source!=wanted_source or actual_destination!=wanted_destination:
        raise ValueError('CCC task endpoints changed or source snapshots are enabled')
    source_info=volume_probe(actual_source);destination_info=volume_probe(actual_destination)
    expected=str(expected_uuid).strip().upper()
    if not expected or str(destination_info.get('uuid','')).upper()!=expected:
        raise ValueError('CCC destination volume UUID mismatch')
    if expected_source_uuid and str(source_info.get('uuid','')).upper()!=str(expected_source_uuid).upper():
        raise ValueError('CCC source volume UUID mismatch')
    if wanted_destination==Path(destination_info.get('mount','')).resolve():
        raise ValueError('CCC destination must be a dedicated subfolder')
    source_physical=set(source_info.get('physical',[]));dest_physical=set(destination_info.get('physical',[]))
    if not source_physical or not dest_physical or source_physical&dest_physical:
        raise ValueError('CCC source and destination must have independent physical backing')
    if source_info.get('uuid')==destination_info.get('uuid'):
        raise ValueError('CCC source and destination are the same volume')
    return {'source':str(actual_source),'destination':str(actual_destination),
            'source_uuid':source_info['uuid'],'destination_uuid':destination_info['uuid']}


def generate_guard(source,destination,source_uuid,dest_uuid):
    """Generate standalone shell code; installation/CCC task selection is manual.

    The deployed file and every parent must be root-owned and not writable by
    group/others. The generated guard invokes only Apple system binaries and does
    not import Python or execute any code from the application/current pointer.
    """
    import shlex
    def endpoint(value):
        path=Path(value).expanduser().absolute()
        if not path.is_absolute() or '..' in path.parts or path==Path('/'):
            raise ValueError('Guard endpoints must be dedicated absolute folders')
        for ancestor in (path,*path.parents):
            if ancestor.is_symlink():raise ValueError('Symlink endpoint refused')
        return str(path)
    source=endpoint(source);destination=endpoint(destination)
    if source==destination:raise ValueError('Guard endpoints must differ')
    source_uuid=str(source_uuid).strip().upper();dest_uuid=str(dest_uuid).strip().upper()
    if not source_uuid or not dest_uuid or source_uuid==dest_uuid:raise ValueError('Distinct volume UUIDs required')
    constants='\n'.join(key+'='+shlex.quote(value) for key,value in (
        ('expected_source',source),('expected_destination',destination),
        ('expected_source_uuid',source_uuid),('expected_destination_uuid',dest_uuid)))
    return '''#!/bin/bash
# Field Lab dedicated CCC preflight. Nonzero aborts copying. Do not add exit 0.
set -euo pipefail
'''+constants+'''
fail() { /usr/bin/printf '%s\\n' "Field Lab: $*" >&2; exit 1; }
[ "$#" -ge 2 ] || fail 'CCC source and destination arguments required'
source_path="${1%/}"
destination_path="${2%/}"
[ "$source_path" = "$expected_source" ] || fail 'Unexpected CCC source; disable source snapshotting and recheck task'
[ "$destination_path" = "$expected_destination" ] || fail 'Unexpected CCC destination'
check_path() {
    local item="$1"
    [ -d "$item" ] || fail 'Endpoint unavailable'
    while [ "$item" != / ]; do
        [ ! -L "$item" ] || fail 'Symlink endpoint refused'
        item="$(/usr/bin/dirname "$item")"
    done
}
volume_uuid() {
    local item="$1" original_device info candidate_uuid mount candidate_device count=0
    original_device="$(/usr/bin/stat -f %d "$item")"
    while :; do
        count=$((count + 1))
        [ "$count" -le 64 ] || return 1
        if info="$(/usr/sbin/diskutil info -plist "$item" 2>/dev/null)"; then
            candidate_uuid="$(/usr/bin/printf '%s' "$info" | /usr/bin/plutil -extract VolumeUUID raw -o - - 2>/dev/null)" || candidate_uuid=''
            mount="$(/usr/bin/printf '%s' "$info" | /usr/bin/plutil -extract MountPoint raw -o - - 2>/dev/null)" || mount=''
            if [ -n "$candidate_uuid" ] && [ -n "$mount" ] && [ -d "$mount" ]; then
                candidate_device="$(/usr/bin/stat -f %d "$mount")"
                if [ "$candidate_device" = "$original_device" ]; then
                    /usr/bin/printf '%s' "$candidate_uuid" | /usr/bin/tr '[:lower:]' '[:upper:]'
                    return 0
                fi
            fi
        fi
        [ "$item" != / ] || return 1
        item="$(/usr/bin/dirname "$item")"
    done
}
check_path "$source_path"
check_path "$destination_path"
[ "$(volume_uuid "$source_path")" = "$expected_source_uuid" ] || fail 'Source volume UUID mismatch'
[ "$(volume_uuid "$destination_path")" = "$expected_destination_uuid" ] || fail 'Destination volume UUID mismatch'
# Physical independence is checked by the manager before this task is launched.
'''


def validate_installed(path,expected_text):
    """Check the exact secured root-owned standalone guard without modifying it."""
    import hashlib
    import stat
    p=Path(path).expanduser().absolute()
    if '..' in p.parts:raise ValueError('Guard parent traversal refused')
    for item in (p,*p.parents):
        st=item.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:
            raise ValueError('CCC guard and all parents must be root-owned and not group/world writable')
        if item!=p and not stat.S_ISDIR(st.st_mode):raise ValueError('Invalid guard parent')
    st=p.lstat()
    if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o755:
        raise ValueError('CCC guard must be a regular executable mode-755 file')
    actual=p.read_bytes();expected=expected_text.encode('utf-8')
    if actual!=expected:raise ValueError('Installed CCC guard does not match reviewed endpoints')
    return {'path':str(p),'sha256':hashlib.sha256(actual).hexdigest()}
