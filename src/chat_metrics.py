"""Privacy-preserving, idempotent per-turn and per-attempt chat metrics."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import logging
import sqlite3
import time
import uuid
from pathlib import Path

LOG = logging.getLogger("uvicorn.error")
# Current Hong Kong time is UTC+08:00; using a fixed offset also works on
# Windows installations without the optional tzdata package. No history is
# backfilled, so historical daylight-saving transitions are irrelevant.
HK = timezone(timedelta(hours=8), "Asia/Hong_Kong")
_scope = ContextVar("campusqa_metric_scope", default=None)
_stage = ContextVar("campusqa_metric_stage", default="answer")


def _day(timestamp):
    return datetime.fromtimestamp(timestamp, HK).date().isoformat()


@contextmanager
def turn_scope(store, turn_id):
    token = _scope.set((store, turn_id))
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def stage_scope(stage):
    token = _stage.set(stage)
    try:
        yield
    finally:
        _stage.reset(token)


def current_scope():
    return _scope.get()


def record_attempt(provider, model, started_at, status, usage=None, *, call_id=None):
    scope = current_scope()
    if scope is None:
        return
    store, turn_id = scope
    store.attempt(turn_id, call_id or uuid.uuid4().hex, _stage.get(), provider,
                  model, started_at, time.time(), status, usage)


@contextmanager
def track_attempt(provider, model, cancel_event=None):
    """Record one network attempt, including incomplete and cancelled calls."""
    class Attempt:
        usage = None
    attempt = Attempt()
    started = time.time()
    call_id = uuid.uuid4().hex
    scope = current_scope()
    stage = _stage.get()
    if scope is not None:
        scope[0].attempt(scope[1], call_id, stage, provider, model, started, started, 'running', None)
    try:
        yield attempt
    except BaseException:
        status = 'cancelled' if cancel_event is not None and cancel_event.is_set() else 'failed'
        if scope is not None:
            scope[0].complete_attempt(scope[1], call_id, stage, provider, model, started, time.time(), status, attempt.usage)
        raise
    else:
        if scope is not None:
            scope[0].complete_attempt(scope[1], call_id, stage, provider, model, started, time.time(), 'succeeded', attempt.usage)


class ChatMetrics:
    def __init__(self, db_path):
        self.path = Path(db_path)
        self.available = True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.executescript('''
              CREATE TABLE IF NOT EXISTS chat_metric_turns (
                turn_id TEXT PRIMARY KEY, session_id TEXT, created_at REAL NOT NULL,
                day TEXT NOT NULL, ended_at REAL, mode TEXT, status TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS chat_metric_turn_day ON chat_metric_turns(day);
              CREATE TABLE IF NOT EXISTS chat_metric_calls (
                call_id TEXT PRIMARY KEY, turn_id TEXT NOT NULL, stage TEXT NOT NULL,
                provider TEXT NOT NULL, model TEXT NOT NULL, started_at REAL NOT NULL,
                ended_at REAL NOT NULL, status TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER);
              CREATE INDEX IF NOT EXISTS chat_metric_calls_turn ON chat_metric_calls(turn_id);
                ''')
        except Exception as error:
            self.available = False
            LOG.error("chat_metrics_init_failed kind=%s", type(error).__name__)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _write(self, fn):
        try:
            with self.connect() as db:
                fn(db)
        except Exception as error:
            self.available = False
            # No payloads, upstream errors, or configuration are logged.
            LOG.error("chat_metrics_write_failed kind=%s", type(error).__name__)

    def start(self, turn_id, session_id, *, created_at=None):
        when = time.time() if created_at is None else float(created_at)
        self._write(lambda db: db.execute(
            "INSERT OR IGNORE INTO chat_metric_turns VALUES (?,?,?,?,NULL,NULL,'running')",
            (str(turn_id), session_id, when, _day(when))))

    def mode(self, turn_id, mode):
        self._write(lambda db: db.execute("UPDATE chat_metric_turns SET mode=? WHERE turn_id=?",
                                          (mode, str(turn_id))))

    def finish(self, turn_id, status, *, ended_at=None):
        when = time.time() if ended_at is None else float(ended_at)
        self._write(lambda db: db.execute(
            "UPDATE chat_metric_turns SET status=?,ended_at=? WHERE turn_id=? AND status='running'",
            (status, when, str(turn_id))))

    def recover_interrupted(self):
        def recover(db):
            now = time.time()
            db.execute("UPDATE chat_metric_turns SET status='interrupted', ended_at=? WHERE status='running'", (now,))
            db.execute("UPDATE chat_metric_calls SET status='interrupted', ended_at=? WHERE status='running'", (now,))
        self._write(recover)

    def recover_succeeded(self, turn_id):
        self._write(lambda db: db.execute(
            "UPDATE chat_metric_turns SET status='succeeded' WHERE turn_id=? AND status='interrupted'", (str(turn_id),)))

    def attempt(self, turn_id, call_id, stage, provider, model, started, ended, status, usage):
        self._record_attempt(turn_id, call_id, stage, provider, model, started, ended, status, usage, update=False)

    def complete_attempt(self, turn_id, call_id, stage, provider, model, started, ended, status, usage):
        self._record_attempt(turn_id, call_id, stage, provider, model, started, ended, status, usage, update=True)

    def _record_attempt(self, turn_id, call_id, stage, provider, model, started, ended, status, usage, *, update):
        usage = usage if isinstance(usage, dict) else {}
        def token_value(key):
            value = usage.get(key)
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        sql = "INSERT INTO chat_metric_calls VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(call_id) "
        sql += ("DO UPDATE SET ended_at=excluded.ended_at,status=excluded.status,input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens"
                if update else "DO NOTHING")
        self._write(lambda db: db.execute(
            sql,
            (str(call_id), str(turn_id), str(stage), str(provider or ''), str(model or ''),
             float(started), float(ended), str(status), token_value('prompt_tokens'), token_value('completion_tokens'))))

    @staticmethod
    def _aggregate(turns, calls):
        terminal = [t for t in turns if t['ended_at'] is not None]
        durations = [max(0, (t['ended_at'] - t['created_at']) * 1000) for t in terminal]
        counts = {s: sum(t['status'] == s for t in turns) for s in
                  ('succeeded', 'failed', 'cancelled', 'interrupted', 'running')}
        return {'questions': len(turns), 'sessions': len({t['session_id'] for t in turns if t['session_id']}),
                **counts, 'average_duration_ms': round(sum(durations) / len(durations), 2) if durations else None,
                'calls': len(calls),
                'known_input_tokens': sum(c['input_tokens'] for c in calls if c['input_tokens'] is not None),
                'known_output_tokens': sum(c['output_tokens'] for c in calls if c['output_tokens'] is not None),
                'unknown_usage_calls': sum(c['input_tokens'] is None or c['output_tokens'] is None for c in calls)}

    def summary(self, date_from=None, date_to=None, model=None, mode=None, stage=None):
        today = datetime.now(HK).date()
        end = date.fromisoformat(date_to) if date_to else today
        start = date.fromisoformat(date_from) if date_from else end - timedelta(days=6)
        if start > end:
            raise ValueError('开始日期不能晚于结束日期')
        clauses, params = ['day >= ?', 'day <= ?'], [start.isoformat(), end.isoformat()]
        if mode:
            clauses.append('mode = ?')
            params.append(mode)
        if model or stage:
            call_conditions = ['c.turn_id=chat_metric_turns.turn_id']
            if model:
                call_conditions.append('c.model=?')
                params.append(model)
            if stage:
                call_conditions.append('c.stage=?')
                params.append(stage)
            clauses.append('EXISTS (SELECT 1 FROM chat_metric_calls c WHERE ' + ' AND '.join(call_conditions) + ')')
        with self.connect() as db:
            turns = [dict(row) for row in db.execute('SELECT * FROM chat_metric_turns WHERE ' + ' AND '.join(clauses), params)]
            ids = {row['turn_id'] for row in turns}
            # Join by date/mode in SQL to avoid SQLite's bind-parameter limit.
            calls = [dict(row) for row in db.execute('SELECT c.* FROM chat_metric_calls c JOIN chat_metric_turns t ON t.turn_id=c.turn_id WHERE t.day>=? AND t.day<=?', (start.isoformat(), end.isoformat())) if row['turn_id'] in ids and (not model or row['model'] == model) and (not stage or row['stage'] == stage)]
        def group(key, values):
            result = []
            for value in sorted(values):
                selected_calls = [c for c in calls if c[key] == value]
                selected_ids = {c['turn_id'] for c in selected_calls}
                selected_turns = [t for t in turns if t['turn_id'] in selected_ids]
                result.append({key: value, **self._aggregate(selected_turns, selected_calls)})
            return result
        days = []
        for day in sorted({t['day'] for t in turns}):
            day_turns = [t for t in turns if t['day'] == day]
            ids = {t['turn_id'] for t in day_turns}
            days.append({'date': day, **self._aggregate(day_turns, [c for c in calls if c['turn_id'] in ids])})
        modes = []
        for value in sorted({t['mode'] or 'unknown' for t in turns}):
            selected = [t for t in turns if (t['mode'] or 'unknown') == value]
            ids = {t['turn_id'] for t in selected}
            modes.append({'mode': value, **self._aggregate(selected, [c for c in calls if c['turn_id'] in ids])})
        return {'timezone': 'Asia/Hong_Kong', 'date_from': start.isoformat(), 'date_to': end.isoformat(),
                'available': self.available, 'totals': self._aggregate(turns, calls), 'days': days,
                'models': group('model', {c['model'] for c in calls}), 'modes': modes,
                'stages': group('stage', {c['stage'] for c in calls})}

    def turn(self, turn_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM chat_metric_turns WHERE turn_id=?', (str(turn_id),)).fetchone()
            if not row:
                return None
            calls = [dict(item) for item in db.execute('SELECT * FROM chat_metric_calls WHERE turn_id=? ORDER BY started_at,call_id', (str(turn_id),))]
        return {'turn': dict(row), 'calls': calls}
