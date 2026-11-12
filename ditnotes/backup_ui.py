"""Loopback-only, opt-in backup rotation UI; persistence belongs to the manager.

``serve(manager, port=0)`` returns an HTTP server. The caller owns serve_forever
and shutdown. The manager supplies status() and run_pending(); no task settings
or filesystem paths are accepted from browser requests.
"""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading


PAGE = r'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backups</title><style>
*{box-sizing:border-box}body{font:16px Helvetica,Arial,sans-serif;color:#222;background:#fff;margin:0;padding:32px;max-width:900px}h1{font-size:25px;font-weight:500;margin:0 0 24px}button{font:inherit;color:#fff;background:#222;border:1px solid #222;border-radius:5px;padding:10px 16px;cursor:pointer}button:disabled{opacity:.5;cursor:default}#refresh{color:#222;background:white;margin-left:8px}#message{margin:24px 0;white-space:pre-wrap}section{border-top:1px solid #ddd;padding:16px 0}h2{font-size:17px;margin:0 0 8px}p{margin:5px 0;color:#555;white-space:pre-wrap;overflow-wrap:anywhere}.error{color:#222;font-weight:600}small{display:block;color:#555;margin-top:20px}
</style><h1>Backups</h1><button id="run">Back up pending footage</button><button id="refresh">Refresh</button>
<div id="message" role="status" aria-live="polite">Loading…</div><div id="items"></div>
<script>
'use strict';const token=__TOKEN__;let refreshing=false;
const message=document.getElementById('message'),items=document.getElementById('items'),run=document.getElementById('run');
function text(tag,value,parent){const e=document.createElement(tag);e.textContent=String(value);parent.appendChild(e);return e}
function size(value){let n=Number(value);if(!Number.isFinite(n)||n<0)return '';const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1000&&i<units.length-1){n/=1000;i++}return (i===0?String(Math.round(n)):n.toFixed(n<10?1:0))+' '+units[i]}
function section(title){const s=document.createElement('section');items.appendChild(s);text('h2',title,s);return s}
function drive(row){const s=section(row.label||row.name||row.id||'Backup drive');const state=row.state==='Closed'?'Closed':'Available';const amount=size(row.bytes);text('p',state+(amount?' · '+amount+' assigned':''),s);const m=String(row.message||'');if(m==='Metadata pending')text('p','Notes awaiting backup',s);else if(m.startsWith('Metadata '))text('p','Notes backed up',s);else if(m==='No metadata snapshot')text('p','Notes not yet backed up',s);if(row.error)text('p',row.error,s).className='error'}
function footage(row){const s=section(row.label||row.relative||row.name||'Footage');const label=String(row.set||row.message||'').match(/^B[0-9]{3,}$/);const states={pending:'Awaiting backup',assigned:'Assigned',copying:'Copying',verified:'Backed up'};text('p',(states[row.state]||'Awaiting backup')+(label?' · '+label[0]:''),s);const amount=size(row.bytes);if(amount)text('p',amount,s);if(row.error)text('p',row.error,s).className='error'}
function render(s){message.textContent=s.ui_error||s.message||'Ready';run.disabled=!!s.ui_running;items.replaceChildren();const drives=s.sets||s.drives||[],jobs=s.jobs||s.items||s.units||[];if(drives.length){text('h2','Drives',items);for(const row of drives)if(row&&typeof row==='object')drive(row)}if(jobs.length){text('h2','Footage',items);for(const row of jobs)if(row&&typeof row==='object')footage(row)}}
async function refresh(){if(refreshing)return;refreshing=true;try{const r=await fetch('/api/status',{cache:'no-store'});const s=await r.json();if(!r.ok)throw Error(s.error||'Status unavailable');render(s)}catch(e){message.textContent='Status unavailable: '+e.message;run.disabled=true}finally{refreshing=false}}
run.addEventListener('click',async()=>{run.disabled=true;try{const r=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json','X-Field-Lab-CSRF':token},body:'{}'});const s=await r.json();if(!r.ok)throw Error(s.error||'Could not begin backup');message.textContent='Checking pending footage…'}catch(e){message.textContent=e.message}await refresh()});document.getElementById('refresh').addEventListener('click',refresh);refresh();setInterval(refresh,2000);
</script></html>'''


class BackupServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, manager, port=0):
        self.manager = manager
        self.csrf_token = secrets.token_urlsafe(32)
        self.action_lock = threading.Lock()
        self.ui_lock = threading.Lock()
        self.ui_running = False
        self.ui_error = None
        super().__init__(('127.0.0.1', port), BackupHandler)
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.host = '127.0.0.1:' + str(self.server_address[1])

    def run_pending(self):
        try:
            self.manager.run_pending()
        except Exception as exc:
            with self.ui_lock:
                self.ui_error = str(exc) or type(exc).__name__
        finally:
            with self.ui_lock:
                self.ui_running = False
            self.action_lock.release()


class BackupHandler(BaseHTTPRequestHandler):
    server: BackupServer
    protocol_version = 'HTTP/1.0'

    def log_message(self, *_args):
        pass

    def _respond(self, code, body, content_type='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _host_ok(self):
        # Reject DNS rebinding and alternate hostnames even on the loopback listener.
        return self.headers.get('Host') == self.server.host

    def do_GET(self):
        if not self._host_ok():
            self._respond(403, {'error': 'Unexpected host'})
            return
        if self.path == '/':
            self._respond(200, PAGE.replace('__TOKEN__', json.dumps(self.server.csrf_token)), 'text/html; charset=utf-8')
        elif self.path == '/api/status':
            try:
                status = dict(self.server.manager.status())
                with self.server.ui_lock:
                    status.update(ui_running=self.server.ui_running, ui_error=self.server.ui_error)
                self._respond(200, status)
            except Exception as exc:
                self._respond(503, {'error': str(exc) or type(exc).__name__})
        else:
            self._respond(404, {'error': 'Not found'})

    def do_POST(self):
        if not self._host_ok() or self.headers.get('Origin') != self.server.origin:
            self._respond(403, {'error': 'Unexpected origin'})
            return
        if not hmac.compare_digest(self.headers.get('X-Field-Lab-CSRF', ''), self.server.csrf_token):
            self._respond(403, {'error': 'Invalid request token'})
            return
        if self.path != '/api/run':
            self._respond(404, {'error': 'Not found'})
            return
        try:
            if self.headers.get('Transfer-Encoding') is not None:
                raise ValueError('Unsupported transfer encoding')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError('Expected a small JSON request')
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ValueError('Expected JSON')
            self.connection.settimeout(3)
            if json.loads(self.rfile.read(length)) != {}:
                raise ValueError('No request parameters are accepted')
        except (ValueError, OSError):
            self._respond(400, {'error': 'Expected an empty JSON object'})
            return
        if not self.server.action_lock.acquire(blocking=False):
            self._respond(409, {'error': 'Backup is already running'})
            return
        with self.server.ui_lock:
            self.server.ui_running = True
            self.server.ui_error = None
        try:
            thread = threading.Thread(target=self.server.run_pending, daemon=True, name='backup-rotation')
            thread.start()
        except Exception:
            with self.server.ui_lock:
                self.server.ui_running = False
            self.server.action_lock.release()
            self._respond(503, {'error': 'Could not start backup worker'})
            return
        self._respond(202, {'started': True})


def serve(manager, port=0):
    return BackupServer(manager, port)
