"""Stable callback/Desktop dispatcher; serialize against release switches."""
import argparse,fcntl,json,subprocess,time,urllib.error,urllib.request
from pathlib import Path

def dispatch(root,command):
    if command=='backup-current':
        subprocess.run([str(root/'.venv/bin/python'),str(root/'deploy/backup_shortcut.py'),'run-current','--home',str(root.parent.parent)],check=True,cwd=root)
        return
    if command=='ingest':
        # Validate this machine's chosen destination before waking its apps.
        subprocess.run([str(root/'.venv/bin/python'),'-c','from ingest_service import config,verify_volume,ROOT; from ditnotes.primary_routing import ensure_not_cutting_over; c=config(); ensure_not_cutting_over(ROOT,c); verify_volume(c)'],check=True,cwd=root)
        subprocess.run(['/usr/bin/open','-a','/Applications/OffShoot.app'],check=True)
    action='start' if command in ('ingest','library','progress') else command
    subprocess.run([str(root/'.venv/bin/python'),str(root/'ingest_service.py'),action],check=True,cwd=root)
    if command=='library':
        config=json.loads((root/'config.json').read_text());url='http://127.0.0.1:'+str(int(config['port']))+'/'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+30
        while True:
            try:
                with opener.open(url+'api/status',timeout=1) as response:
                    if response.status==200:break
            except (OSError,urllib.error.URLError):pass
            if time.monotonic()>deadline:raise RuntimeError('Library did not become available. Open AI Progress and inspect the service status.')
            time.sleep(.2)
        subprocess.run(['/usr/bin/open',url],check=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--home',type=Path,required=True);p.add_argument('command',choices=['start','stop','recover','reconcile','ingest','library','progress','backup-current']);a=p.parse_args()
    home=a.home.expanduser().resolve()
    with (home/'deploy.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_SH)
        root=(home/'current').resolve(strict=True)
        if root.parent!=home/'releases':raise ValueError('Invalid managed release')
        dispatch(root,a.command)
if __name__=='__main__':main()
