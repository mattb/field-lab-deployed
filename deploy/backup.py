#!/usr/bin/env python3
"""Opt-in backup rotation; existing CCC tasks and settings are never edited.

Dedicated tasks must be created and reviewed in CCC before registration. Run and
UI commands may start only those explicitly registered tasks.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ditnotes.backup_rotation import Manager, safe
from ditnotes.atomic import fsync_dir


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    q = sub.add_parser('init', help='Initialize isolated rotation state and APFS clone spool')
    q.add_argument('--project', type=Path, required=True)
    q.add_argument('--spool', type=Path, required=True)
    q.add_argument('--state', type=Path)
    q = sub.add_parser('prepare-set', help='Create an empty fixed source before configuring a new CCC task')
    q.add_argument('--state', type=Path, required=True)
    q.add_argument('--label', required=True)
    q = sub.add_parser('add-drive', help='Register a separately created, reviewed dedicated CCC task')
    q.add_argument('--state', type=Path, required=True)
    q.add_argument('--label', required=True)
    q.add_argument('--mount', type=Path, required=True)
    q.add_argument('--destination-relative', required=True)
    q.add_argument('--task-uuid', required=True)
    q.add_argument('--dedicated-task-reviewed', action='store_true')
    q.add_argument('--guard-path', type=Path)
    q = sub.add_parser('status', help='Read rotation state without starting CCC')
    q.add_argument('--state', type=Path, required=True)
    q = sub.add_parser('run', help='Discover verified ingests and back up pending footage')
    q.add_argument('--state', type=Path, required=True)
    q.add_argument('--queue-db', type=Path)
    q = sub.add_parser('ui', help='Open the local backup interface; does not start a backup')
    q.add_argument('--state', type=Path, required=True)
    q.add_argument('--queue-db', type=Path)
    q.add_argument('--port', type=int, default=8778)
    q.add_argument('--open', action='store_true', dest='open_browser')
    q = sub.add_parser('guard', help='Write a standalone guard for manual secure installation; no sudo or CCC edits')
    q.add_argument('--state', type=Path, required=True)
    q.add_argument('--label', required=True)
    q.add_argument('--output', type=Path, required=True)
    return p


class DiscoveryManager:
    def __init__(self, manager, queue_db):
        self.manager, self.queue_db = manager, queue_db
    def status(self):
        return self.manager.status()
    def run_pending(self):
        if self.queue_db is not None:
            self.manager.discover_queue(self.queue_db)
        return self.manager.run_pending()


def _write_guard(manager, label, output):
    from ditnotes.backup_guard import generate_guard
    entry = next((e for e in manager.state['sets'] if e['label'] == label), None)
    if entry is None:
        raise ValueError('Unknown backup label')
    source = Path(manager.state['spool'])/label/'payload'
    destination = Path(entry['mount'])/entry['destination_relative']
    text = generate_guard(source, destination, manager.state['primary']['uuid'], entry['uuid'])
    output = safe(output, exists=False)
    if not output.parent.is_dir():
        raise ValueError('Guard output parent must already exist')
    fd = os.open(output, os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os, 'O_NOFOLLOW', 0), 0o700)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush(); os.fsync(stream.fileno())
        fsync_dir(output.parent)
    except Exception:
        # Preserve an incomplete guard for diagnosis. It is never installed here.
        raise
    return {'guard': str(output), 'installed': False, 'ccc_settings_changed': False,
            'next_step': 'Follow docs/BACKUP-ROTATION.md for secure installation and task acceptance'}


def main(argv=None, *, manager_type=Manager, serve_fn=None, opener=subprocess.run):
    a = parser().parse_args(argv)
    try:
        if a.command == 'init':
            state = a.state or a.project.expanduser().absolute()/'_DIT_NOTES'/'backup-rotation'
            manager = manager_type.initialize(state, a.project, a.spool)
            result = {'state': str(state), 'status': manager.status(), 'ccc_settings_changed': False}
        else:
            manager = manager_type(a.state)
            if a.command == 'prepare-set':
                result = manager.prepare_set(a.label)
            elif a.command == 'add-drive':
                kwargs = {'dedicated_task_reviewed': a.dedicated_task_reviewed}
                if a.guard_path is not None:
                    kwargs['guard_path'] = a.guard_path
                result = manager.add_set(a.label, a.mount, a.destination_relative, a.task_uuid, **kwargs)
            elif a.command == 'status':
                result = manager.status()
            elif a.command == 'run':
                result = DiscoveryManager(manager, a.queue_db).run_pending()
            elif a.command == 'guard':
                result = _write_guard(manager, a.label, a.output)
            elif a.command == 'ui':
                if not 0 <= a.port <= 65535:
                    raise ValueError('Port must be between 0 and 65535')
                if serve_fn is None:
                    from ditnotes.backup_ui import serve
                    serve_fn = serve
                server = serve_fn(DiscoveryManager(manager, a.queue_db), port=a.port)
                print(json.dumps({'url': server.origin, 'backup_started': False}), flush=True)
                try:
                    if a.open_browser:
                        opener(['/usr/bin/open', server.origin], check=True, timeout=15)
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    server.server_close()
                return 0
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
