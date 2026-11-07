"""Transactional question board in the existing project metadata database.

Research answers are immutable artifacts, not retrieval evidence. Board writes
have their own revision so saving a question cannot start a media reindex.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .project_store import ProjectStore


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value, limit, *, empty=False):
    if not isinstance(value, str) or len(value) > limit or not empty and not value.strip():
        raise ValueError('Invalid research text')
    return value.strip()


def _followups(values):
    if not isinstance(values, list) or len(values) > 40:
        raise ValueError('Supply at most forty follow-up questions')
    return [_text(v, 2000) for v in values]


class ResearchBoard:
    def __init__(self, project, project_id=None):
        self.project = Path(project).resolve()
        self.store = ProjectStore(self.project/'_DIT_NOTES/project.sqlite3', project_id=project_id)
        self.db = self.store.db
        try:
            with self.store._transaction():
                for statement in (
                    "CREATE TABLE IF NOT EXISTS research_meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL)",
                    "INSERT OR IGNORE INTO research_meta VALUES('revision',0)",
                    '''CREATE TABLE IF NOT EXISTS research_questions(
                       id TEXT PRIMARY KEY,question TEXT NOT NULL,notes TEXT NOT NULL,followups TEXT NOT NULL,
                       status TEXT NOT NULL,version INTEGER NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)''',
                    '''CREATE TABLE IF NOT EXISTS research_runs(
                       id TEXT PRIMARY KEY,question_id TEXT NOT NULL,question_version INTEGER NOT NULL,
                       prompt TEXT NOT NULL,notes TEXT NOT NULL,followups TEXT NOT NULL,snapshot_revision TEXT NOT NULL,
                       status TEXT NOT NULL,payload TEXT NOT NULL,trace TEXT NOT NULL,error TEXT,
                       created_at REAL NOT NULL,updated_at REAL NOT NULL,
                       FOREIGN KEY(question_id) REFERENCES research_questions(id))''',
                    '''CREATE TABLE IF NOT EXISTS research_answers(
                       id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,question_id TEXT NOT NULL,
                       question_version INTEGER NOT NULL,snapshot_revision TEXT NOT NULL,payload TEXT NOT NULL,
                       created_at REAL NOT NULL,FOREIGN KEY(run_id) REFERENCES research_runs(id),
                       FOREIGN KEY(question_id) REFERENCES research_questions(id))''',
                    'CREATE INDEX IF NOT EXISTS research_runs_question ON research_runs(question_id,created_at)',
                ):
                    self.db.execute(statement)
        except BaseException:
            self.close()
            raise

    def close(self):
        self.store.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _audit(self, operation, values):
        self.db.execute('INSERT INTO audit(operation,payload,created_at) VALUES(?,?,?)',
                        ('research.'+operation, _json(values), time.time()))
        self.db.execute("UPDATE research_meta SET value=value+1 WHERE key='revision'")

    def _question(self, question_id):
        row = self.db.execute('SELECT * FROM research_questions WHERE id=?', (question_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown research question')
        return row

    def _expected(self, row, expected_version):
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or row['version'] != expected_version:
            raise ValueError('The question changed; reload the board before saving')

    def _run(self, run_id):
        row = self.db.execute('SELECT * FROM research_runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown research run')
        return row

    def create(self, question, notes='', followups=None):
        question = _text(question, 5000)
        notes = _text(notes, 30000, empty=True)
        followups = _followups([] if followups is None else followups)
        key = 'question_'+uuid.uuid4().hex
        now = time.time()
        with self.store._transaction():
            self.db.execute('INSERT INTO research_questions VALUES(?,?,?,?,?,?,?,?)',
                            (key, question, notes, _json(followups), 'open', 1, now, now))
            self._audit('question.create', {'question_id': key})
        return self.get(key)

    def update(self, question_id, expected_version, **values):
        if not values or not set(values) <= {'question', 'notes', 'followups', 'status'}:
            raise ValueError('Invalid question update')
        checked = {}
        for key, value in values.items():
            if key == 'question':
                checked[key] = _text(value, 5000)
            elif key == 'notes':
                checked[key] = _text(value, 30000, empty=True)
            elif key == 'followups':
                checked[key] = _json(_followups(value))
            else:
                if value not in ('open', 'answered', 'follow_up', 'archived'):
                    raise ValueError('Invalid question status')
                checked[key] = value
        with self.store._transaction():
            row = self._question(question_id)
            self._expected(row, expected_version)
            self.db.execute('UPDATE research_questions SET '+','.join(k+'=?' for k in checked)+',version=version+1,updated_at=? WHERE id=?',
                            (*checked.values(), time.time(), question_id))
            self._audit('question.update', {'question_id': question_id, 'fields': sorted(checked)})
        return self.get(question_id)

    def archive(self, question_id, expected_version):
        return self.update(question_id, expected_version, status='archived')

    def get(self, question_id, snapshot_revision=None):
        row = dict(self._question(question_id))
        row['followups'] = json.loads(row['followups'])
        answers = []
        for value in self.db.execute('SELECT * FROM research_answers WHERE question_id=? ORDER BY created_at,id', (question_id,)):
            answer = dict(value)
            payload = json.loads(answer.pop('payload'))
            answer.update(payload)
            # A saved historical answer always retains its original provenance.
            answer['question_changed'] = answer['question_version'] != row['version']
            answer['stale'] = bool(snapshot_revision and answer['snapshot_revision'] != snapshot_revision or answer['question_changed'])
            answers.append(answer)
        row['answers'] = answers
        row['latest_answer'] = answers[-1] if answers else None
        row['stale'] = bool(row['latest_answer'] and row['latest_answer']['stale'])
        return row

    def list(self, snapshot_revision=None, include_archived=False):
        rows = self.db.execute('SELECT id FROM research_questions'+('' if include_archived else " WHERE status<>'archived'")+' ORDER BY created_at,id')
        return [self.get(r['id'], snapshot_revision) for r in rows]

    def _run_value(self, row):
        value = dict(row)
        payload = json.loads(value.pop('payload'))
        value['trace'] = json.loads(value['trace'])
        value['followups'] = json.loads(value['followups'])
        value.update(payload)
        value.setdefault('answer', '')
        value.setdefault('claims', [])
        value.setdefault('results', [])
        value.setdefault('coverage', {})
        return value

    def runs(self):
        return [self._run_value(r) for r in self.db.execute('SELECT * FROM research_runs ORDER BY created_at,id')]

    def state(self, snapshot_revision=None):
        return {'questions': self.list(snapshot_revision), 'runs': self.runs(),
                'revision': self.db.execute("SELECT value FROM research_meta WHERE key='revision'").fetchone()[0]}

    def start_run(self, question_id, expected_version, snapshot_revision):
        snapshot_revision = _text(snapshot_revision, 128)
        key = 'research_'+uuid.uuid4().hex
        now = time.time()
        with self.store._transaction():
            row = self._question(question_id)
            self._expected(row, expected_version)
            if row['status'] == 'archived':
                raise ValueError('Restore an archived question before asking it')
            active = self.db.execute("SELECT id FROM research_runs WHERE question_id=? AND status IN ('queued','running')", (question_id,)).fetchone()
            if active:
                raise ValueError('This question already has an active research run')
            self.db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (key, question_id, expected_version, row['question'], row['notes'], row['followups'], snapshot_revision,
                 'queued', '{}', '[]', None, now, now))
            self._audit('run.start', {'run_id': key, 'question_id': question_id})
        return self._run_value(self._run(key))

    def update_run(self, run_id, **values):
        if not values or not set(values) <= {'status', 'trace', 'error'}:
            raise ValueError('Invalid research run update')
        checked = {}
        for key, value in values.items():
            if key == 'status':
                if value not in ('queued', 'running', 'error', 'interrupted'):
                    raise ValueError('Complete runs through finish_run')
                checked[key] = value
            elif key == 'trace':
                if not isinstance(value, list) or len(_json(value)) > 1_000_000:
                    raise ValueError('Invalid research trace')
                checked[key] = _json(value)
            else:
                checked[key] = None if value is None else _text(value, 20000, empty=True)
        with self.store._transaction():
            row = self._run(run_id)
            if row['status'] == 'complete':
                raise ValueError('Completed research runs are immutable')
            self.db.execute('UPDATE research_runs SET '+','.join(k+'=?' for k in checked)+',updated_at=? WHERE id=?',
                            (*checked.values(), time.time(), run_id))
            self._audit('run.update', {'run_id': run_id, 'fields': sorted(checked)})
        return self._run_value(self._run(run_id))

    def finish_run(self, run_id, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get('answer'), str) or not payload['answer'].strip():
            raise ValueError('A completed research run needs a validated answer')
        if set(payload) & {'id', 'run_id', 'question_id', 'question_version', 'created_at', 'updated_at', 'status', 'prompt', 'notes', 'trace', 'error'}:
            raise ValueError('Answer payload cannot replace canonical research provenance')
        payload = dict(payload)
        encoded = _json(payload)
        if len(encoded) > 2_000_000:
            raise ValueError('Research answer is too large')
        with self.store._transaction():
            run = self._run(run_id)
            if run['status'] == 'complete':
                if run['payload'] != encoded:
                    raise ValueError('Completed research answers are immutable')
                return self._run_value(run)
            if run['status'] not in ('queued', 'running'):
                raise ValueError('Only active research runs can complete')
            if payload.get('snapshot_revision', run['snapshot_revision']) != run['snapshot_revision']:
                raise ValueError('Answer snapshot differs from its research run')
            now = time.time()
            self.db.execute('INSERT INTO research_answers VALUES(?,?,?,?,?,?,?)',
                ('answer_'+uuid.uuid4().hex, run_id, run['question_id'], run['question_version'], run['snapshot_revision'], encoded, now))
            self.db.execute("UPDATE research_runs SET status='complete',payload=?,error=NULL,updated_at=? WHERE id=?", (encoded, now, run_id))
            # If an operator edited the question during inference, retain the
            # historical answer but never overwrite that newer question or notes.
            question_status='open' if payload.get('outcome') in ('partial','not_supported') else 'answered'
            self.db.execute("UPDATE research_questions SET status=?,updated_at=? WHERE id=? AND version=? AND status<>'archived'",
                            (question_status, now, run['question_id'], run['question_version']))
            self._audit('run.complete', {'run_id': run_id, 'question_id': run['question_id']})
        return self._run_value(self._run(run_id))

    def save_answer(self, question_id, expected_version, payload):
        run = self.start_run(question_id, expected_version, payload['snapshot_revision'])
        try:
            return self.finish_run(run['id'], payload)
        except Exception as error:
            self.update_run(run['id'], status='error', error=str(error))
            raise

    def recover_runs(self):
        """Call once when this app owns startup; ordinary reads never interrupt jobs."""
        with self.store._transaction():
            rows = list(self.db.execute("SELECT id FROM research_runs WHERE status IN ('queued','running')"))
            for row in rows:
                self.db.execute("UPDATE research_runs SET status='interrupted',error=?,updated_at=? WHERE id=?",
                                ('Research was interrupted; retry this question. Saved answers are intact.', time.time(), row['id']))
            if rows:
                self._audit('run.recover', {'run_ids': [r['id'] for r in rows]})
        return len(rows)
