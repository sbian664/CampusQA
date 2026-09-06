import unittest

from server import _public_tool_call_log


class PublicTracePrivacyTests(unittest.TestCase):
    def test_public_tool_log_keeps_summary_but_drops_retrieval_details(self):
        public_log = _public_tool_call_log([
            {
                "turn_id": "turn-1",
                "tool_name": "search_knowledge_base",
                "arguments": {
                    "query": "课程注册",
                    "top_k": 5,
                    "filters": {"doc_type": "md"},
                },
                "result_preview": "[结果 1] 内部知识库正文",
                "query": "课程注册",
                "engine": "reranked",
                "rerank_enabled": True,
                "duration_ms": 12,
                "result_count": 1,
                "hits": [{
                    "source": "/www/wwwroot/Agent/data/documents/secret.md",
                    "merged_content": "不应通过公开接口返回的完整正文",
                }],
            },
        ])

        self.assertEqual(len(public_log), 1)
        entry = public_log[0]
        self.assertEqual(entry["tool_name"], "search_knowledge_base")
        self.assertEqual(entry["query"], "课程注册")
        self.assertEqual(entry["result_count"], 1)
        self.assertNotIn("result_preview", entry)
        self.assertNotIn("hits", entry)
        self.assertNotIn("source", str(entry))
        self.assertNotIn("不应通过公开接口返回的完整正文", str(entry))


if __name__ == "__main__":
    unittest.main()
