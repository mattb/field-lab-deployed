#!/usr/bin/env python3
"""Plan CCC date-folder filters. No copies, CCC calls or settings writes.

Input schema 1: project, closed_sets [{label, dates}], current_label.
Output is a proposal, NOT an applied task configuration or verified backup.
"""
import argparse
import datetime
import copy
import json
import os
from pathlib import Path
import re
import tempfile


def date_folder(name):
    match = re.fullmatch(r'(\d{8})(?:_D\d{2,})?', name)
    if not match:
        return False
    try:
        datetime.datetime.strptime(match[1], '%Y%m%d')
        return True
    except ValueError:
        return False


def plan(project, ledger, next_label):
    original = Path(project).expanduser().absolute()
    if original.is_symlink() or any(p.is_symlink() for p in original.parents):
        raise ValueError('Project path must not use symlinks')
    root = original.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Project must be an existing directory')
    if ledger.get('schema') != 1 or Path(ledger.get('project', '')).absolute() != root:
        raise ValueError('Ledger schema/project mismatch')
    if not re.fullmatch(r'B\d{3,}', next_label):
        raise ValueError('Expected next disk label such as B002')
    if ledger.get('pending_rotation'):
        raise ValueError('A handover is already pending; reconcile CCC configuration before planning another')
    labels, assigned, previous = set(), set(), []
    for entry in ledger.get('closed_sets', []):
        label = entry['label']
        if not re.fullmatch(r'B\d{3,}', label) or label in labels:
            raise ValueError('Duplicate or invalid disk label')
        labels.add(label)
        dates = entry['dates']
        if not dates or any(not isinstance(d, str) or not date_folder(d) for d in dates):
            raise ValueError('Invalid or empty closed folder assignment')
        if len(set(dates)) != len(dates) or assigned.intersection(dates):
            raise ValueError('Overlapping folder assignments')
        assigned.update(dates)
        previous.append({'label': label, 'mode': 'Include', 'folders': sorted(dates),
                         'expressions': ['/' + d + '/**' for d in sorted(dates)]})
    current = ledger.get('current_label')
    if not isinstance(current, str) or not re.fullmatch(r'B\d{3,}', current) or current in labels:
        raise ValueError('Invalid current disk label')
    if next_label in labels or next_label == current:
        raise ValueError('Next disk label is already used')
    present, others = set(), []
    for child in root.iterdir():
        if child.is_symlink():
            raise ValueError('Source contains a symlink; review source scope')
        if child.is_dir() and date_folder(child.name):
            present.add(child.name)
        else:
            others.append(child.name)
    if assigned - present:
        raise ValueError('Assigned dated folders are missing; reconcile moves before rotation')
    closing = sorted(present - assigned)
    if not closing:
        raise ValueError('No dated folders to close on the current disk')
    previous.append({'label': current, 'mode': 'Include', 'folders': closing,
                     'expressions': ['/' + d + '/**' for d in closing]})
    all_assigned = sorted(present)
    proposed = copy.deepcopy(ledger)
    proposed['closed_sets'].append({'label': current, 'dates': closing})
    proposed['current_label'] = next_label
    return {'schema': 1, 'kind': 'ccc-date-filter-proposal', 'applied': False,
            'backup_verified': False, 'project': str(root), 'closing': current,
            'previous': previous,
            'current': {'label': next_label, 'mode': 'Exclude', 'folders': all_assigned,
                        'expressions': ['/' + d + '/**' for d in all_assigned]},
            'other_root_items_to_review': sorted(others),
            'proposed_ledger': proposed,
            'requires': ['CCC idle; source scope complete and stable',
                         'Outgoing set independently verified before handover',
                         'Review non-date items and consistent metadata backup policy',
                         'Apply and read back filters/endpoints in supported CCC UI',
                         'Preview, validate destination UUID and capacity before running',
                         'Record configuration separately from copy verification']}


def save_new(path, value):
    path = Path(path).expanduser().absolute()
    if path.is_symlink() or path.exists():
        raise ValueError('Output already exists; preserve prior proposal')
    if not path.parent.is_dir() or path.parent.is_symlink() or any(p.is_symlink() for p in path.parent.parents):
        raise ValueError('Output parent must exist without symlinks')
    # Complete private file first, then publish without overwriting a concurrent writer.
    fd, name = tempfile.mkstemp(prefix='.rotation-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.link(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        os.unlink(name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--ledger', type=Path, required=True)
    p.add_argument('--next-label', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        result = plan(a.project, json.loads(a.ledger.read_text()), a.next_label)
        if a.output.expanduser().resolve().is_relative_to(a.project.expanduser().resolve()):
            raise ValueError('Proposal must stay outside the source project')
        save_new(a.output, result)
        print(json.dumps({'proposal': str(a.output), 'applied': False, 'backup_verified': False}))
    except (OSError, ValueError, KeyError, TypeError) as e:
        p.exit(1, str(e) + '\n')

if __name__ == '__main__':
    main()
