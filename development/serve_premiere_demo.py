#!/usr/bin/env python3
"""Serve an explicitly synthetic editorial fixture without loading ML models."""
import argparse,json,sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


class NoInference:
    def __init__(self,*args,**kwargs):pass
    def readiness(self):return {'ready':False,'reason':'Synthetic editorial demo; inference disabled'}
    def close(self):pass


def serve(project,port,native_name):
    from ditnotes.server import create_server
    from ditnotes.premiere_bridge import PremiereBridge
    project=Path(project).expanduser().resolve(strict=True)
    catalog=json.loads((project/'_DIT_NOTES/catalog.json').read_text())
    if not catalog.get('project',{}).get('synthetic') or not (project/'SYNTHETIC-TEST-ONLY.txt').is_file():
        raise ValueError('This server is restricted to a generated synthetic test project')
    if Path(native_name).name!=native_name or not native_name.endswith('.prproj'):
        raise ValueError('Use a Premiere filename within the synthetic project')
    bridge=PremiereBridge(project)
    if not bridge.status()['paired']:
        bridge.prepare(str(project/native_name))
    bridge.queue_snapshot()
    with patch('ditnotes.server.SearchBackend',NoInference):
        server=create_server(project,port,ROOT)
    print(f'Field Lab demo: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--port',type=int,default=18798)
    parser.add_argument('--native-name',default='Field Lab Demo.prproj')
    args=parser.parse_args()
    serve(args.project,args.port,args.native_name)
