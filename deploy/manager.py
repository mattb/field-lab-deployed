#!/usr/bin/env python3
"""Stage and switch isolated application releases; never touch media or project DBs.

This is deliberately stdlib-only. Bootstrap owns dependencies and model downloads;
the manager owns the source seal, readiness gate, locks and atomic version pointer.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import time
import uuid

DEFAULT_HOME = Path.home() / 'Library/Application Support/Field Lab'
EXCLUDED_DIRS = {'.git', '.venv', '__pycache__', '.history', 'node_modules', 'models', 'runtime', 'python', 'bin', 'lib', '.pytest_cache', '.conversion-venv', '.cache', '.field-lab-bootstrap', '.deploy-tests', 'tools'}
EXCLUDED_FILES = {'.git', '.DS_Store', 'config.json', 'deploy-ready.json', '.field-lab-release.json', '.field-lab-ready.json'}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)

def atomic_json(path, data):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as f:
            json.dump(data, f, sort_keys=True, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
            if hasattr(fcntl, 'F_FULLFSYNC'): fcntl.fcntl(f.fileno(), fcntl.F_FULLFSYNC)
        os.replace(temporary, path); sync_dir(path.parent)
    finally:
        if temporary.exists(): temporary.unlink()

def real_directory(path, create=False):
    path = Path(path)
    if path.is_symlink(): raise ValueError(f'Refusing symlink directory: {path}')
    if create: path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir(): raise ValueError(f'Expected a directory: {path}')
    return path.resolve()

def layout(home):
    home = real_directory(Path(home).expanduser().absolute(), create=True)
    releases = real_directory(home / 'releases', create=True)
    shared = real_directory(home / 'shared', create=True)
    for name in ('runtime', 'model-generations'): real_directory(shared / name, create=True)
    config = shared / 'config.json'
    if config.is_symlink() or (config.exists() and not config.is_file()):
        raise ValueError('Shared config must be a regular file, not a symlink')
    return home, releases, shared

@contextmanager
def locked(home):
    home, releases, shared = layout(home)
    path = home / 'deploy.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a+') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another deployment is running') from None
        yield home, releases, shared

def source_files(source):
    source = real_directory(source)
    files = {}
    for base, directories, names in os.walk(source, followlinks=False):
        parent = Path(base)
        for name in list(directories):
            rel = (parent / name).relative_to(source)
            if name in EXCLUDED_DIRS or str(rel) in ('viewer/dist', 'native-progress/.build', 'native-progress/dist') or name.startswith('.staging-'):
                directories.remove(name)
            elif (parent / name).is_symlink(): raise ValueError(f'Source directory symlink is not portable: {rel}')
        for name in names:
            path = parent / name
            rel = path.relative_to(source).as_posix()
            if name in EXCLUDED_FILES or name.endswith(('.pyc', '.scpt')) or rel == 'ditnotes/native/feature_select': continue
            if path.is_symlink() or not path.is_file(): raise ValueError(f'Source entry must be a regular file: {rel}')
            files[rel] = {'sha256': digest(path), 'executable': bool(path.stat().st_mode & 0o111)}
    if not files: raise ValueError('Source tree is empty')
    return dict(sorted(files.items()))

def identity(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def git_provenance(source):
    """Record source provenance without making Git a runtime requirement."""
    result={'git_commit':None,'git_dirty':None}
    def git(*args):
        return subprocess.run(['git','-C',str(source),*args],capture_output=True,text=True,timeout=10)
    try:
        top=git('rev-parse','--show-toplevel')
        if top.returncode or Path(top.stdout.strip()).resolve()!=source.resolve():return result
        commit=git('rev-parse','--verify','HEAD')
        if not commit.returncode:result['git_commit']=commit.stdout.strip()
        dirty=git('status','--porcelain','--untracked-files=normal')
        if not dirty.returncode:result['git_dirty']=bool(dirty.stdout.strip())
    except (OSError,subprocess.SubprocessError):pass
    return result

def safe_relative(root, relative, allow_internal_links=False):
    p = Path(relative)
    if p.is_absolute() or not p.parts or '..' in p.parts: raise ValueError('Invalid manifest path')
    out = root / p
    for part in [out, *out.parents]:
        if part == root: break
        if part.is_symlink() and not allow_internal_links: raise ValueError(f'Manifest path uses a symlink: {relative}')
    if not out.resolve().is_relative_to(root.resolve()): raise ValueError('Manifest escaped release')
    return out

def verify_source(release):
    manifest_path = release / '.field-lab-release.json'
    if manifest_path.is_symlink(): raise ValueError('Source manifest cannot be a symlink')
    manifest = json.loads(manifest_path.read_text())
    if identity(manifest['files']) != manifest['release_id']: raise ValueError('Source manifest identity mismatch')
    if release.name != manifest['release_id']: raise ValueError('Release directory identity mismatch')
    for rel, expected in manifest['files'].items():
        path = safe_relative(release, rel)
        if not path.is_file() or digest(path) != expected['sha256'] or bool(path.stat().st_mode & 0o111) != expected['executable']:
            raise ValueError(f'Source seal failed: {rel}')
    actual = source_files(release)
    if actual != manifest['files']: raise ValueError('Release contains unsealed or changed source files')
    return manifest

def release_path(releases, release_id):
    if len(release_id) != 64 or any(c not in '0123456789abcdef' for c in release_id): raise ValueError('Expected a 64-character release ID')
    return real_directory(releases / release_id)

def stage(source, home=DEFAULT_HOME, fault=None):
    source = real_directory(source)
    with locked(home) as (home, releases, shared):
        if home.is_relative_to(source) or source.is_relative_to(home): raise ValueError('Source checkout and deployment home must be separate')
        files = source_files(source); release_id = identity(files); release = releases / release_id
        assets_lock = source / 'deploy/assets.lock.json'
        if not assets_lock.is_file() or assets_lock.is_symlink(): raise ValueError('Source requires deploy/assets.lock.json')
        generation = digest(assets_lock)
        model_root = real_directory(shared / 'model-generations' / generation, create=True)
        if release.exists() or release.is_symlink():
            release = real_directory(release); verify_source(release)
            return {'release': str(release), 'release_id': release_id, 'reused': True}
        temporary = releases / ('.staging-' + uuid.uuid4().hex)
        temporary.mkdir()
        # Incomplete staging folders deliberately remain inspectable after failure.
        for rel, expected in files.items():
            target = temporary / rel; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)
            target.chmod(0o755 if expected['executable'] else 0o644)
            with target.open('rb') as f: os.fsync(f.fileno())
            if digest(target) != expected['sha256']: raise ValueError(f'Source changed while staging: {rel}')
            if fault: fault('copy', target)
        for name in ('runtime', 'config.json'): (temporary / name).symlink_to(shared / name, target_is_directory=name != 'config.json')
        (temporary / 'models').symlink_to(model_root, target_is_directory=True)
        atomic_json(temporary / '.field-lab-release.json', {'schema': 1, 'release_id': release_id, 'model_generation': generation, 'files': files, **git_provenance(source)})
        # All directory entries must be durable before publishing the candidate.
        for base, _, _ in os.walk(temporary, topdown=False): sync_dir(base)
        os.rename(temporary, release); sync_dir(releases)
        return {'release': str(release), 'release_id': release_id, 'reused': False}

def validate_shared(release, shared):
    manifest = verify_source(release)
    generation = manifest.get('model_generation', '')
    if len(generation) != 64 or generation != digest(release / 'deploy/assets.lock.json'): raise ValueError('Model generation does not match assets lock')
    for name in ('models', 'runtime', 'config.json'):
        p = release / name
        expected = shared / 'model-generations' / generation if name == 'models' else shared / name
        if not p.is_symlink() or os.readlink(p) != str(expected): raise ValueError(f'Unexpected shared path: {name}')
        if name != 'config.json': real_directory(expected)

def verify_bootstrap(release):
    manifest = verify_source(release)
    receipt_path = release / 'deploy-ready.json'
    if receipt_path.is_symlink(): raise ValueError('Bootstrap receipt cannot be a symlink')
    receipt = json.loads(receipt_path.read_text())
    if receipt.get('ready') is not True or receipt.get('release_id') != manifest['release_id']: raise ValueError('Bootstrap receipt does not match this source release')
    generated = receipt.get('generated_files')
    if not isinstance(generated, dict) or not generated: raise ValueError('Bootstrap receipt has no generated asset checksums')
    required = {'viewer/dist/index.html', '.venv/pyvenv.cfg'}
    if not required.issubset(generated): raise ValueError('Bootstrap receipt omits UI or Python verification')
    for rel, sha in generated.items():
        if Path(rel).parts[0] in ('runtime', 'models', 'config.json'): raise ValueError('Generated assets cannot seal shared mutable state')
        path = safe_relative(release, rel, allow_internal_links=True)
        if not path.is_file() or digest(path) != sha: raise ValueError(f'Generated asset seal failed: {rel}')
    # Missing/extra dependency modules can change imports even when all original
    # files still match. Bytecode caches are intentionally regenerable.
    for directory in ('viewer/dist', '.venv', 'tools/python', 'tools/node', 'tools/uv', 'search/node_modules', 'viewer/node_modules'):
        prefix = directory + '/'
        expected_paths = {name for name in generated if name.startswith(prefix)}
        actual_paths = {str(path.relative_to(release)) for path in (release / directory).rglob('*')
                        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
        if actual_paths != expected_paths: raise ValueError(f'Generated asset inventory changed: {directory}')
    return manifest, receipt

def offline_check(release):
    result = subprocess.run([str(release / '.venv/bin/python'), str(release / 'deploy/bootstrap.py'), '--release', str(release), '--check'], cwd=release, capture_output=True, text=True, timeout=600)
    try: ready = json.loads(result.stdout)
    except ValueError: raise RuntimeError('Offline readiness returned invalid JSON') from None
    if result.returncode or ready.get('ready') is not True: raise RuntimeError('Offline readiness failed: ' + result.stdout[-4000:] + result.stderr[-2000:])
    return ready

def mark_ready(home, release_id, checker=offline_check):
    with locked(home) as (home, releases, shared):
        release = release_path(releases, release_id); validate_shared(release, shared)
        verify_bootstrap(release); readiness = checker(release)
        if readiness.get('ready') is not True: raise RuntimeError('Offline readiness failed')
        receipt_sha = digest(release / 'deploy-ready.json')
        atomic_json(release / '.field-lab-ready.json', {'release_id': release_id, 'bootstrap_sha256': receipt_sha, 'checked_at': time.time(), 'offline': readiness})
        return {'release': str(release), 'release_id': release_id, 'ready': True}

def pointer(home, name):
    path = home / name
    if not path.exists() and not path.is_symlink(): return None
    if not path.is_symlink(): raise ValueError(f'{name} must be a managed symlink')
    target = path.resolve(strict=True)
    if target.parent != home / 'releases' or len(target.name) != 64: raise ValueError(f'{name} points outside managed releases')
    return target

def set_pointer(home, name, target):
    temporary = home / ('.' + name + '.' + uuid.uuid4().hex)
    temporary.symlink_to(target)
    try: os.replace(temporary, home / name); sync_dir(home)
    finally:
        if temporary.is_symlink(): temporary.unlink()

@contextmanager
def stopped_service(shared):
    path = shared / 'runtime/service.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a+') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('AI service is running; stop it cleanly before switching releases') from None
        yield

def activate(home, release_id=None, rollback=False, fault=None, checker=offline_check):
    with locked(home) as (home, releases, shared):
        current = pointer(home, 'current'); previous = pointer(home, 'previous')
        release = previous if rollback else release_path(releases, release_id)
        if release is None: raise ValueError('There is no previous release to roll back to')
        validate_shared(release, shared); verify_bootstrap(release)
        ready_path = release / '.field-lab-ready.json'
        if ready_path.is_symlink(): raise ValueError('Ready receipt cannot be a symlink')
        ready = json.loads(ready_path.read_text())
        if ready.get('release_id') != release.name or ready.get('bootstrap_sha256') != digest(release / 'deploy-ready.json') or ready.get('offline', {}).get('ready') is not True: raise ValueError('Candidate has not passed mark-ready')
        if checker(release).get('ready') is not True: raise RuntimeError('Current offline readiness failed')
        with stopped_service(shared):
            if current == release: return {'release': str(release), 'release_id': release.name, 'changed': False}
            if current: set_pointer(home, 'previous', current)
            if fault: fault('before-switch', release)
            set_pointer(home, 'current', release)
            if fault: fault('after-switch', release)
            return {'release': str(release), 'release_id': release.name, 'previous': str(current) if current else None, 'changed': True}

def status(home):
    with locked(home) as (home, releases, shared):
        current=pointer(home,'current');previous=pointer(home,'previous')
        provenance={}
        if current:
            try:
                manifest=verify_source(current)
                provenance={key:manifest.get(key) for key in ('release_id','git_commit','git_dirty')}
            except (OSError,ValueError,KeyError,TypeError) as error:
                # Status must remain usable for diagnosing a damaged current
                # release and selecting a separately verified rollback target.
                provenance={'integrity_error':str(error)}
        return {'home': str(home), 'current': str(current or ''), 'previous': str(previous or ''), 'current_provenance':provenance, 'staging': sorted(p.name for p in releases.glob('.staging-*'))}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('stage', 'mark-ready', 'activate', 'rollback', 'status'):
        p = sub.add_parser(command); p.add_argument('--home', type=Path, default=DEFAULT_HOME)
        if command == 'stage': p.add_argument('--source', type=Path, required=True)
        if command in ('mark-ready', 'activate'): p.add_argument('--release', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'stage': result = stage(args.source, args.home)
        elif args.command == 'mark-ready': result = mark_ready(args.home, args.release)
        elif args.command == 'activate': result = activate(args.home, args.release)
        elif args.command == 'rollback': result = activate(args.home, rollback=True)
        else: result = status(args.home)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        print(json.dumps({'error': str(e)}), file=sys.stderr); return 1
    return 0

if __name__ == '__main__': sys.exit(main())
