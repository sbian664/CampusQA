from threading import Event
from unittest.mock import patch

import pytest

from src.llm_client import OpenAICompatibleClient


class StreamResponse:
    status_code = 200
    headers = {}

    def __init__(self, lines):
        self.lines = lines
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_lines(self, **kwargs):
        yield from self.lines

    def close(self):
        self.closed = True


class JsonStreamResponse(StreamResponse):
    def iter_content(self, chunk_size):
        yield b'{"choices":'
        self.event.set()
        yield b'[]}'

    def __init__(self, event):
        super().__init__([])
        self.event = event


def test_stream_message_emits_real_sse_deltas_and_usage():
    response = StreamResponse([
        'data: {"choices":[{"delta":{"content":"\\u4f60"},"finish_reason":null}]}'.encode('utf-8'),
        'data: {"choices":[{"delta":{"content":"\\u597d"},"finish_reason":null}]}'.encode('utf-8'),
        b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":2}}',
        b'data: [DONE]',
    ])
    client = OpenAICompatibleClient(config={"api_key": "test", "base_url": "https://example.test", "model": "test"})
    chunks = []
    with patch("src.llm_client.requests.post", return_value=response) as post:
        text = client.stream_message([{"role": "user", "content": "x"}], chunks.append, Event())

    assert text == "你好"
    assert chunks == ["你", "好"]
    assert client.last_usage == {"prompt_tokens": 2, "completion_tokens": 2}
    assert post.call_args.kwargs["stream"] is True
    assert response.closed


def test_stream_message_fails_if_upstream_does_not_provide_streaming():
    client = OpenAICompatibleClient(config={"api_key": "test", "base_url": "https://example.test", "model": "test"})
    with pytest.raises(RuntimeError, match="streaming"):
        with patch("src.llm_client.requests.post", return_value=object()):
            client.stream_message([], lambda _: None, Event())


def test_stream_message_rejects_truncated_answer():
    response = StreamResponse([
        b'data: {"choices":[{"delta":{"content":"part"},"finish_reason":null}]}',
        b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
        b'data: [DONE]',
    ])
    client = OpenAICompatibleClient(config={"api_key": "test", "base_url": "https://example.test", "model": "test"})
    with patch("src.llm_client.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="truncated"):
            client.stream_message([], lambda _: None, Event())


def test_tool_call_request_closes_upstream_when_cancelled():
    event = Event()
    response = JsonStreamResponse(event)
    client = OpenAICompatibleClient(config={"api_key": "test", "base_url": "https://example.test", "model": "test"})
    with patch("src.llm_client.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="cancelled"):
            client.send_message_with_tools([], cancel_event=event)
    assert response.closed
