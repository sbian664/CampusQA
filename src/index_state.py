"""Durable maintenance state and process-local retrieval barrier."""
import json
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import Condition, RLock, local

from src.errors import KnowledgeUnavailable
from src.storage import atomic_json


class IndexState:
    def __init__(self, path):
        self.path = Path(path)
        self.condition = Condition(RLock())
        self.writer_lock = RLock()
        self.local = local()
        self.active = 0
        self.writing = False
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            if self.data.get('status') not in {'ready', 'rebuilding', 'repair_required'}:
                raise ValueError('invalid index state')
        except FileNotFoundError:
            self.data = {'status': 'ready', 'reason': None}
        except Exception:
            self.data = {'status': 'repair_required', 'reason': '索引状态文件损坏，需要修复'}
        if self.status == 'rebuilding':
            self.set('repair_required', '上次重建被中断，需要重试维护重建')

    @property
    def status(self):
        return self.data['status']

    def set(self, status, reason=None):
        with self.condition:
            self.data = {'status': status, 'reason': reason}
            atomic_json(self.path, self.data)
            self.condition.notify_all()

    @contextmanager
    def read(self):
        depth = getattr(self.local, 'reading', 0)
        with self.condition:
            if not depth and (self.status != 'ready' or self.writing):
                raise KnowledgeUnavailable('知识库维护中或需要修复，请稍后重试')
            self.active += 1
            self.local.reading = depth + 1
        try:
            yield
        finally:
            with self.condition:
                self.active -= 1
                self.local.reading -= 1
                self.condition.notify_all()

    @contextmanager
    def write(self):
        with self.writer_lock:
            nested = getattr(self.local, 'writing', False)
            if not nested:
                with self.condition:
                    self.writing = True
                    while self.active:
                        self.condition.wait()
                self.local.writing = True
            try:
                yield
            finally:
                if not nested:
                    with self.condition:
                        self.writing = False
                        self.local.writing = False
                        self.condition.notify_all()

    def begin_rebuild(self):
        # Persist the closed gate before waiting for active retrievals.
        with self.condition:
            self.set('rebuilding', '索引重建中')
            while self.active:
                self.condition.wait()


def retrieval_guard(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        state = getattr(self, 'index_state', None) or getattr(getattr(self, 'kb', None), 'index_state', None)
        if state is None:
            return function(self, *args, **kwargs)
        with state.read():
            return function(self, *args, **kwargs)
    return guarded
