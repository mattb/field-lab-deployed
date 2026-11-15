"""Loopback media browser and durable project annotation API."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from email.utils import formatdate, parsedate_to_datetime
import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import threading
import os
from pathlib import Path
import re
import secrets
import hmac
import stat
import threading
from urllib.parse import unquote, urlsplit

from .search_backend import SearchBackend, serve_model_request
from .metadata_api import MetadataAPI, handle_post

ROOT = Path(__file__).resolve().parents[1]


def _parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or relative.startswith('/') or '\x00' in relative:
        raise PermissionError('Invalid relative path')
    parts = tuple(relative.split('/'))
    if any(p in ('', '.', '..') for p in parts):
        raise PermissionError('Path traversal is not allowed')
    return parts


@contextmanager
def _open_file(root: Path, relative: str):
    """Walk directory descriptors without following symlinks, including races."""
    parts = _parts(relative)
    directory = None
    descriptor = None
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PermissionError('Only regular files can be served')
        stream = os.fdopen(descriptor, 'rb')
        descriptor = None
        with stream:
            yield stream
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


class _JSONFile:
    def __init__(self, root: Path, relative: str, missing: dict):
        self.root, self.relative, self.missing = root, relative, missing
        self.signature = None
        self.value = missing
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            try:
                with _open_file(self.root, self.relative) as stream:
                    info = os.fstat(stream.fileno())
                    signature = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    if signature != self.signature:
                        value = json.load(stream)
                        if not isinstance(value, dict):
                            raise ValueError('Expected a JSON object')
                        self.value, self.signature = value, signature
            except FileNotFoundError:
                self.signature, self.value = None, self.missing
            return self.value


def _decode_path(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith('/'):
        raise PermissionError('Only local origin-form URLs are accepted')
    try:
        return unquote(parsed.path, encoding='utf-8', errors='strict')
    except UnicodeDecodeError as error:
        raise PermissionError('Invalid URL encoding') from error


def _media_map(catalog: dict) -> dict[str, str]:
    allowed = {}
    assets = catalog.get('assets', [])
    if not isinstance(assets, list):
        return allowed
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        relative = asset.get('relative_path')
        try:
            _parts(relative)
        except (PermissionError, TypeError):
            continue
        # Only a currently declared source URL authorizes access. Historical aliases
        # and arbitrary files elsewhere in the project never become public routes.
        for field in ('media_url', 'original_url', 'source_url'):
            value = asset.get(field)
            if not isinstance(value, str):
                continue
            try:
                path = _decode_path(value)
            except (PermissionError, ValueError):
                continue
            if path.startswith('/media/') and path[len('/media/'):] == relative:
                allowed[path] = relative
    return allowed


def _byte_range(value: str | None, size: int):
    if value is None:
        return None
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip(), re.IGNORECASE)
    if not match or size == 0 or not any(match.groups()):
        raise ValueError('Unsupported or unsatisfiable range')
    left, right = match.groups()
    if not left:
        suffix = int(right)
        if suffix <= 0:
            raise ValueError('Invalid suffix range')
        return max(0, size - suffix), size - 1
    start = int(left)
    end = min(int(right), size - 1) if right else size - 1
    if start >= size or end < start:
        raise ValueError('Unsatisfiable range')
    return start, end


def _backup_attempt_summaries(library):
    """Read only registered pair receipts; never create directories or start CCC."""
    result=[]
    for primary,profile in library.registry()['profiles'].items():
        for label,entry in profile.get('backup',{}).get('sets',{}).items():
            summary={'primary_id':primary,'set_label':label,'task_uuid':entry.get('task_uuid'),
                     'state':'unreadable','needs_history_review':True,'backup_verified':False}
            try:
                # _open_file rejects symlinked ancestors and final files alike.
                relative='runtime/backup-shortcut/attempts/'+primary+'-'+label+'.json'
                with _open_file(library.shared,relative) as stream:
                    if os.fstat(stream.fileno()).st_size>2*1024*1024:raise ValueError('Oversized backup attempt')
                    value=json.load(stream)
                if not isinstance(value,dict) or value.get('primary_id')!=primary or value.get('set_label')!=label or value.get('task_uuid')!=entry.get('task_uuid'):
                    raise ValueError('Backup attempt does not match the registered pair')
                state=value.get('state')
                if state not in ('launching','running','unknown','ccc_finished','reconciled'):raise ValueError('Unknown backup attempt state')
                summary.update(state=state,needs_history_review=state in ('launching','running','unknown'))
                for key in ('attempt_id','created_at','updated_at','ccc_exit_code'):
                    if isinstance(value.get(key),(str,int,float)) and not isinstance(value.get(key),bool):summary[key]=value[key]
            except FileNotFoundError:
                continue
            except (OSError,ValueError,TypeError):
                summary['message']='Backup attempt cannot be read safely; review CCC history before starting again.'
            if summary['needs_history_review']:
                summary.setdefault('message','Prior CCC outcome is unknown. Review CCC task history and reconcile this backup set before starting again.')
            elif summary['state']=='ccc_finished':
                summary['message']='CCC request finished. Review its result and verification in CCC.'
            else:
                summary['message']='Prior attempt reconciled from CCC task history.'
            result.append(summary)
    return result


class _ProjectServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, project: Path, port: int, tool_root: Path, shoot_library=None):
        from .multivolume import MultiVolumeLibrary
        self.shoot_library=shoot_library or MultiVolumeLibrary.discover(tool_root)
        offline_bootstrap=False
        self.primary_id=None
        if self.shoot_library:
            matching=[pid for pid,p in self.shoot_library.registry()['profiles'].items() if Path(p['project_root'])==project.absolute()]
            self.primary_id=matching[0] if matching else None
            if not matching or not self.shoot_library.available(matching[0]):
                project=self.shoot_library.bootstrap_project();offline_bootstrap=True
        self.project = project.resolve(strict=True)
        self.tool_root = tool_root.resolve(strict=True)
        if not self.project.is_dir() or not self.tool_root.is_dir():
            raise ValueError('Project and tool roots must be directories')
        from .catalog_index import Snapshots
        self.catalog = Snapshots(self.project)
        self.search_vectors = self.catalog.vectors
        missing = {'ready': False, 'models': {k: {'ready': False} for k in ('embeddings', 'reranker', 'clip')},
                   'runtime': {'ready': False}, 'reason': 'Search assets have not been prepared'}
        self.model_asset_root = (self.tool_root / 'models').resolve()
        self.readiness = _JSONFile(self.model_asset_root, '.runtime/search-readiness.json', missing)
        self.search_backend=SearchBackend(self.tool_root,self.resolve_image,warm_start=True)
        super().__init__(('127.0.0.1', port), _Handler)
        from .premiere_bridge import PremiereJournalWorker
        if self.shoot_library and not offline_bootstrap:
            # Root compatibility APIs share the same identity-guarded metadata
            # context as scoped requests, including its background publisher.
            from .multivolume import primary_context
            self.metadata=primary_context(self.shoot_library,self.primary_id,self).metadata
            library,primary=self.shoot_library,self.primary_id
            class VerifiedPremiereWorker(PremiereJournalWorker):
                def tick(worker):
                    library.require_profile(primary)
                    return super().tick()
            self.premiere_worker=VerifiedPremiereWorker(self.project,self.catalog.read).start()
        else:
            self.metadata=MetadataAPI(self)
            if not offline_bootstrap:self.premiere_worker=PremiereJournalWorker(self.project,self.catalog.read).start()
        if self.shoot_library:self.shoot_library.refresh()
        self.chat_init_lock=threading.Lock()
        self.backup_action_lock=threading.Lock();self.backup_status_lock=threading.Lock()
        self.backup_csrf=secrets.token_urlsafe(32)
        self.backup_status={'running':False,'status':'idle','message':'','error':None,'primary_id':None,'set_label':None}

    def resolve_image(self,url):
        if self.shoot_library and url.startswith('/primaries/'):
            return self.shoot_library.resolve_image(url)
        allowed={h.get('image_url') for a in self.catalog.read().get('assets',[]) for h in a.get('highlights',[])}
        if url not in allowed or not url.startswith('/notes/'):raise ValueError('Still is not in the current catalog')
        rel='_DIT_NOTES/'+_decode_path(url)[len('/notes/'):]
        with _open_file(self.project,rel):pass
        return self.project/rel

    def server_close(self):
        # Drain request handlers before closing the executors they can submit to.
        # The daemon stop command bounds the whole shutdown and can terminate its
        # process group; service.lock remains held until all writers are done.
        super().server_close()
        if self.shoot_library:self.shoot_library.close()
        if hasattr(self,'premiere_worker'):self.premiere_worker.close()
        if hasattr(self,'chat'):self.chat.close()
        if hasattr(self,'metadata'):self.metadata.close()
        if hasattr(self,'search_backend'):self.search_backend.close()


class _Handler(BaseHTTPRequestHandler):
    server: _ProjectServer
    server_version = 'FieldnotesLocal/1'

    def log_message(self, *_args):
        pass

    def do_POST(self):
        if getattr(self.server,'shoot_library',None) and urlsplit(self.path).path=='/api/shoot/backup':
            self._backup_request();return
        if self._primary_request(False,post=True):return
        if getattr(self.server,'shoot_library',None) and urlsplit(self.path).path!='/api/search-models':
            self._json({'error':'Choose a primary volume before changing project metadata'},False,409);return
        from .chat import handle_chat_post
        from .premiere_http import handle as handle_premiere
        if not handle_premiere(self) and not handle_chat_post(self) and not handle_post(self):serve_model_request(self,self.server.search_backend)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def _json(self, value, head, code=200):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _serve(self, head):
        try:
            host = self.headers.get('Host', '').split(':', 1)[0].lower()
            if host not in ('127.0.0.1', 'localhost'):
                self.send_error(403, 'Loopback host required')
                return
            path = _decode_path(self.path)
            if getattr(self.server,'shoot_library',None):
                if path.startswith('/api/shoot'):
                    self._shoot_request(path,head);return
                if self._primary_request(head):return
                if path.startswith('/api/') and path not in ('/api/status','/api/readiness'):
                    primary=self.server.primary_id
                    if not primary:
                        self._json({'error':'Choose a primary volume'},head,409);return
                    if not self.server.shoot_library.available(primary):
                        from urllib.parse import parse_qs
                        self._json(self.server.shoot_library.cached_api(primary,path,parse_qs(urlsplit(self.path).query)),head);return
                    self.server.shoot_library.require_profile(primary)
            if path.startswith('/api/premiere/'):
                from .premiere_http import handle as handle_premiere
                handle_premiere(self, head)
                return
            if path == '/api/chat':
                from .chat import get_chat
                from urllib.parse import parse_qs
                self._json(get_chat(self.server).state(parse_qs(urlsplit(self.path).query).get('session_id',[None])[0]),head);return
            if path == '/api/catalog':
                self._json(self.server.metadata.catalog(), head)
                return
            if path == '/api/people/review':
                self._json(self.server.metadata.people_review(),head);return
            if path == '/api/browse':
                self._json(self.server.metadata.browse(),head);return
            if path in ('/api/annotations-state','/api/metadata'):
                self._json(self.server.metadata.state(),head);return
            if path=='/api/asset-tags':
                from urllib.parse import parse_qs
                query=parse_qs(urlsplit(self.path).query)
                try:
                    if set(query)!={'content_id'} or len(query['content_id'])!=1:raise ValueError('One content_id is required')
                    self._json(self.server.metadata.asset_tags(query['content_id'][0]),head)
                except ValueError as error:self._json({'error':str(error)},head,400)
                return
            if path=='/api/face/review':
                self._json(self.server.metadata.face_review(),head);return
            if path == '/api/status':
                self._json(self.server.get_status() if hasattr(self.server,'get_status') else {'phase':'ready','pending':0,'active':None,'last_error':None},head);return
            if path == '/api/search-vectors':
                self._json(self.server.search_vectors() if hasattr(self.server,'search_vectors') else {'revision':{},'vectors':[]},head);return
            if path == '/api/readiness':
                # Cached JSON/stat checks only: no per-request model hashing or downloads.
                self._json({**self.server.readiness.read(),'backend':self.server.search_backend.readiness()}, head)
                return
            if path.startswith('/api/'):
                self.send_error(404)
                return
            if path.startswith(('/media/','/notes/')) and getattr(self.server,'shoot_library',None):
                if not self.server.primary_id:raise PermissionError('Choose a primary volume')
                self.server.shoot_library.require_profile(self.server.primary_id)
            if path.startswith('/media/'):
                relative = _media_map(self.server.catalog.read()).get(path)
                if relative is None:
                    self.send_error(404, 'Source is not in the current catalog')
                    return
                self._file(self.server.project, relative, head)
                return
            for prefix, base, folder in (
                ('/notes/', self.server.project, '_DIT_NOTES'),
                ('/models/', self.server.model_asset_root, '.runtime/search-models'),
                ('/runtime/', self.server.model_asset_root, '.runtime/browser'),
            ):
                if path.startswith(prefix):
                    relative = path[len(prefix):]
                    if relative.endswith('/'):
                        relative += 'index.html'
                    self._file(base, folder + '/' + relative, head)
                    return
            relative = path.lstrip('/') or 'index.html'
            try:
                self._file(self.server.tool_root, 'viewer/dist/' + relative, head)
            except FileNotFoundError:
                if Path(relative).suffix or relative.startswith('assets/'):
                    raise
                self._file(self.server.tool_root, 'viewer/dist/index.html', head)
        except (PermissionError, UnicodeError, ValueError) as error:
            self.send_error(403 if isinstance(error, PermissionError) else 503,
                            'Path not permitted' if isinstance(error, PermissionError) else 'Local metadata is unavailable')
        except (FileNotFoundError, NotADirectoryError):
            self.send_error(404)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.EACCES, errno.EPERM):
                self.send_error(403, 'Path not permitted')
            elif error.errno in (errno.ENOENT, errno.ENOTDIR):
                self.send_error(404)
            else:
                self.send_error(500, 'Local file could not be read')

    def _shoot_request(self,path,head):
        library=self.server.shoot_library
        library.refresh()
        if path=='/api/shoot/backup':
            with self.server.backup_status_lock:status=dict(self.server.backup_status)
            status['attempts']=_backup_attempt_summaries(library)
            if status['status']=='idle':
                uncertain=next((a for a in status['attempts'] if a['needs_history_review']),None)
                if uncertain:status.update(status='needs_history_review',message=uncertain['message'],primary_id=uncertain['primary_id'],set_label=uncertain['set_label'])
            self._json(status,head)
            return
        if path=='/api/shoot':
            self._json({**library.status(),'csrf_token':self.server.backup_csrf},head);return
        functions={'/api/shoot':library.status,'/api/shoot/catalog':library.catalog,
                   '/api/shoot/browse':library.browse,'/api/shoot/people':library.people,
                   '/api/shoot/vectors':library.vectors}
        if path not in functions:self._json({'error':'Unknown shoot operation'},head,404);return
        self._json(functions[path](),head)

    def _backup_request(self):
        server=self.server;library=server.shoot_library
        host=self.headers.get('Host','')
        if host not in (f'127.0.0.1:{server.server_port}',f'localhost:{server.server_port}') or self.headers.get('Origin')!='http://'+host or not hmac.compare_digest(self.headers.get('X-Field-Lab-CSRF',''),server.backup_csrf):
            self._json({'error':'Same-origin authenticated action required'},False,403);return
        try:
            if self.headers.get('Transfer-Encoding') is not None:raise ValueError('Unsupported transfer encoding')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=4096 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Small JSON request required')
            self.connection.settimeout(3);value=json.loads(self.rfile.read(length))
            if not isinstance(value,dict) or set(value)!={'primary_id','set_label'}:raise ValueError('Expected primary_id and set_label')
            primary,label=value['primary_id'],value['set_label']
            if not isinstance(primary,str) or not isinstance(label,str):raise ValueError('Invalid primary or backup set')
            registry=library.registry()
            if registry.get('pending_cutover'):raise ValueError('Finish or reconcile the pending cutover before backups')
            profile=library.profile(primary);library.require_profile(primary)
            if label not in profile.get('backup',{}).get('sets',{}):raise ValueError('Unknown backup set for this primary')
        except (ValueError,TypeError,KeyError,OSError) as error:
            self._json({'error':str(error)},False,409);return
        if not server.backup_action_lock.acquire(blocking=False):self._json({'error':'A backup request is already running'},False,409);return
        with server.backup_status_lock:server.backup_status={'running':True,'status':'running','message':'CCC backup request running','error':None,'primary_id':primary,'set_label':label}
        def run():
            try:
                from deploy.backup_shortcut import run_primary_task
                result=run_primary_task(library.shared.parent,primary,label)
                with server.backup_status_lock:server.backup_status.update(status='finished',message='CCC request finished; review verification in CCC',result=result)
            except Exception as error:
                with server.backup_status_lock:server.backup_status.update(status='error',message='Backup request needs review',error=str(error))
            finally:
                with server.backup_status_lock:server.backup_status['running']=False
                server.backup_action_lock.release()
        try:threading.Thread(target=run,name='primary-backup-request',daemon=True).start()
        except Exception:
            server.backup_action_lock.release()
            with server.backup_status_lock:server.backup_status.update(running=False,status='error',error='Could not launch request')
            self._json({'error':'Could not launch backup request'},False,503);return
        self._json({'started':True,'primary_id':primary,'set_label':label,'verification_certified':False},False,202)

    def _primary_request(self,head,post=False):
        library=getattr(self.server,'shoot_library',None)
        if not library:return False
        parsed=urlsplit(self.path)
        if not parsed.path.startswith('/primaries/'):return False
        match=re.fullmatch(r'/primaries/([A-Za-z0-9_-]{1,128})(/.*)',_decode_path(self.path))
        if not match:self._json({'error':'Invalid primary route'},head,403);return True
        primary,path=match.groups()
        try:
            profile=library.profile(primary)
            if not post and path.startswith('/notes/'):
                relative=unquote(path[len('/notes/'):]);image=library.resolve_image('/primaries/'+primary+'/notes/'+relative)
                self._file(image.parent,image.name,head);return True
            if not post and path.startswith('/media/'):
                project=library.require_profile(primary);cached=library.cached(primary)
                if not cached:raise FileNotFoundError('No catalogue')
                relative=_media_map(cached['catalog']).get(path)
                if relative is None:raise FileNotFoundError('Source is not in the primary catalogue')
                self._file(project,relative,head);return True
            if not library.available(primary):
                if post:self._json({'error':'Connect '+profile.get('label',primary)+' to save changes','available':False},head,409);return True
                from urllib.parse import parse_qs
                self._json(library.cached_api(primary,path,parse_qs(parsed.query)),head);return True
            from .multivolume import primary_context
            context=primary_context(library,primary,self.server)
            original_server,original_path=self.server,self.path
            try:
                self.server=context;self.path=path+('?' + parsed.query if parsed.query else '')
                if post:
                    from .chat import handle_chat_post
                    from .premiere_http import handle as handle_premiere
                    if not handle_premiere(self) and not handle_chat_post(self) and not handle_post(self):self._json({'error':'Unknown primary operation'},False,404)
                    library.refresh(primary)
                else:self._serve(head)
            finally:self.server,self.path=original_server,original_path
            return True
        except (OSError,ValueError,PermissionError,KeyError) as error:
            self._json({'error':str(error),'available':False},head,409 if post else 404);return True

    def _file(self, root: Path, relative: str, head: bool):
        with _open_file(root, relative) as stream:
            info = os.fstat(stream.fileno())
            size = info.st_size
            etag = f'"{info.st_ino:x}-{info.st_size:x}-{info.st_mtime_ns:x}"'
            range_header = self.headers.get('Range')
            if_range = self.headers.get('If-Range')
            if if_range and range_header:
                matches = if_range.strip() == etag
                if not matches:
                    try:
                        matches = int(info.st_mtime) <= parsedate_to_datetime(if_range).timestamp()
                    except (ValueError, TypeError, OverflowError):
                        matches = False
                if not matches:
                    range_header = None
            try:
                requested = _byte_range(range_header, size)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.send_header('Accept-Ranges', 'bytes')
                self.end_headers()
                return
            start, end = requested if requested else (0, size - 1)
            length = max(0, end - start + 1)
            media_type = {'.wasm': 'application/wasm', '.mjs': 'application/javascript',
                          '.wav': 'audio/wav', '.vtt': 'text/vtt', '.srt': 'text/plain'}.get(Path(relative).suffix.lower())
            media_type = media_type or mimetypes.guess_type(relative)[0] or 'application/octet-stream'
            self.send_response(206 if requested else 200)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('ETag', etag)
            self.send_header('Last-Modified', formatdate(info.st_mtime, usegmt=True))
            self.send_header('X-Content-Type-Options', 'nosniff')
            if requested:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            if head or length == 0:
                return
            stream.seek(start)
            try:
                while length:
                    chunk = stream.read(min(1024 * 1024, length))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    length -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass


def create_server(project: Path, port: int = 8766, tool_root: Path = ROOT, *, shoot_library=None):
    """Construct a server without starting it, useful to embed or test."""
    return _ProjectServer(Path(project), int(port), Path(tool_root),shoot_library=shoot_library)


def serve(project: Path, port: int = 8766, tool_root: Path = ROOT):
    server = create_server(project, port, tool_root)
    print(f'Fieldnotes: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--tool-root', type=Path, default=ROOT)
    args = parser.parse_args()
    serve(args.project, args.port, args.tool_root)
