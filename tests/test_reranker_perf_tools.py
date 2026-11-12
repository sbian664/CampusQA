"""Tests for reproducible benchmark reporting and offline ONNX packaging."""
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.benchmark_reranker import compare_reports, percentile, read_cases
from scripts.export_reranker_onnx import export_model


def load_offline():
    path = Path(__file__).resolve().parents[1] / 'deploy/docker/offline.py'
    spec = importlib.util.spec_from_file_location('performance_offline', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_percentile_and_empty_samples():
    assert percentile([], .95) is None
    assert percentile([1, 3], .5) == 2
    assert percentile([4], .95) == 4


def test_rank_comparison_is_not_answer_accuracy():
    a = {'fixture_sha256': 'same', 'rankings': [{'id': 'q', 'ids': ['b', 'a'], 'scores': [2, 1]}]}
    b = {'fixture_sha256': 'same', 'rankings': [{'id': 'q', 'ids': ['a', 'b'], 'scores': [2, 1]}]}
    result = compare_reports(a, b)
    assert result['top1_agreement'] == 0
    assert result['mean_top3_overlap'] == 1
    assert result['max_absolute_score_delta'] == 1
    assert result['not_answer_accuracy'] is True
    b['fixture_sha256'] = 'different'
    with pytest.raises(ValueError, match='different fixtures'):
        compare_reports(a, b)


def test_fixture_validation(tmp_path):
    path = tmp_path / 'cases.jsonl'
    valid = {'id': 'q', 'query': 'Question', 'candidates': [{'id': 'a', 'content': 'document'}]}
    path.write_text(json.dumps(valid) + '\n')
    assert len(read_cases(path)) == 1
    valid['relevant_ids'] = ['not-present']
    path.write_text(json.dumps(valid) + '\n')
    with pytest.raises(ValueError, match='Relevant IDs'):
        read_cases(path)


def test_export_rejects_existing_output_before_importing_heavy_dependencies(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'config.json').write_text('{}')
    destination = tmp_path / 'existing'
    destination.mkdir()
    for target in (source, source / 'nested', destination):
        with pytest.raises(ValueError, match='new directory'):
            export_model(source, target, 'avx2')


def create_model_pack(root, offline):
    for name in ('embedding/model.safetensors', 'reranker/onnx/model_qint8_avx2.onnx', 'reranker/config.json'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'test-artifact')
    files = [{'path': p.relative_to(root).as_posix(), 'size': p.stat().st_size, 'sha256': offline.digest(p)}
             for p in root.rglob('*') if p.is_file()]
    (root / 'manifest.json').write_text(json.dumps({
        'models': [{'directory': 'embedding'}, {'directory': 'reranker'}], 'files': files}))


def test_onnx_only_reranker_pack_is_verified_and_selected(tmp_path, monkeypatch):
    offline = load_offline()
    create_model_pack(tmp_path, offline)
    assert offline.verify_models(tmp_path)['verified_files'] == 3
    monkeypatch.setenv('RERANKER_BACKEND', 'onnx')
    monkeypatch.setenv('RERANKER_ONNX_FILE', 'onnx/model_qint8_avx2.onnx')
    offline.verify_reranker_selection(tmp_path)
    monkeypatch.setenv('RERANKER_ONNX_FILE', 'onnx/missing.onnx')
    with pytest.raises(ValueError, match='verified model manifest'):
        offline.verify_reranker_selection(tmp_path)
    monkeypatch.setenv('RERANKER_BACKEND', 'torch')
    with pytest.raises(ValueError, match='PyTorch reranker weights'):
        offline.verify_reranker_selection(tmp_path)


def test_unmanifested_onnx_graph_is_not_accepted(tmp_path, monkeypatch):
    offline = load_offline()
    create_model_pack(tmp_path, offline)
    (tmp_path / 'reranker/onnx/unverified.onnx').write_bytes(b'unverified')
    monkeypatch.setenv('RERANKER_BACKEND', 'onnx')
    monkeypatch.setenv('RERANKER_ONNX_FILE', 'onnx/unverified.onnx')
    with pytest.raises(ValueError, match='verified model manifest'):
        offline.verify_reranker_selection(tmp_path)


def test_onnx_queue_profile_keeps_candidate_budget_unchanged():
    source = (Path(__file__).resolve().parents[1] / 'config.py').read_text()
    for name, value in [('RERANKER_CANDIDATE_K', '30'), ('RERANKER_MAX_LENGTH', '512'), ('RERANKER_BATCH_SIZE', '16')]:
        assert f'os.getenv("{name}", "{value}")' in source
