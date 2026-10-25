from src.agent_finalization import ProtocolSafeStream


def test_protocol_safe_stream_releases_plain_text_and_drops_split_tool_block():
    chunks = []
    stream = ProtocolSafeStream(chunks.append)

    assert stream.feed("Hello world").startswith("Hello worl")
    stream.feed("! <tool_")
    stream.feed("call>private internal data</tool_call>tail")
    stream.feed("", final=True)

    assert "".join(chunks) == "Hello world!"
    assert stream.emitted == "Hello world!"


