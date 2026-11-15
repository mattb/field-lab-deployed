#!/usr/bin/env python3
"""Resolve Field Lab's newest published GitHub release, including prereleases.

Read-only. GitHub /releases/latest omits prereleases and is unsuitable here.
This discovers a tag; it does not check out code, activate it, or configure apps.
"""
import datetime
import argparse
from pathlib import Path
import json
import re
import urllib.request

API='https://api.github.com/repos/mattb/field-lab-deployed/releases'


def select(releases):
    eligible=[]
    for release in releases:
        if release.get('draft') or not release.get('published_at'):
            continue
        tag=release.get('tag_name','')
        if not isinstance(tag,str) or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?',tag):
            continue
        try:stamp=datetime.datetime.fromisoformat(release['published_at'].replace('Z','+00:00'))
        except (ValueError,TypeError):continue
        if stamp.tzinfo is None:continue
        eligible.append((stamp,tag,release))
    if not eligible:raise ValueError('No published Field Lab release found')
    release=max(eligible,key=lambda item:(item[0],item[1]))[2]
    return {'repository':'https://github.com/mattb/field-lab-deployed','tag':release['tag_name'],
            'published_at':release['published_at'],'prerelease':bool(release.get('prerelease')),
            'release_url':'https://github.com/mattb/field-lab-deployed/releases/tag/'+release['tag_name'],
            'next':'Fetch this exact tag, verify its FIELD-LAB-RELEASE.json tag, and follow its update_entrypoint'}


def resolve(opener=urllib.request.urlopen):
    releases=[]
    for page in range(1,11):
        request=urllib.request.Request(API+'?per_page=100&page='+str(page),headers={'Accept':'application/vnd.github+json','User-Agent':'Field-Lab-Release-Resolver'})
        with opener(request,timeout=20) as response:batch=json.load(response)
        if not isinstance(batch,list):raise ValueError('Unexpected GitHub releases response')
        releases.extend(batch)
        if len(batch)<100:return select(releases)
    raise ValueError('Release inventory exceeded bound; resolve explicitly rather than guessing')


def validate_manifest(checkout,tag):
    root=Path(checkout).resolve(strict=True)
    manifest=root/'FIELD-LAB-RELEASE.json'
    if manifest.is_symlink():raise ValueError('Release manifest must be a regular file')
    value=json.loads(manifest.read_text())
    if value.get('schema')!=1 or value.get('tag')!=tag:raise ValueError('Release manifest tag/schema mismatch')
    procedures=[value['update_entrypoint']]
    migrations=value['workflow_migrations']
    if not isinstance(migrations,list):raise ValueError('Invalid workflow migrations')
    seen=set()
    for migration in migrations:
        identity=migration['id']
        if not isinstance(identity,str) or not identity or identity in seen:raise ValueError('Invalid/duplicate migration ID')
        seen.add(identity);procedures.append(migration['procedure'])
    for relative in procedures:
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or not path.parts:raise ValueError('Procedure escapes selected release')
        actual=root/path
        if any(p.is_symlink() for p in [actual,*actual.parents] if p!=root and root in p.parents):raise ValueError('Procedure uses symlink')
        if not actual.is_file() or not actual.resolve().is_relative_to(root):raise ValueError('Procedure is missing/outside selected release')
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',type=Path);parser.add_argument('--tag')
    args=parser.parse_args()
    try:
        if bool(args.checkout)!=bool(args.tag):raise ValueError('--checkout and --tag must be supplied together')
        print(json.dumps(validate_manifest(args.checkout,args.tag) if args.checkout else resolve(),indent=2))
    except (OSError,ValueError,KeyError,TypeError) as exc:raise SystemExit('Cannot resolve/validate a published release: '+str(exc))
