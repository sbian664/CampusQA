import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event

from src.admin.control_store import AdminControlStore, hash_password
from src.admin.jobs import AdminJobManager


class AdminJobTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.store = AdminControlStore(
            Path(self.temp_dir.name) / "admin.db",
            username="admin",
            password_hash=hash_password("secret"),
        )
        self.manager = AdminJobManager(self.store)

    def tearDown(self):
        self.manager.shutdown()
        self.temp_dir.cleanup()

    def wait_for_terminal(self, job_id):
        deadline = time.time() + 3
        while time.time() < deadline:
            job = self.store.get_job(job_id)
            if job["status"] in {"succeeded", "failed", "interrupted"}:
                return job
            time.sleep(0.01)
        self.fail("job did not reach a terminal state")

    def test_job_persists_progress_and_completion(self):
        finished = Event()

        def work(progress):
            progress(40, "正在读取文档")
            finished.set()
            return {"updated_count": 2}

        job_id = self.manager.submit("scan", work)
        job = self.wait_for_terminal(job_id)

        self.assertTrue(finished.is_set())
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["result"]["updated_count"], 2)

    def test_only_one_index_mutation_runs_at_a_time(self):
        started = Event()
        release = Event()

        def work(progress):
            started.set()
            release.wait(2)

        first = self.manager.submit("rebuild", work)
        self.assertTrue(started.wait(1))
        with self.assertRaises(RuntimeError):
            self.manager.submit("scan", work)
        release.set()
        self.assertEqual(self.wait_for_terminal(first)["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
