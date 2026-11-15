#!/usr/bin/env python3
"""Inspect/plan multi-primary shoot cutover; never configure apps or copy media.

External configuration and readback happen through the cutover skill/coordinator.
A journal record is not independent proof that an external app was configured.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ditnotes.shoot_registry import CUTOVER_STAGES,ShootRegistry


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home',type=Path,required=True)
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('init');q.add_argument('--name',default='Shoot')
    q=sub.add_parser('register');q.add_argument('--volume',type=Path,required=True);q.add_argument('--ingest-folder',default='Ingests');q.add_argument('--label')
    sub.add_parser('status')
    q=sub.add_parser('refresh-mount');q.add_argument('--primary',required=True);q.add_argument('--volume',type=Path,required=True)
    q=sub.add_parser('ledger-path');q.add_argument('--primary',required=True)
    q=sub.add_parser('backup-register');q.add_argument('--primary',required=True);q.add_argument('--label',required=True);q.add_argument('--readback',type=Path,required=True)
    q=sub.add_parser('backup-current');q.add_argument('--primary',required=True);q.add_argument('--label',required=True)
    q=sub.add_parser('begin');q.add_argument('--primary',required=True)
    sub.add_parser('target-config')
    q=sub.add_parser('record-step');q.add_argument('--stage',choices=CUTOVER_STAGES,required=True);q.add_argument('--evidence',type=Path,required=True)
    return p


def main(argv=None,registry_type=ShootRegistry):
    p=parser();a=p.parse_args(argv)
    try:
        registry=registry_type(a.home)
        if a.command=='init':result=registry.initialize(name=a.name)
        elif a.command=='register':result=registry.register_primary(a.volume,a.ingest_folder,a.label)
        elif a.command=='status':
            result=registry.read()
            result['availability']={identity:registry.availability(identity) for identity in result['profiles']}
        elif a.command=='refresh-mount':result=registry.refresh_mount(a.primary,a.volume)
        elif a.command=='ledger-path':result={'ledger':str(registry.ledger_path(a.primary))}
        elif a.command=='backup-register':result=registry.register_backup(a.primary,a.label,json.loads(a.readback.read_text()),reviewed=True)
        elif a.command=='backup-current':result=registry.set_current_backup(a.primary,a.label)
        elif a.command=='begin':result=registry.begin_cutover(a.primary)
        elif a.command=='target-config':result=registry.target_config()
        elif a.command=='record-step':result=registry.record_cutover(a.stage,json.loads(a.evidence.read_text()))
        print(json.dumps(result,indent=2));return 0
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        print(json.dumps({'error':str(exc),'app_configuration_changed':False}),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
