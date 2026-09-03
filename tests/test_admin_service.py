import unittest
from tempfile import TemporaryDirectory

from src.admin.service import AdminService


class AdminServiceTests(unittest.TestCase):
    def test_overview_keeps_runtime_unknowns_explicit_and_limits_sessions(self):
        service = AdminService(
            health_provider=lambda: {"status": "ok", "timestamp": "2026-09-02T10:00:00+00:00"},
            kb_provider=lambda: {
                "total_files": 4,
                "total_chunks": 12,
                "total_size_mb": 1.5,
                "updated_at": None,
            },
            llm_provider=lambda: {
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_key": "sk-secret",
                "has_api_key": True,
            },
            mode_provider=lambda: {"agent_mode": True, "mode_name": "Agent 自主检索"},
            sessions_provider=lambda limit, offset, query: [
                {"session_id": f"s-{i}", "title": f"问题 {i}", "message_count": i}
                for i in range(offset, offset + limit)
            ],
        )

        overview = service.overview(session_limit=3)

        self.assertEqual(overview["knowledge_base"]["freshness"], "未采集")
        self.assertEqual(overview["sessions"]["items"][0]["session_id"], "s-0")
        self.assertEqual(len(overview["sessions"]["items"]), 3)
        self.assertTrue(overview["llm"]["has_api_key"])
        self.assertNotIn("sk-secret", str(overview))

    def test_trace_summary_marks_legacy_logs_without_inventing_hits(self):
        summary = AdminService.trace_summary([
            {"tool_name": "search_knowledge_base", "result_preview": "[结果 1]"},
            {"tool_name": "search_knowledge_base", "query": "课程注册", "hits": [{"source": "guide.md"}]},
        ])

        self.assertEqual(summary["count"], 2)
        self.assertTrue(summary["has_legacy_entries"])
        self.assertEqual(summary["structured_entries"], 1)

    def test_search_results_keep_channel_and_safe_hit_fields(self):
        result = AdminService.search_response(
            query="课程注册",
            channels={
                "vector": [{"source": "a.md", "content": "vector", "score": 0.4}],
                "bm25": [{"source": "b.md", "content": "bm25", "bm25_score": 1.2}],
                "hybrid": [],
                "reranked": [],
            },
            duration_ms=12,
        )

        self.assertEqual(result["query"], "课程注册")
        self.assertEqual(result["duration_ms"], 12)
        self.assertEqual(result["channels"]["vector"][0]["source"], "a.md")
        self.assertIn("content_snippet", result["channels"]["bm25"][0])

    def test_search_results_show_relative_sources_without_server_paths(self):
        with TemporaryDirectory() as temp_dir:
            result = AdminService.search_response(
                query="课程注册",
                channels={"bm25": [{"source": f"{temp_dir}/nested/guide.md", "title": f"{temp_dir}/nested/guide.md", "content": "bm25"}]},
                duration_ms=8,
                source_root=temp_dir,
            )

        self.assertEqual(result["channels"]["bm25"][0]["source"], "nested/guide.md")
        self.assertEqual(result["channels"]["bm25"][0]["title"], "nested/guide.md")


if __name__ == "__main__":
    unittest.main()
