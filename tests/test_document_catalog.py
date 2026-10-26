import base64
import json
from types import SimpleNamespace

import pytest

from src.document_catalog import DocumentCatalog
from src.errors import KnowledgeUnavailable
from src.index_state import IndexState
from src.tools import ToolHandler


def catalog_kb(tmp_path, count=3):
    kb = SimpleNamespace(loader=SimpleNamespace(base_dir=str(tmp_path)), metadata={},
                         _chunk_texts={}, _chunk_metadata={}, index_state=IndexState(tmp_path / 'state.json'))
    for index in range(count):
        add_document(kb, tmp_path, f'courses/course_UFUG{index:04}.txt', f'id-{index}', [f'内容{index}'])
    return kb


def add_document(kb, root, source, document_id, texts):
    chunk_ids = [f'{document_id}_{index}' for index in range(len(texts))]
    kb.metadata[str(root / source)] = {
        'document_id': document_id, 'chunk_ids': chunk_ids,
        'chunk_count': len(texts), 'mtime': 1735689600, 'updated_at': 'v1',
    }
    for chunk_id, text in zip(chunk_ids, texts):
        kb._chunk_texts[chunk_id] = text
        kb._chunk_metadata[chunk_id] = {'title': f'标题 {document_id}', 'doc_type': 'text'}


def test_catalog_pages_cover_more_than_search_top_k_without_using_vector_store(tmp_path):
    kb = catalog_kb(tmp_path, 137)
    catalog = DocumentCatalog(kb)
    ids, cursor = [], None
    while True:
        page = catalog.find_documents({'filename_contains': 'uFuG 00'}, page_size=17, cursor=cursor)
        assert page['total'] == 100
        assert all(not item['source'].startswith('/') and 'content' not in item for item in page['items'])
        ids.extend(item['document_id'] for item in page['items'])
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == 100


def test_filters_combine_and_source_prefix_observes_directory_boundary(tmp_path):
    kb = catalog_kb(tmp_path)
    add_document(kb, tmp_path, 'courses-old/course_UFUG0000.txt', 'other', ['old'])
    catalog = DocumentCatalog(kb)
    page = catalog.find_documents({'source_prefix': 'COURSES/', 'source_contains': 'ＵＦＵＧ 0000',
                                    'doc_type': 'txt', 'title_contains': 'ID-0'})
    assert [item['document_id'] for item in page['items']] == ['id-0']
    assert catalog.find_documents({'source': 'courses/course_UFUG0000.txt'})['total'] == 1
    assert catalog.find_documents({'filename_contains': 'not here'})['total'] == 0


def test_dates_are_inclusive_utc_file_dates_and_missing_values_do_not_match(tmp_path):
    kb = catalog_kb(tmp_path)
    kb.metadata[next(iter(kb.metadata))]['mtime'] = None
    catalog = DocumentCatalog(kb)
    assert catalog.find_documents({'mtime_after': '2025-01-01', 'mtime_before': '2025-01-01'})['total'] == 2
    assert catalog.find_documents({'mtime_before': '2024-12-31'})['total'] == 0
    with pytest.raises(ValueError, match='日期'):
        catalog.find_documents({'mtime_after': '2025-02-30'})


@pytest.mark.parametrize('filters', [{'credits': '3'}, {'source': '../secret'}, {'source_prefix': '/etc'},
                                    {'filename_contains': ''}, {'doc_type': 'exe'}, {'title_contains': None}])
def test_invalid_filters_fail_explicitly(tmp_path, filters):
    with pytest.raises(ValueError):
        DocumentCatalog(catalog_kb(tmp_path)).find_documents(filters)


def test_cursor_binds_query_revision_and_operation(tmp_path):
    kb = catalog_kb(tmp_path)
    catalog = DocumentCatalog(kb)
    cursor = catalog.find_documents(page_size=1)['next_cursor']
    with pytest.raises(ValueError, match='变化'):
        catalog.find_documents({'doc_type': 'text'}, cursor=cursor)
    with pytest.raises(ValueError, match='无效'):
        catalog.read_documents(['id-0'], cursor=cursor)
    kb.metadata[next(iter(kb.metadata))]['updated_at'] = 'v2'
    with pytest.raises(ValueError, match='变化'):
        catalog.find_documents(cursor=cursor)
    with pytest.raises(ValueError, match='无效'):
        catalog.find_documents(cursor='not-a-cursor')
    encoded = base64.urlsafe_b64encode(json.dumps([1, 'find']).encode()).decode()
    with pytest.raises(ValueError):
        catalog.find_documents(cursor=encoded)


def test_read_cursor_covers_all_chunks_and_unicode_without_drops(tmp_path):
    kb = catalog_kb(tmp_path, 0)
    chunks = ['你好🙂' * 2000, 'second chunk']
    add_document(kb, tmp_path, 'long.txt', 'long', chunks)
    add_document(kb, tmp_path, 'short.txt', 'short', ['尾页'])
    catalog = DocumentCatalog(kb)
    actual = {'long': '', 'short': ''}
    cursor = None
    while True:
        page = catalog.read_documents(['long', 'short'], max_chars=8000, cursor=cursor)
        assert sum(len(item['content'].encode()) for item in page['items']) <= 12000
        for item in page['items']:
            assert item['start_offset'] == len(actual[item['document_id']])
            actual[item['document_id']] += item['content']
            assert item['end_offset'] == len(actual[item['document_id']])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert actual == {'long': '\n\n'.join(chunks), 'short': '尾页'}


def test_read_cursor_rejects_changed_content_order_and_arbitrary_path(tmp_path):
    kb = catalog_kb(tmp_path)
    catalog = DocumentCatalog(kb)
    cursor = catalog.read_documents(['id-0', 'id-1'], max_chars=1)['next_cursor']
    with pytest.raises(ValueError, match='变化'):
        catalog.read_documents(['id-1', 'id-0'], cursor=cursor)
    kb._chunk_texts['id-0_0'] = 'changed'
    with pytest.raises(ValueError, match='变化'):
        catalog.read_documents(['id-0', 'id-1'], cursor=cursor)
    with pytest.raises(ValueError, match='ID'):
        catalog.read_documents(['/etc/passwd'])


def test_catalog_reports_exclusions_and_never_silently_omits_missing_body(tmp_path):
    kb = catalog_kb(tmp_path)
    kb.metadata[str(tmp_path / 'unindexed.txt')] = {'document_id': 'seed'}
    add_document(kb, tmp_path.parent, 'outside.txt', 'outside', ['secret'])
    catalog = DocumentCatalog(kb)
    result = catalog.find_documents()
    assert result['total'] == 3 and result['excluded_documents'] == 2
    assert 'outside' not in str(result['items'])
    del kb._chunk_texts['id-0_0']
    with pytest.raises(ValueError, match='正文不完整'):
        catalog.read_documents(['id-0'])


def test_tools_share_maintenance_barrier_and_do_not_reuse_previous_trace(tmp_path):
    kb = catalog_kb(tmp_path)
    handler = ToolHandler(kb)
    first = json.loads(handler.execute('find_documents', {'filters': {'filename_contains': 'UFUG'}}))
    assert first['total'] == 3
    assert handler.get_call_log()[-1]['engine'] == 'find_documents'
    handler.execute('read_documents', {'document_ids': ['missing']})
    assert 'hits' not in handler.get_call_log()[-1]
    kb.index_state.set('rebuilding')
    with pytest.raises(KnowledgeUnavailable):
        handler.execute('find_documents', {})
    with pytest.raises(KnowledgeUnavailable):
        DocumentCatalog(kb).read_documents(['id-0'])


@pytest.mark.parametrize('kwargs', [{'page_size': 0}, {'page_size': True}, {'page_size': 101}])
def test_catalog_limits_are_validated_not_silently_clamped(tmp_path, kwargs):
    with pytest.raises(ValueError):
        DocumentCatalog(catalog_kb(tmp_path)).find_documents(**kwargs)


def test_count_view_returns_matching_total_without_a_page_of_items(tmp_path):
    catalog = DocumentCatalog(catalog_kb(tmp_path, 137))
    result = catalog.find_documents({'filename_contains': 'UFUG 00'}, view='count')
    assert result['total'] == 100
    assert result['view'] == 'count' and result['scope'] == 'active_indexed_documents'
    assert result['items'] == [] and result['next_cursor'] is None
    with pytest.raises(ValueError, match='view'):
        catalog.find_documents(view='summary')


def test_count_view_is_available_through_the_agent_tool_handler(tmp_path):
    result = json.loads(ToolHandler(catalog_kb(tmp_path)).execute('find_documents', {'view': 'count'}))
    assert result['tool'] == 'find_documents' and result['total'] == 3 and result['items'] == []
