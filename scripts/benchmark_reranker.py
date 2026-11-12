"""Benchmark a local reranker on fixed JSONL query/candidate sets, with no LLM calls.

Each line: {"id":"q1","query":"...","candidates":[{"id":"d1","content":"..."}],
            "relevant_ids":["d1"]}  # relevant_ids is optional, human-labelled.
Cold = clear cache between completed client waves, not unload the model.
The report omits query/document text; raw fixtures remain operator-controlled.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import math
import os
from pathlib import Path
import platform
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentile(values, p):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * p
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def read_cases(path: Path):
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Fixture requires at least one case and unique case IDs')
    for case in cases:
        if not isinstance(case['query'], str) or not case['query'].strip() or not case['candidates']:
            raise ValueError('Every case requires a query and nonempty candidates')
        ids = [c['id'] for c in case['candidates']]
        if len(ids) != len(set(ids)):
            raise ValueError('Candidate IDs must be unique within each case')
        if any(not isinstance(c['content'], str) for c in case['candidates']):
            raise ValueError('Candidate content must be text')
        if not set(case.get('relevant_ids', [])).issubset(ids):
            raise ValueError('Relevant IDs must refer to provided candidates')
    return cases


def compare_reports(current, baseline, top_k=3):
    if current['fixture_sha256'] != baseline['fixture_sha256']:
        raise ValueError('Cannot compare different fixtures')
    previous = {c['id']: c for c in baseline['rankings']}
    overlaps, top1, differences = [], [], []
    for case in current['rankings']:
        other = previous[case['id']]
        k = min(top_k, len(case['ids']))
        overlaps.append(len(set(case['ids'][:k]) & set(other['ids'][:k])) / k)
        top1.append(case['ids'][0] == other['ids'][0])
        scores = dict(zip(other['ids'], other['scores']))
        differences.extend(abs(score - scores[cid]) for cid, score in zip(case['ids'], case['scores']))
    return {'cases': len(overlaps), 'top1_agreement': sum(top1) / len(top1),
            f'mean_top{top_k}_overlap': sum(overlaps) / len(overlaps),
            'mean_absolute_score_delta': sum(differences) / len(differences),
            'max_absolute_score_delta': max(differences),
            'not_answer_accuracy': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--backend', choices=['torch', 'onnx'], default='torch')
    parser.add_argument('--onnx-file', default='onnx/model.onnx')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--max-length', type=int, default=512)
    parser.add_argument('--clients', type=int, default=1)
    parser.add_argument('--inference-slots', type=int, default=1)
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--cache', choices=['cold', 'warm'], default='cold')
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    if min(args.threads, args.batch_size, args.max_length, args.clients, args.rounds, args.inference_slots) < 1:
        parser.error('All numeric options must be positive')
    cases = read_cases(args.cases)
    # Set before importing project config or torch: no process-global retuning.
    os.environ.update({
        'INFERENCE_CPU_THREADS': str(args.threads), 'INFERENCE_INTEROP_THREADS': '1',
        'RERANKER_BACKEND': args.backend, 'RERANKER_DEVICE': 'cpu',
        'RERANKER_MODEL_PATH': str(args.model_path.resolve()), 'RERANKER_ONNX_FILE': args.onnx_file,
        'RERANKER_AUTO_DOWNLOAD': 'false', 'RERANKER_BATCH_SIZE': str(args.batch_size),
        'RERANKER_MAX_LENGTH': str(args.max_length), 'RERANKER_MAX_CONCURRENT': str(args.inference_slots),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false',
    })
    from src import reranker
    from src.inference_runtime import collect_rerank_metrics
    start = time.perf_counter()
    model = reranker.get_reranker_model()
    load_seconds = time.perf_counter() - start
    lengths = []
    for case in cases:
        for item in case['candidates']:
            lengths.append(len(model.tokenizer(case['query'], item['content'], truncation=False)['input_ids']))

    def invoke(case):
        start = time.perf_counter()
        with collect_rerank_metrics() as metrics:
            ranked = reranker.rerank_results(case['query'], case['candidates'], model, len(case['candidates']))
        return {'case_id': case['id'], 'wall_ms': (time.perf_counter() - start) * 1000,
                'metrics': metrics.to_dict()}, {'id': case['id'], 'ids': [x['id'] for x in ranked],
                                              'scores': [x['rerank_score'] for x in ranked]}

    invoke(cases[0])  # Warm model execution once; report load time separately.
    if args.cache == 'warm':
        for case in cases:
            invoke(case)
    samples, rankings = [], {}
    measurement_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        for _ in range(args.rounds):
            for offset in range(0, len(cases), args.clients):
                if args.cache == 'cold':
                    reranker._score_cache.clear()  # Never clear while other requests are executing.
                futures = [pool.submit(invoke, case) for case in cases[offset:offset + args.clients]]
                for future in futures:
                    sample, ranked = future.result()
                    samples.append(sample)
                    rankings[ranked['id']] = ranked
    elapsed = time.perf_counter() - measurement_started
    latencies = [s['wall_ms'] for s in samples]
    totals = {key: sum(s['metrics'][key] for s in samples)
              for key in ['pairs', 'unique_pairs', 'cache_hits', 'cache_hits_after_wait',
                          'predicted_pairs', 'predict_calls', 'queue_ms', 'predict_ms']}
    versions = {}
    for package in ['torch', 'sentence-transformers', 'transformers', 'onnxruntime', 'optimum-onnx']:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            pass
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {
        'fixture_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'settings': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()
                     if k not in {'cases', 'output', 'baseline'}},
        'hardware': {'system': platform.system(), 'machine': platform.machine(),
                     'processor': platform.processor(), 'logical_cpus': os.cpu_count()},
        'versions': versions, 'load_seconds': load_seconds,
        'peak_rss_mib': maxrss / (1024 ** 2 if sys.platform == 'darwin' else 1024),
        'token_lengths_before_truncation': {'p50': percentile(lengths, .5), 'p95': percentile(lengths, .95),
                                           'max': max(lengths),
                                           'truncated_fraction': sum(n > args.max_length for n in lengths) / len(lengths)},
        'summary': {'requests': len(samples), 'p50_ms': percentile(latencies, .5),
                    'p95_ms': percentile(latencies, .95), 'max_ms': max(latencies),
                    'requests_per_second': len(samples) / elapsed, **totals},
        'samples': samples, 'rankings': [rankings[c['id']] for c in cases],
        'notes': ['Cache-cold keeps model warm. Export/loading are excluded from request latency.',
                  'Wall time includes internal admission queue; predict_ms includes tokenization.',
                  'Small sample p95 is descriptive only, not a production tail-latency estimate.'],
    }
    labelled = []
    for case in cases:
        relevant = set(case.get('relevant_ids', []))
        if relevant:
            labelled.append(len(relevant & set(rankings[case['id']]['ids'][:3])) / len(relevant))
    if labelled:
        report['labelled_recall_at_3'] = sum(labelled) / len(labelled)
    if args.baseline:
        report['baseline_comparison'] = compare_reports(report, json.loads(args.baseline.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(args.output), **report['summary'],
                      'comparison': report.get('baseline_comparison')}, indent=2))


if __name__ == '__main__':
    main()
