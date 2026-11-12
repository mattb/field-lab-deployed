"""Consistent project-sidecar snapshots for backup sets, without loading AI code."""
from __future__ import annotations

from contextlib import closing
import fcntl
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

from .atomic import fsync_dir, fsync_tree


def _safe(path):
    path = Path(path).expanduser().absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Metadata snapshot path must not contain symlinks')
    return path


def _skip(name):
    return (name in {'backup-rotation', '.backup-rotation', '.DS_Store'}
            or name.endswith(('-wal', '-shm', '-journal', '.lock', '.tmp', '.part'))
            or name.startswith(('.staging-', '.tmp-')))


def _sqlite_backup(source, target):
    deadline = time.monotonic() + 30
    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError('Metadata SQLite snapshot timed out')
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True, timeout=2)) as src:
        with closing(sqlite3.connect(target, timeout=2)) as dest:
            src.backup(dest, pages=128, progress=progress, sleep=.01)
            if dest.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('Metadata SQLite snapshot failed integrity check')
            dest.execute('PRAGMA journal_mode=DELETE')


def snapshot(project, target, clone):
    """Publish a new immutable directory containing project's _DIT_NOTES contents.

    ``clone(source: Path, destination: Path)`` creates a regular-file copy (the
    production caller supplies APFS clonefile). SQLite databases use the backup
    API, including committed WAL contents, instead. The target must not exist.
    A failed/interrupted snapshot never creates the final target; a hidden sibling
    may survive SIGKILL and is not a readiness marker.
    """
    project = _safe(project)
    source = _safe(project/'_DIT_NOTES')
    target = _safe(target)
    if not source.is_dir():
        raise ValueError('Project has no _DIT_NOTES directory to snapshot')
    if target.exists():
        raise FileExistsError('Metadata snapshot target already exists')
    if target == source or source in target.parents:
        raise ValueError('Metadata snapshot target must be outside _DIT_NOTES')
    # An ancestor of the source cannot be replaced with the completed snapshot.
    if target == project or target in source.parents:
        raise ValueError('Metadata snapshot target overlaps project')
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _safe(source/'.process.lock')
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    staging = None
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError('Project process lock is not a regular file')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Project is busy; retry metadata backup when indexing is idle') from None
        staging = Path(tempfile.mkdtemp(prefix='.'+target.name+'.staging-', dir=target.parent))
        for root, dirs, files in os.walk(source, followlinks=False):
            root = Path(root)
            for name in list(dirs):
                item = root/name
                if item.is_symlink():
                    raise ValueError('Metadata snapshot rejects symlinks')
                if _skip(name):
                    dirs.remove(name)
                else:
                    if not stat.S_ISDIR(item.lstat().st_mode):
                        raise ValueError('Metadata snapshot rejects special files')
                    (staging/item.relative_to(source)).mkdir()
            for name in files:
                item = root/name
                item_stat = item.lstat()
                if not stat.S_ISREG(item_stat.st_mode):
                    raise ValueError('Metadata snapshot rejects symlinks and special files')
                if _skip(name):
                    continue
                output = staging/item.relative_to(source)
                if name.endswith('.sqlite3'):
                    _sqlite_backup(item, output)
                else:
                    clone(item, output)
                if output.is_symlink() or not output.is_file():
                    raise ValueError('Metadata clone did not create a regular file')
        fsync_tree(staging)
        # Do not replace an existing snapshot even if a second actor made it while
        # this function held the project lock. Callers also serialize their ledger.
        if target.exists():
            raise FileExistsError('Metadata snapshot target already exists')
        os.rename(staging, target)
        staging = None
        fsync_dir(target.parent)
        return target
    finally:
        if staging is not None:
            shutil.rmtree(staging)
        os.close(lock_fd)
