"""Sending an email has to either go out or say it didn't.

It used to persist the outbound message and answer success whether SMTP was
configured or not, and whether the send raised or not, so the inbox showed
emails as sent that the customer never got.
"""

import asyncio
import logging
import smtplib
import time
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.message import Message
from app.models.user import User
from app.services.email_service import MAIL_TIMEOUT_SECONDS

ADMIN = User(id="u-admin", name="Admin", email="admin@example.com", password_hash="x", role="admin")


@pytest.fixture
def smtp_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_user", "crm@example.com")
    monkeypatch.setattr(settings, "smtp_password", "pw")
    monkeypatch.setattr(settings, "smtp_from", "")


async def _make_contact(session_maker, *, email: str | None = "customer@example.com") -> str:
    async with session_maker() as session:
        contact = Contact(id="c1", name="Test", email=email)
        session.add(contact)
        await session.commit()
        return contact.id


async def _outbound_count(session_maker) -> int:
    async with session_maker() as session:
        return (
            await session.execute(
                select(func.count()).select_from(Message).where(Message.direction == "outbound")
            )
        ).scalar()


async def _post_send(client, contact_id: str):
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    try:
        return await client.post(
            "/api/messages/email/send",
            json={"contact_id": contact_id, "subject": "Hello", "body": "Quote attached"},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize(
    "host, user",
    [("", ""), ("", "crm@example.com"), ("smtp.example.com", "")],
)
async def test_send_without_smtp_is_503_and_not_saved(
    client, async_session_maker, monkeypatch, host, user
):
    """Guards AC3: a missing SMTP host or user answers 503 NOT_CONFIGURED and
    persists no outbound message."""
    monkeypatch.setattr(settings, "smtp_host", host)
    monkeypatch.setattr(settings, "smtp_user", user)

    contact_id = await _make_contact(async_session_maker)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NOT_CONFIGURED"
    smtp_cls.assert_not_called()
    assert await _outbound_count(async_session_maker) == 0


@pytest.mark.parametrize("failing_step", ["connect", "login", "send"])
async def test_send_error_is_502_logged_and_not_saved(
    client, async_session_maker, smtp_configured, caplog, failing_step
):
    """Guards AC4: an error while connecting, authenticating or sending answers
    502 API_ERROR, persists no outbound message and logs the error."""
    contact_id = await _make_contact(async_session_maker)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        if failing_step == "connect":
            smtp_cls.side_effect = OSError("connection refused")
        elif failing_step == "login":
            server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
        else:
            server.send_message.side_effect = smtplib.SMTPRecipientsRefused({})

        with caplog.at_level(logging.ERROR, logger="app.services.email_service"):
            resp = await _post_send(client, contact_id)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "API_ERROR"
    assert await _outbound_count(async_session_maker) == 0
    assert any(
        r.name == "app.services.email_service" and r.levelno >= logging.ERROR
        for r in caplog.records
    )


async def test_send_success_is_saved(client, async_session_maker, smtp_configured):
    """Guards AC4: a successful send still persists the outbound message and
    answers 200, as before."""
    contact_id = await _make_contact(async_session_maker)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["direction"] == "outbound"
    assert data["recipient_id"] == "customer@example.com"
    smtp_cls.assert_called_once_with(
        "smtp.example.com", settings.smtp_port, timeout=MAIL_TIMEOUT_SECONDS
    )
    server.login.assert_called_once_with("crm@example.com", "pw")
    server.send_message.assert_called_once()
    assert await _outbound_count(async_session_maker) == 1


async def test_contact_without_email_is_unchanged(client, async_session_maker, smtp_configured):
    """Guards AC4: a contact with no email still answers 400 without trying to
    send or persisting anything."""
    contact_id = await _make_contact(async_session_maker, email=None)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "Contact not found or has no email"
    smtp_cls.assert_not_called()
    assert await _outbound_count(async_session_maker) == 0


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


async def test_send_does_not_block_event_loop(client, async_session_maker, smtp_configured):
    """Guards AC3: the SMTP calls run via asyncio.to_thread with
    timeout=MAIL_TIMEOUT_SECONDS, so a 0.3s blocking send_message leaves the
    event loop free to run other coroutines."""
    contact_id = await _make_contact(async_session_maker)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        server.send_message.side_effect = lambda msg: time.sleep(0.3)
        resp, ticks = await _ticks_during(_post_send(client, contact_id))

    assert resp.status_code == 200
    assert ticks >= 3
    assert smtp_cls.call_args.kwargs["timeout"] == MAIL_TIMEOUT_SECONDS


async def test_send_timeout_is_502_and_not_saved(client, async_session_maker, smtp_configured):
    """Guards AC3: a TimeoutError from send_message answers 502 API_ERROR and
    persists no outbound message."""
    contact_id = await _make_contact(async_session_maker)

    with patch("app.services.email_service.smtplib.SMTP") as smtp_cls:
        server = smtp_cls.return_value.__enter__.return_value
        server.send_message.side_effect = TimeoutError("timed out")
        resp = await _post_send(client, contact_id)

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "API_ERROR"
    assert await _outbound_count(async_session_maker) == 0
