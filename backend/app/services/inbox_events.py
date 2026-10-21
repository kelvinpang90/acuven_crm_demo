"""Tell open inbox pages that messages changed, so they refetch without a reload.

The signal carries no data: clients refetch through the normal, permission-checked
message endpoints, so the stream itself exposes nothing a user may not see.

Subscribers live in this process. The backend runs as a single uvicorn process;
running several would need Redis pub/sub in place of `_subscribers`.

Any committed write to `messages` publishes, whichever code path made it
(chatbot events, webhooks, agent sends, mark-read), via the session hooks below.
"""

import asyncio
import time
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session

from app.models.message import Message

HEARTBEAT_SECONDS = 20  # well under the 60s proxy read timeout
MAX_STREAM_SECONDS = 15 * 60  # the client reconnects with a fresh token
_CHANGED = "inbox_messages_changed"

_subscribers: set[asyncio.Queue[None]] = set()


def publish() -> None:
    for queue in _subscribers:
        # Size-1 queues: pending signals collapse into one refetch.
        if queue.empty():
            queue.put_nowait(None)


async def stream(
    heartbeat_seconds: float = HEARTBEAT_SECONDS,
    max_seconds: float = MAX_STREAM_SECONDS,
) -> AsyncIterator[str]:
    queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
    _subscribers.add(queue)
    deadline = time.monotonic() + max_seconds
    try:
        # Sent at once so the client knows the stream is open.
        yield ": connected\n\n"
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                await asyncio.wait_for(queue.get(), timeout=min(heartbeat_seconds, remaining))
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
            else:
                yield "event: messages_changed\ndata: {}\n\n"
    finally:
        _subscribers.discard(queue)


@event.listens_for(Session, "after_flush")
def _note_flushed_messages(session: Session, flush_context) -> None:
    # new / dirty / deleted still hold the pre-flush state here.
    if any(isinstance(obj, Message) for obj in (*session.new, *session.dirty, *session.deleted)):
        session.info[_CHANGED] = True


@event.listens_for(Session, "do_orm_execute")
def _note_bulk_message_writes(state: ORMExecuteState) -> None:
    # update(Message) / delete(Message) bypass the flush.
    if (state.is_update or state.is_delete) and state.bind_mapper is not None:
        if state.bind_mapper.class_ is Message:
            state.session.info[_CHANGED] = True


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    # A flag left by a rolled-back write only costs an extra refetch.
    if session.info.pop(_CHANGED, False):
        publish()
