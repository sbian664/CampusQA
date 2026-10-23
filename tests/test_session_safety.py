import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from src.session import Session


def test_new_sessions_are_unique_uuids():
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: Session().session_id, range(100)))
    assert len(set(ids)) == 100
    assert all(UUID(value).version == 4 for value in ids)


@pytest.mark.parametrize('value', ['../x', '..', '.', 'kb_metadata', 'kb_metadata_chunks', 'index_state', 'x/y', 'CON', 'CON.txt', 'x.'])
def test_rejects_unsafe_and_reserved_ids(value):
    with pytest.raises(ValueError):
        Session(value)


def test_cache_cannot_be_loaded_overwritten_or_deleted(tmp_path):
    with patch('src.session.CACHE_DIR', str(tmp_path)):
        path = tmp_path / 'legacy.json'
        path.write_text('{"cache": true}', encoding='utf-8')
        session = Session('legacy')
        for operation in (session.load, session.save, session.delete):
            with pytest.raises(ValueError):
                operation()
        assert json.loads(path.read_text()) == {'cache': True}


def test_legacy_session_and_atomic_interruption(tmp_path):
    with patch('src.session.CACHE_DIR', str(tmp_path)):
        session = Session('20260901_121212')
        session.add_message('user', 'old')
        path = Path(session.save())
        session.add_message('assistant', 'new')
        with patch('src.session.os.replace', side_effect=OSError('interrupted')):
            with pytest.raises(Exception):
                session.save()
        assert len(json.loads(path.read_text(encoding='utf-8'))['messages']) == 1
        assert list(tmp_path.glob('*.tmp')) == []
        for filename in ('../escape.json', 'another.json'):
            with pytest.raises(ValueError):
                session.save(filename)


def test_session_coordination_rejects_conflict_without_waiting(tmp_path):
    with patch('src.session.CACHE_DIR', str(tmp_path)):
        with Session.coordinate('test'):
            with ThreadPoolExecutor(max_workers=1) as pool:
                def attempt():
                    with Session.coordinate('test'):
                        pass
                with pytest.raises(Exception, match='处理中'):
                    pool.submit(attempt).result(timeout=1)


def test_budget_never_drops_persisted_messages(tmp_path):
    with patch('src.session.CACHE_DIR', str(tmp_path)):
        session = Session(max_history=2)
        for i in range(6):
            session.add_message('user' if i % 2 == 0 else 'assistant', str(i))
        session.save()
        restored = Session(session.session_id)
        restored.load()
        assert len(restored.messages) == 6
