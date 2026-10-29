from unittest.mock import patch
from types import SimpleNamespace

from src.chatbot import Chatbot
from src.llm_client import LLMResponse
from src.knowledge_base import KnowledgeBase
from src.tools import ToolHandler


class KB:
    def hybrid_search(self, query, top_k, filters=None):
        return [{"source": f"doc-{i}.txt", "chunk_index": 10, "content": f"anchor-{i}",
                 "score": 1 - i / 10, "bm25_score": 1} for i in range(top_k)]

    def expand_adjacent_chunks(self, results, radius=1):
        return list(results) + [{"source": item["source"], "chunk_index": 9,
            "content": f"neighbor-{i}", "score": item["score"], "is_context_neighbor": True,
            "anchor_chunk": item["chunk_index"]} for i, item in enumerate(results)]


def run(scope, top_k=5):
    handler = ToolHandler(KB(), rerank_enabled=False)
    args = {"query": "course", "top_k": top_k}
    if scope is not None:
        args["entity_scope"] = scope
    output = handler.execute("search_knowledge_base", args)
    return output, handler.get_call_log()[0]


def test_only_first_three_anchors_get_context_for_single_entity():
    output, trace = run("single")
    for i in range(5):
        assert f"anchor-{i}" in output
        assert (f"neighbor-{i}" in output) == (i < 3)
    assert trace["result_count"] == 5
    assert len(trace["hits"]) == 5


def test_multiple_missing_and_small_result_counts_keep_old_behavior():
    for scope in ("multiple", None, "unknown"):
        output, _ = run(scope)
        assert all(f"neighbor-{i}" in output for i in range(5))
    output, _ = run("single", top_k=3)
    assert all(f"neighbor-{i}" in output for i in range(3))


def test_duplicate_check_allows_only_one_full_context_escalation():
    single = {"query": "course", "top_k": 5, "filters": {}, "entity_scope": "single"}
    assert not Chatbot._is_duplicate_search("course", 5, {}, [single], new_entity_scope="multiple")
    full = {**single, "entity_scope": "multiple"}
    assert Chatbot._is_duplicate_search("course", 5, {}, [single, full], new_entity_scope="multiple")


def test_same_response_conflicting_labels_are_conservatively_multiple():
    class Client:
        def __init__(self):
            self.calls = 0
        def send_message_with_tools(self, messages, tools=None, **kwargs):
            self.calls += 1
            if self.calls > 1:
                return LLMResponse(content="done", finish_reason="stop")
            return LLMResponse(finish_reason="tool_calls", tool_calls=[
                {"id": "a", "type": "function", "function": {"name": "search_knowledge_base",
                    "arguments": {"query": "course-a", "entity_scope": "single"}}},
                {"id": "b", "type": "function", "function": {"name": "search_knowledge_base",
                    "arguments": {"query": "course-b", "entity_scope": "multiple"}}},
            ])
    captured = []
    def execute(handler, name, arguments):
        captured.append(arguments.copy())
        return "[NO_RESULTS]"
    bot = Chatbot.__new__(Chatbot)
    bot.client, bot.kb = Client(), KB()
    with patch.object(ToolHandler, "execute", autospec=True, side_effect=execute):
        bot.agent_chat("compare A and B", [])
    assert [item["entity_scope"] for item in captured] == ["multiple", "multiple"]


def test_single_uses_final_reranked_order_not_original_candidate_order():
    kb = KB()
    ranked = kb.hybrid_search("course", 5)
    ranked = [ranked[i] for i in (4, 2, 0, 1, 3)]
    for i, item in enumerate(ranked):
        item["rerank_rank"] = i + 1
    handler = ToolHandler(kb, rerank_enabled=True)
    with patch("src.tools.search_with_optional_rerank", return_value=(ranked, ranked)):
        result = handler.execute("search_knowledge_base", {"query": "course", "top_k": 5, "entity_scope": "single"})
    assert all(f"neighbor-{i}" in result for i in range(3))
    assert "neighbor-3" not in result and "neighbor-4" not in result


def test_later_exact_hit_is_not_duplicated_as_a_top_three_neighbor():
    class AdjacentKB:
        expand_adjacent_chunks = KnowledgeBase.expand_adjacent_chunks
        def __init__(self):
            self._chunk_metadata = {str(i): {"source": "doc.txt", "chunk_index": i} for i in range(15)}
            self._chunk_texts = {str(i): f"content-{i}-end" for i in range(15)}
            self.store = SimpleNamespace(count=lambda: 15)
        def hybrid_search(self, query, top_k, filters=None):
            return [{"source": "doc.txt", "chunk_index": i, "content": self._chunk_texts[str(i)],
                     "score": 1 - pos / 10, "bm25_score": 1} for pos, i in enumerate((2, 5, 8, 3, 12))]
    handler = ToolHandler(AdjacentKB(), rerank_enabled=False)
    result = handler.execute("search_knowledge_base", {"query": "course", "top_k": 5, "entity_scope": "single"})
    assert result.count("content-3-end") == 1
    assert result.count("content-12-end") == 1
    assert "content-11-end" not in result and "content-13-end" not in result
    assert len(handler.get_call_log()[0]["hits"]) == 5
