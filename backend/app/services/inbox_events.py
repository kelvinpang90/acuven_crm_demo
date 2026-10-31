"""Tell open inbox pages that messages changed, so they refetch without a reload.

The signal carries no data: clients refetch through the normal, permission-checked
message endpoints, so the stream itself exposes nothing a user may not see.

Subscribers live in this process. The backend runs as a single uvicorn process;
running several would need Redis pub/sub in place of `_subscribers`.

Any committed write to `messages` publishes, whichever code path made it
(chatbot events, webhooks, agent sends, mark-read), via the session hooks below.
Besides messages, committed changes to a contact's handover, owner and reply
claim columns publish too.
"""

import asyncio
import time
from collections.abc import AsyncIterator

from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session

from app.models.contact import Contact
from app.models.message import Message

HEARTBEAT_SECONDS = 20  # well under the 60s proxy read timeout
MAX_STREAM_SECONDS = 15 * 60  # the client reconnects with a fresh token
_CHANGED = "inbox_messages_changed"
# Contact columns the inbox shows; changing any of them refreshes open pages.
_INBOX_CONTACT_COLUMNS = frozenset(
    {
        "wa_handover_active",
        "wa_handover_expires_at",
        "wa_handover_updated_at",
        "assigned_to",
        "reply_claimed_by",
        "reply_claimed_until",
    }
)

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


def _inbox_columns_changed(contact: Contact) -> bool:
    attrs = inspect(contact).attrs
    return any(attrs[name].history.has_changes() for name in _INBOX_CONTACT_COLUMNS)


def _bulk_set_keys(state: ORMExecuteState) -> set[str]:
    statement = state.statement
    keys = [*(getattr(statement, "_values", None) or {})]
    keys += [key for key, _ in getattr(statement, "_ordered_values", None) or ()]
    # update(Contact) executed with a list of rows by primary key.
    params = state.parameters
    for row in params if isinstance(params, (list, tuple)) else [params or {}]:
        keys += [*row]
    return {key if isinstance(key, str) else getattr(key, "key", None) for key in keys}


@event.listens_for(Session, "after_flush")
def _note_flushed_messages(session: Session, flush_context) -> None:
    # new / dirty / deleted, and attribute history, still hold the pre-flush state here.
    if any(isinstance(obj, Message) for obj in (*session.new, *session.dirty, *session.deleted)):
        session.info[_CHANGED] = True
    elif any(isinstance(obj, Contact) and _inbox_columns_changed(obj) for obj in session.dirty):
        session.info[_CHANGED] = True


@event.listens_for(Session, "do_orm_execute")
def _note_bulk_message_writes(state: ORMExecuteState) -> None:
    # update(Message) / delete(Message) / update(Contact) bypass the flush.
    if (state.is_update or state.is_delete) and state.bind_mapper is not None:
        if state.bind_mapper.class_ is Message:
            state.session.info[_CHANGED] = True
        elif state.bind_mapper.class_ is Contact and state.is_update:
            if _bulk_set_keys(state) & _INBOX_CONTACT_COLUMNS:
                state.session.info[_CHANGED] = True


@event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    # A flag left by a rolled-back write only costs an extra refetch.
    if session.info.pop(_CHANGED, False):
        publish()
