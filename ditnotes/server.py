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


class _ProjectServer(ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, project: Path, port: int, tool_root: Path):
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
        self.metadata=MetadataAPI(self)
        self.chat_init_lock=threading.Lock()

    def resolve_image(self,url):
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
        if hasattr(self,'chat'):self.chat.close()
        if hasattr(self,'research'):self.research.close()
        if hasattr(self,'metadata'):self.metadata.close()
        if hasattr(self,'search_backend'):self.search_backend.close()


class _Handler(BaseHTTPRequestHandler):
    server: _ProjectServer
    server_version = 'FieldnotesLocal/1'

    def log_message(self, *_args):
        pass

    def do_POST(self):
        from .chat import handle_chat_post
        from .research_api import handle_research_post
        if not handle_research_post(self) and not handle_chat_post(self) and not handle_post(self):serve_model_request(self,self.server.search_backend)

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
            if path == '/api/research':
                from .research_api import get_research
                self._json(get_research(self.server).state(),head);return
            if path == '/api/chat':
                from .chat import get_chat
                from urllib.parse import parse_qs,urlsplit
                self._json(get_chat(self.server).state(parse_qs(urlsplit(self.path).query).get('session_id',[None])[0]),head);return
            if path == '/api/catalog':
                self._json(self.server.metadata.catalog(), head)
                return
            if path in ('/api/annotations-state','/api/metadata'):
                self._json(self.server.metadata.state(),head);return
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


def create_server(project: Path, port: int = 8766, tool_root: Path = ROOT):
    """Construct a server without starting it, useful to embed or test."""
    return _ProjectServer(Path(project), int(port), Path(tool_root))


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
