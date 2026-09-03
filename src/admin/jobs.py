"""Single-worker persistent jobs for index mutations."""

from __future__ import annotations

import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Dict

from .control_store import AdminControlStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdminJobManager:
    def __init__(self, store: AdminControlStore) -> None:
        self.store = store
        self.store.mark_running_jobs_interrupted()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="campusqa-admin-job")
        self.lock = Lock()
        self.futures: Dict[str, Future] = {}

    def submit(
        self,
        kind: str,
        work: Callable[[Callable[[int, str], None]], Any],
        target_id: str | None = None,
    ) -> str:
        with self.lock:
            active = next(
                (job for job in self.store.list_jobs(limit=20)
                 if job["status"] in {"queued", "running"} and job["kind"] in {"scan", "rebuild", "upload", "publish_submission"}),
                None,
            )
            if active:
                raise RuntimeError(f"已有索引任务运行中: {active['job_id']}")
            job_id = self.store.create_job(kind, target_id=target_id)
            self.futures[job_id] = self.executor.submit(self._run, job_id, work)
            return job_id

    def _run(self, job_id: str, work: Callable[[Callable[[int, str], None]], Any]) -> None:
        self.store.update_job(job_id, status="running", progress=0, started_at=_now(), phase="starting", message="任务已开始")

        def progress(percent: int, message: str = "") -> None:
            self.store.update_job(
                job_id,
                progress=max(0, min(int(percent), 99)),
                phase="working",
                message=message,
            )

        try:
            result = work(progress)
            self.store.update_job(job_id, status="succeeded", progress=100, phase="complete", message="任务完成", result=result, finished_at=_now())
        except Exception as error:
            self.store.update_job(
                job_id,
                status="failed",
                phase="failed",
                message="任务失败",
                error=f"{type(error).__name__}: {error}",
                finished_at=_now(),
            )
            traceback.print_exc()

    def get(self, job_id: str) -> Dict[str, Any] | None:
        return self.store.get_job(job_id)

    def list(self, limit: int = 50, offset: int = 0) -> list[Dict[str, Any]]:
        return self.store.list_jobs(limit=limit, offset=offset)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)
