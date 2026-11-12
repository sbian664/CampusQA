"""Turn-scoped rerank intent, independent of retrieval wording."""
from unittest.mock import patch

import pytest

from config import AGENT_SYSTEM_PROMPT
from src.chatbot import Chatbot
from src.llm_client import LLMResponse
from src.reranker import RerankScoreCache
from src.tools import SEARCH_KB_TOOL, ToolHandler


class RecordingKB:
    metadata = {}

    def __init__(self):
        self.queries = []

    def hybrid_search(self, query, top_k, filters=None, **kwargs):
        self.queries.append(query)
        return [{
            "source": f"course-{i}.md", "chunk_index": 0,
            "content": f"Evidence {i}", "score": 0.8, "bm25_score": 1,
        } for i in range(2)]


class RecordingModel:
    def __init__(self):
        self.calls = []

    def predict(self, pairs, batch_size, show_progress_bar):
        self.calls.append(list(pairs))
        return [0.9 - i / 10 for i, _ in enumerate(pairs)]


@pytest.fixture
def pipeline():
    kb, model = RecordingKB(), RecordingModel()
    handler = ToolHandler(kb, rerank_enabled=True,
                          reranker_loader=lambda: model,
                          user_query="What are its prerequisites?")
    with patch("src.reranker._score_cache", RerankScoreCache(128)), \
         patch("src.reranker.RERANKER_AVAILABLE", True):
        yield handler, kb, model


def search(handler, query="AIAA1010 prerequisites", **kwargs):
    return handler.execute("search_knowledge_base", {"query": query, **kwargs})


def test_schema_exposes_only_answer_oriented_modes():
    props = SEARCH_KB_TOOL["function"]["parameters"]["properties"]
    assert props["rerank_query_source"]["enum"] == ["user_query", "custom"]
    assert "rerank_query_update_reason" in props
    assert "追问或已拆分的子问题使用 search_query" not in AGENT_SYSTEM_PROMPT
    assert "search_query" not in str(SEARCH_KB_TOOL)


def test_default_uses_user_question_not_keywords(pipeline):
    handler, _, model = pipeline
    handler.user_query = "What are the prerequisites for AIAA1010?"
    search(handler, query="AIAA1010 prior courses")
    assert {q for q, _ in model.calls[0]} == {handler.user_query}
    trace = handler.get_call_log()[0]
    assert trace["rerank_query_source"] == "user_query"
    assert trace["rerank_query"] == handler.user_query


def test_custom_persists_when_keywords_change_or_fields_are_omitted(pipeline):
    handler, kb, model = pipeline
    full = "What are the prerequisites for AIAA1010?"
    search(handler, rerank_query_source="custom", rerank_query=full, top_k=1)
    search(handler, query="AIAA1010 prior courses", top_k=2)
    search(handler, query="AIAA1010 先修课程", rerank_query_source="custom")
    assert len(kb.queries) == 3
    assert len(model.calls) == 1
    assert all(entry["rerank_query"] == full for entry in handler.get_call_log())
    assert all(entry["rerank_query_source"] == "custom" for entry in handler.get_call_log())
    assert all(entry["rerank_query_revision"] == 1 for entry in handler.get_call_log())


def test_rewrite_requires_reason_and_new_text_gets_new_scores(pipeline):
    handler, kb, model = pipeline
    first = "What are the prerequisites for AIAA1010?"
    revised = "What are the prerequisites for AIAA1010 in the 2026 curriculum?"
    search(handler, rerank_query_source="custom", rerank_query=first)
    error = search(handler, rerank_query_source="custom", rerank_query=revised)
    assert error.startswith("[ERROR]")
    assert "rerank_query_update_reason" in error
    assert len(kb.queries) == 1
    assert len(model.calls) == 1
    reason = "The retrieved curriculum identifies a material year constraint."
    search(handler, rerank_query_source="custom", rerank_query=revised,
           rerank_query_update_reason=reason)
    search(handler, query="AIAA1010 entry requirements")
    assert len(model.calls) == 2
    assert {q for q, _ in model.calls[1]} == {revised}
    traces = handler.get_call_log()
    assert traces[2]["rerank_query_revision"] == 2
    assert traces[2]["rerank_query_update_reason"] == reason
    assert traces[3]["rerank_query"] == revised
    assert "error" not in traces[3]


def test_user_to_custom_correction_is_supported(pipeline):
    handler, _, model = pipeline
    search(handler)
    search(handler, rerank_query_source="custom",
           rerank_query="What are the prerequisites for AIAA1010?",
           rerank_query_update_reason="Resolve the omitted course from the previous turn.")
    assert len(model.calls) == 2
    assert handler.get_call_log()[-1]["rerank_query_source"] == "custom"


def test_explicit_user_query_does_not_silently_reset_existing_custom(pipeline):
    handler, kb, _ = pipeline
    search(handler, rerank_query_source="custom", rerank_query="Resolved full question")
    assert search(handler, rerank_query_source="user_query").startswith("[ERROR]")
    assert len(kb.queries) == 1
    search(handler)
    assert handler.get_call_log()[-1]["rerank_query"] == "Resolved full question"


@pytest.mark.parametrize("args", [
    {"rerank_query_source": "custom"},
    {"rerank_query_source": "custom", "rerank_query": "  "},
    {"rerank_query_source": "search_query"},
    {"rerank_query_source": "invalid"},
])
def test_invalid_first_choice_never_falls_back_to_keywords(pipeline, args):
    handler, kb, model = pipeline
    assert search(handler, **args).startswith("[ERROR]")
    assert kb.queries == []
    assert model.calls == []


def test_missing_user_context_cannot_turn_keywords_into_a_question(pipeline):
    handler, kb, model = pipeline
    handler.user_query = ""
    assert search(handler).startswith("[ERROR]")
    assert kb.queries == [] and model.calls == []


def test_custom_is_not_shared_between_user_turns(pipeline):
    handler, kb, model = pipeline
    search(handler, rerank_query_source="custom", rerank_query="Resolved full question")
    next_turn = ToolHandler(kb, rerank_enabled=True, reranker_loader=lambda: model,
                            user_query="An unrelated independent question")
    search(next_turn)
    assert next_turn.get_call_log()[0]["rerank_query"] == next_turn.user_query
    assert len(model.calls) == 2


def test_duplicate_detection_compares_effective_text_not_mode_label():
    past = [{"query": "AIAA1010 prerequisites", "top_k": 3, "filters": {},
             "rerank_query_source": "user_query", "rerank_query": "Full question"}]
    assert Chatbot._is_duplicate_search(
        "AIAA1010 prerequisites", 3, {}, past, "custom", "Full question")
    assert not Chatbot._is_duplicate_search(
        "AIAA1010 prerequisites", 3, {}, past, "custom", "Revised full question")


class SequenceClient:
    def __init__(self, arguments):
        self.arguments = iter(arguments)
        self.messages = []
        self.counter = 0

    def send_message_with_tools(self, messages, tools=None, **kwargs):
        self.messages.append(list(messages))
        args = next(self.arguments, None) if tools is not None else None
        if args is None:
            return LLMResponse(content="Answer", finish_reason="stop")
        self.counter += 1
        return LLMResponse(finish_reason="tool_calls", tool_calls=[{
            "id": f"call-{self.counter}", "type": "function",
            "function": {"name": "search_knowledge_base", "arguments": args},
        }])


def run_agent(arguments, message="What are its prerequisites?", original=None):
    kb, model = RecordingKB(), RecordingModel()
    bot = Chatbot.__new__(Chatbot)
    bot.client, bot.kb = SequenceClient(arguments), kb
    kwargs = {} if original is None else {"original_user_message": original}
    with patch("src.reranker._score_cache", RerankScoreCache(128)), \
         patch("src.reranker.RERANKER_AVAILABLE", True), \
         patch("src.tools.get_reranker_model", return_value=model), \
         patch("src.reranker.get_reranker_model", return_value=model):
        # The constructor's default loader was bound at definition time.
        with patch("src.reranker._model", model):
            result = bot.agent_chat(message, [], rerank_enabled=True, **kwargs)
    return result, kb, model, bot.client


def test_agent_guard_uses_inherited_custom_and_allows_material_revision():
    q = "AIAA1010 prerequisites"
    result, kb, model, _ = run_agent([
        {"query": q, "top_k": 1, "rerank_query_source": "custom", "rerank_query": "Full question"},
        {"query": q, "top_k": 1},
        {"query": q, "top_k": 1, "rerank_query_source": "custom", "rerank_query": "Revised full question",
         "rerank_query_update_reason": "The first search established a different course identity."},
        {"query": q, "top_k": 2},
    ])
    assert len(kb.queries) == 3  # Only the unchanged duplicate is blocked.
    assert len(model.calls) == 2
    assert result.content == "Answer"


def test_invalid_custom_does_not_poison_duplicate_guard():
    q = "AIAA1010 prerequisites"
    _, kb, model, _ = run_agent([
        {"query": q, "rerank_query_source": "custom"},
        {"query": q, "rerank_query_source": "custom", "rerank_query": "Resolved full question"},
    ])
    assert len(kb.queries) == 1 and len(model.calls) == 1


def test_context_router_rewrite_is_seeded_as_custom_not_raw_user_query():
    full = "What are the prerequisites for AIAA1010?"
    result, kb, model, client = run_agent(
        [{"query": "AIAA1010 prerequisites"}], message=full,
        original="What are its prerequisites?")
    assert len(kb.queries) == 1
    assert {q for q, _ in model.calls[0]} == {full}
    assert result.tool_call_log[0]["rerank_query_source"] == "custom"
    assert "What are its prerequisites?" in str(client.messages[0])


def test_standalone_router_preserves_original_question():
    from src.context_router import ContextRouter

    class Model:
        def classify_and_rewrite(self, current_query, history):
            return {"route": "standalone", "rewritten_query": "Unnecessary paraphrase",
                    "reason": "The input is independent."}

    question = "What are the prerequisites for AIAA1010?"
    route = ContextRouter(Model()).route(question, [{"role": "user", "content": "Earlier topic"}])
    assert route.rewritten_query == question
    assert route.selected_history == []


def test_resolver_preview_does_not_commit_revisions(pipeline):
    handler, _, _ = pipeline
    args = {"rerank_query_source": "custom", "rerank_query": "Resolved question"}
    assert handler._resolve_rerank_query(args) == ("Resolved question", "custom")
    assert handler._resolve_rerank_query({}) == (handler.user_query, "user_query")
    search(handler, **args)
    assert handler.get_call_log()[0]["rerank_query_revision"] == 1
