import pytest

from app.utils.sse_utils import create_sse_queue, get_sse_queue, push_to_session, sse_generator


class DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_sse_client_disconnect_releases_memory_queue():
    session_id = "sse-disconnect-test"
    create_sse_queue(session_id)
    push_to_session(session_id, "delta", {"delta": "synthetic"})
    stream = sse_generator(session_id, DisconnectedRequest())
    ready = await anext(stream)
    assert "event: ready" in ready
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert get_sse_queue(session_id) is None
