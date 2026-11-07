"""Question-board HTTP API and research jobs using the existing chat executor."""
from __future__ import annotations
import asyncio
import fcntl
import json
import threading
import time
from urllib.parse import urlsplit

from .research import ResearchTools, catalog_coverage
from .research_store import ResearchBoard


def coverage_from_catalog(catalog):
    """Cheap inventory for UI polling; no Node process or model inference."""
    return catalog_coverage(catalog)


class ResearchService:
    def __init__(self,server):
        self.server=server;self.closed=threading.Event()
        self.jobs_lock=threading.Lock();self.job_pending=False
        path=server.project/'_DIT_NOTES/.research-service.lock'
        if path.is_symlink():raise ValueError('Research service lock cannot be a symlink')
        self.lockfile=path.open('a');self.owner=False
        try:
            try:
                fcntl.flock(self.lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB);self.owner=True
            except BlockingIOError:pass
            # Recover only when this process owns research execution. Ordinary
            # state reads cannot interrupt a live writer.
            if self.owner:
                with self.board() as board:board.recover_runs()
        except BaseException:
            if self.owner:fcntl.flock(self.lockfile,fcntl.LOCK_UN)
            self.lockfile.close()
            raise

    def board(self):
        return ResearchBoard(self.server.project,self.server.catalog.read().get('project',{}).get('id'))

    def state(self):
        catalog=self.server.catalog.read();coverage=coverage_from_catalog(catalog)
        with self.board() as board:state=board.state(coverage['snapshot_revision'])
        return {**state,'coverage':coverage,'can_run':self.owner and not self.closed.is_set()}

    def question(self,values):
        action=values.get('action','create')
        with self.board() as board:
            if action=='create':return board.create(values.get('question'),values.get('notes',''),values.get('followups',[]))
            key=values.get('id');version=values.get('expected_version')
            if action=='archive':return board.archive(key,version)
            if action=='update':
                fields={k:values[k] for k in ('question','notes','followups','status') if k in values}
                return board.update(key,version,**fields)
            raise ValueError('Unknown question-board action')

    def submit(self,question_id,expected_version):
        from .chat import get_chat,ingest_busy
        if not self.owner or self.closed.is_set():raise ValueError('Research execution belongs to another library server or is closing')
        if ingest_busy(self.server):
            raise ValueError('The question is saved. Wait for this ingest to finish before running research on this server.')
        # A pinned tools instance owns a Node index. Bound this before creating
        # one, including while the shared executor is occupied by ordinary chat.
        with self.jobs_lock:
            if self.job_pending:raise ValueError('A research question is already queued or running. This question remains saved; retry after it finishes.')
            self.job_pending=True
        tools=None
        try:
            chat=get_chat(self.server)
            # Pin evidence before queuing; do not combine future generations.
            tools=ResearchTools(self.server)
            with self.board() as board:run=board.start_run(question_id,expected_version,tools.snapshot_revision)
            try:future=chat.executor.submit(self.run,run,tools,chat)
            except BaseException:
                with self.board() as board:board.update_run(run['id'],status='interrupted',error='Research could not be queued; retry this saved question.')
                raise
            def completed(task):
                try:
                    if task.cancelled():
                        # Cancelled futures never enter run/finally, so release
                        # their source process and persist the recovery state.
                        try:
                            with self.board() as board:board.update_run(run['id'],status='interrupted',error='Queued research was cancelled during shutdown; retry this saved question.')
                        finally:tools.close()
                finally:
                    with self.jobs_lock:self.job_pending=False
            future.add_done_callback(completed)
        except BaseException:
            if tools is not None:tools.close()
            with self.jobs_lock:self.job_pending=False
            raise
        return {'run_id':run['id'],'question_id':question_id}

    def run(self,run,tools,chat):
        trace=[];trace_lock=threading.Lock();monitor_stop=threading.Event()
        def notify(stage,details):
            with trace_lock:
                event={'stage':stage,**details,'at':time.time()}
                if stage.startswith('Model ') and trace and trace[-1]['stage']==stage:trace[-1]=event
                else:trace.append(event)
                with self.board() as board:board.update_run(run['id'],trace=trace)
        def monitor():
            from .research_progress import model_progress
            path=self.server.tool_root/'runtime/chat-model.log'
            offset=path.stat().st_size if path.exists() else 0
            while not monitor_stop.wait(1):
                try:
                    with path.open() as stream:
                        stream.seek(offset)
                        latest=None
                        for line in stream:
                            value=model_progress(line)
                            if value:latest=value
                        offset=stream.tell()
                        if latest:notify(latest.pop('stage'),latest)
                except FileNotFoundError:continue
                except Exception:return # Progress reporting must not fail a research answer.
        monitor_thread=threading.Thread(target=monitor,daemon=True,name='research-progress')
        tools.notify=notify
        try:
            if self.closed.is_set():raise RuntimeError('Research is shutting down')
            with self.board() as board:board.update_run(run['id'],status='running')
            notify('Preparing local model',{})
            monitor_thread.start()
            chat.ensure_model()
            from .research_agent import run_research_agent
            question=run['prompt'];tools.configure_request(question)
            # Operator notes guide the question; saved/generated earlier answers
            # are deliberately absent from the evidence corpus and prompt.
            prompt=question
            if run.get('notes'):prompt+='\n\nOperator research notes (context, not transcript evidence):\n'+run['notes']
            if run.get('followups'):prompt+='\n\nSaved follow-up questions:\n'+'\n'.join(run['followups'])
            answer,messages=asyncio.run(run_research_agent(prompt,tools))
            monitor_stop.set();monitor_thread.join(timeout=2)
            payload={**answer.model_dump(),'results':tools.shown,'coverage':tools.interview_coverage(),
                'snapshot_revision':tools.snapshot_revision,'messages':messages,
                'model':'Qwen3.8-27B-4bit','wall_seconds':time.time()-run['created_at'],
                'provenance_aliases':tools.provenance_aliases()}
            with self.board() as board:board.finish_run(run['id'],payload)
        except Exception as error:
            interrupted=self.closed.is_set() or chat.closing.is_set()
            with self.board() as board:board.update_run(run['id'],status='interrupted' if interrupted else 'error',error=str(error))
        finally:
            monitor_stop.set()
            if monitor_thread.is_alive():monitor_thread.join(timeout=2)
            chat.stop_owned_model();tools.close()

    def close(self):
        # server_close drains the shared chat executor before calling here.
        self.closed.set()
        if self.owner:fcntl.flock(self.lockfile,fcntl.LOCK_UN)
        self.lockfile.close()


def get_research(server):
    if not hasattr(server,'research'):
        with server.chat_init_lock:
            if not hasattr(server,'research'):server.research=ResearchService(server)
    return server.research


def handle_research_post(handler):
    path=urlsplit(handler.path).path
    if path not in ('/api/research/question','/api/research/ask'):return False
    host=handler.headers.get('Host');origin=handler.headers.get('Origin');port=handler.server.server_port
    if host not in (f'127.0.0.1:{port}',f'localhost:{port}') or origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
        handler.send_error(403);return True
    if handler.headers.get('Content-Type','').split(';')[0]!='application/json':handler.send_error(415);return True
    try:
        length=int(handler.headers.get('Content-Length',0))
        if not 0<length<=150000:raise ValueError('Invalid request size')
        values=json.loads(handler.rfile.read(length))
        if not isinstance(values,dict):raise ValueError('Expected a JSON object')
        service=get_research(handler.server)
        response={'question':service.question(values)} if path.endswith('/question') else service.submit(values.get('question_id'),values.get('expected_version'))
        handler._json(response,False)
    except Exception as error:handler._json({'error':str(error)},False,400)
    return True
