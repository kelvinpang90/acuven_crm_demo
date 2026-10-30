"""Polling IMAP must not stall the event loop.

It used to call imaplib synchronously, with no timeout, straight on the event
loop, so a slow or stuck mail server froze every other request in the backend.
"""

import asyncio
import logging
import time
from unittest.mock import MagicMock

import pytest

from app.config import settings
from app.services import email_service
from app.services.email_service import MAIL_TIMEOUT_SECONDS, poll_emails


def _raw(message_id: str) -> bytes:
    return (
        f"Message-ID: <{message_id}>\r\n"
        "From: Customer <customer@example.com>\r\n"
        "Subject: Hi\r\n"
        "\r\n"
        "Hello\r\n"
    ).encode()


class FakeIMAP:
    """Stands in for the IMAP4_SSL class: calling it 'connects' and returns itself."""

    def __init__(self, messages: dict[bytes, bytes], *, fetch_sleep=0.0, fetch_error_on=None):
        self.messages = messages
        self.fetch_sleep = fetch_sleep
        self.fetch_error_on = fetch_error_on
        self.connect_kwargs = None
        self.stored: list[bytes] = []
        self.shutdown_called = False

    def __call__(self, host, port, **kwargs):
        self.connect_kwargs = kwargs
        return self

    def login(self, user, password):
        return "OK", [b"logged in"]

    def select(self, mailbox):
        return "OK", [str(len(self.messages)).encode()]

    def search(self, charset, criterion):
        return "OK", [b" ".join(self.messages)]

    def fetch(self, eid, parts):
        if self.fetch_sleep:
            time.sleep(self.fetch_sleep)
        if eid == self.fetch_error_on:
            raise TimeoutError("timed out")
        return "OK", [(b"RFC822", self.messages[eid])]

    def store(self, eid, command, flags):
        self.stored.append(eid)
        return "OK", []

    def logout(self):
        return "BYE", []

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def imap_configured(monkeypatch):
    monkeypatch.setattr(settings, "imap_host", "imap.example.com")
    monkeypatch.setattr(settings, "imap_user", "crm@example.com")
    monkeypatch.setattr(settings, "imap_password", "pw")


def _process_by_id(accepted: set[str]):
    async def fake_process(db, msg):
        return msg.get("Message-ID") in accepted

    return fake_process


async def _ticks_during(coro):
    """Await `coro` while a coroutine counts every 0.05s; return (result, ticks)."""
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        result = await coro
        counted = ticks
    finally:
        task.cancel()
    return result, counted


async def test_poll_does_not_block_event_loop(imap_configured, monkeypatch):
    """Guards AC2: every IMAP call runs via asyncio.to_thread, so a 0.3s blocking
    fetch leaves the event loop free to run other coroutines."""
    fake = FakeIMAP({b"1": _raw("a@x")}, fetch_sleep=0.3)
    monkeypatch.setattr(email_service, "IMAP4_SSL", fake)
    monkeypatch.setattr(email_service, "_process_email", _process_by_id({"<a@x>"}))

    count, ticks = await _ticks_during(poll_emails(None))

    assert count == 1
    assert ticks >= 3


async def test_poll_passes_timeout(imap_configured, monkeypatch):
    """Guards AC1/AC2: IMAP4_SSL is built with timeout=MAIL_TIMEOUT_SECONDS (30)."""
    fake = FakeIMAP({})
    monkeypatch.setattr(email_service, "IMAP4_SSL", fake)

    await poll_emails(None)

    assert MAIL_TIMEOUT_SECONDS == 30
    assert fake.connect_kwargs == {"timeout": MAIL_TIMEOUT_SECONDS}


async def test_only_processed_emails_are_marked_seen(imap_configured, monkeypatch):
    """Guards AC2: _process_email still decides; only the email it accepted is
    stored as Seen, and the count reflects that."""
    fake = FakeIMAP({b"1": _raw("ok@x"), b"2": _raw("skip@x")})
    monkeypatch.setattr(email_service, "IMAP4_SSL", fake)
    monkeypatch.setattr(email_service, "_process_email", _process_by_id({"<ok@x>"}))

    count = await poll_emails(None)

    assert count == 1
    assert fake.stored == [b"1"]


async def test_fetch_timeout_returns_count_logs_and_shuts_down(
    imap_configured, monkeypatch, caplog
):
    """Guards AC2: a TimeoutError from fetch is logged at ERROR, the count so far
    is returned, and the open connection is shut down."""
    fake = FakeIMAP({b"1": _raw("a@x"), b"2": _raw("b@x")}, fetch_error_on=b"2")
    monkeypatch.setattr(email_service, "IMAP4_SSL", fake)
    monkeypatch.setattr(email_service, "_process_email", _process_by_id({"<a@x>", "<b@x>"}))

    with caplog.at_level(logging.ERROR, logger="app.services.email_service"):
        count = await poll_emails(None)

    assert count == 1
    assert fake.stored == [b"1"]
    assert fake.shutdown_called
    assert any(
        r.name == "app.services.email_service" and r.levelno >= logging.ERROR
        for r in caplog.records
    )


async def test_poll_without_imap_does_not_connect(monkeypatch):
    """Guards AC2: with IMAP not configured, poll returns 0 without connecting."""
    monkeypatch.setattr(settings, "imap_host", "")
    monkeypatch.setattr(settings, "imap_user", "")
    imap_cls = MagicMock()
    monkeypatch.setattr(email_service, "IMAP4_SSL", imap_cls)

    assert await poll_emails(None) == 0
    imap_cls.assert_not_called()
