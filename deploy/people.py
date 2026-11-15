#!/usr/bin/env python3
"""Back up and migrate one project to unified People without rewriting legacy data."""
import argparse,hashlib,json,os,sqlite3,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ditnotes.atomic import atomic_json,fsync_dir
from ditnotes.project_store import ProjectStore

def legacy_fingerprints(db):
 names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'unified_%' AND name NOT LIKE 'sqlite_%' AND name NOT IN ('meta','audit') ORDER BY name")]
 result={}
 for name in names:
  if not name.replace('_','').isalnum():raise ValueError('Unexpected registry table name')
  h=hashlib.sha256();count=0
  for row in db.execute('SELECT * FROM "'+name+'" ORDER BY rowid'):
   h.update(json.dumps(tuple(row),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode());h.update(b'\n');count+=1
  result[name]={'rows':count,'sha256':h.hexdigest()}
 return result

def migrate(project):
 project=Path(project).expanduser().resolve(strict=True);db=project/'_DIT_NOTES/project.sqlite3'
 if not db.is_file() or db.is_symlink():raise ValueError('Project registry is missing or unsafe')
 backupdir=project/'_DIT_NOTES/backups';backupdir.mkdir(exist_ok=True)
 if backupdir.is_symlink():raise ValueError('Backup directory cannot be a symlink')
 backup=backupdir/('before-unified-people-'+str(time.time_ns())+'.sqlite3');pending=backup.with_suffix('.pending')
 try:
  with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(pending) as target:
   source.backup(target)
   if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed')
  with pending.open('rb') as f:os.fsync(f.fileno())
  os.replace(pending,backup);fsync_dir(backupdir)
 except BaseException:
  # Keep a partial backup for diagnosis; never touch the source on failure.
  raise
 with ProjectStore(db) as store:
  with store._transaction():
   before=legacy_fingerprints(store.db);audit=store.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]
   result=store.ensure_unified_people();after=legacy_fingerprints(store.db)
   if before!=after:raise RuntimeError('Migration changed legacy data; transaction rolled back')
   people=store.unified_people(ensure=False)
   report={'project_id':store.project_id,'backup':str(backup),'migration':result,'legacy_tables':before,'legacy_unchanged':True,'people':len(people),'name_conflicts':sum(p['name_conflict'] for p in people),'evidence_groups':sum(len(p['members']) for p in people),'audit_rows_before':audit,'audit_rows_after':store.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0]}
  if store.db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Registry integrity check failed')
 atomic_json(backup.with_suffix('.receipt.json'),report)
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True);a=p.parse_args()
 print(json.dumps(migrate(a.project),indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
