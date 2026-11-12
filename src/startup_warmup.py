"""Optional serial prewarming before ASGI readiness; not an OOM safety proof."""
from __future__ import annotations

import gc
import json
import logging
import math
from pathlib import Path
import sys
import time

from config import RERANKER_AVAILABLE, RERANKER_STARTUP_MODE
from src.inference_runtime import RerankMetrics

_log = logging.getLogger('uvicorn.error')


def process_memory_snapshot(status_path: Path = Path('/proc/self/status')) -> dict:
    """Report process RSS/high-water mark. Neither is the container's total usage."""
    result = {'rss_mib': None, 'peak_rss_mib': None}
    try:
        for line in status_path.read_text().splitlines():
            key, _, value = line.partition(':')
            if key in {'VmRSS', 'VmHWM'}:
                field = 'rss_mib' if key == 'VmRSS' else 'peak_rss_mib'
                result[field] = round(int(value.split()[0]) / 1024, 3)
    except (OSError, ValueError, IndexError):
        pass
    if result['peak_rss_mib'] is None:
        try:
            import resource
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            result['peak_rss_mib'] = round(peak / (1024 ** 2 if sys.platform == 'darwin' else 1024), 3)
        except (ImportError, OSError, ValueError):
            pass
    return result


def prepare_reranker_startup(mode: str | None = None) -> dict:
    """Load the configured singleton, then run one short pair without score caching.

    Called after embedding and index startup have finished. In warmup mode any
    failure propagates: do not advertise readiness and defer failure to a user.
    One small input initializes common runtime paths, not every future batch or
    sequence shape. Keeping both models resident still requires a memory budget.
    """
    mode = RERANKER_STARTUP_MODE if mode is None else mode
    if mode not in {'lazy', 'warmup'}:
        raise ValueError('RERANKER_STARTUP_MODE must be lazy or warmup')
    report = {'mode': mode, 'status': 'disabled' if not RERANKER_AVAILABLE else mode}
    if not RERANKER_AVAILABLE or mode == 'lazy':
        _log.info('reranker_startup %s', json.dumps(report))
        return report

    from src import reranker

    # Release unreachable Python temporaries left by indexing. This does not
    # force native allocators or the OS to return memory, nor unload embeddings.
    gc.collect()
    report['memory_before'] = process_memory_snapshot()
    _log.info('reranker_startup_begin %s', json.dumps(report))
    try:
        started = time.perf_counter()
        # Startup is not a model download/export job. Pre-provision artifacts.
        reranker.ensure_local_reranker_model(auto_download=False)
        model = reranker.get_reranker_model()
        report['load_ms'] = round((time.perf_counter() - started) * 1000, 3)
        report['backend'] = getattr(model, '_campusqa_backend', 'unknown')
        metrics = RerankMetrics()
        with reranker._inference_gate.slot(metrics):
            started = time.perf_counter()
            values = model.predict(
                [('What are the course prerequisites? 课程先修要求是什么？',
                  'Prerequisites: calculus. 先修要求：微积分。')],
                batch_size=1, show_progress_bar=False,
            )
            if len(values) != 1 or not math.isfinite(float(values[0])):
                raise ValueError('Invalid reranker warmup prediction')
            report['warmup_ms'] = round((time.perf_counter() - started) * 1000, 3)
        report['status'] = 'warmed'
        return report
    except Exception:
        report['status'] = 'failed'
        raise
    finally:
        report['memory_after'] = process_memory_snapshot()
        _log.info('reranker_startup_end %s', json.dumps(report))
