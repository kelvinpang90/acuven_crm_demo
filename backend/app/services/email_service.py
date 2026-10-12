"""Email IMAP/SMTP integration service.

Handles inbound email polling and outbound sending.
Polling is skipped when IMAP is not configured; sending without SMTP, or a
failed send, raises EmailSendError and persists nothing.
"""

import email
import logging
import smtplib
import uuid
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from imaplib import IMAP4_SSL
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.contact import Contact
from app.models.message import Message
from app.services import routing_service

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when sending an outbound email fails.

    `reason` is one of: "not_configured", "api_error".
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


async def poll_emails(db: AsyncSession) -> int:
    """Poll IMAP for new emails. Returns count of processed messages."""
    if not settings.imap_host or not settings.imap_user:
        return 0

    count = 0
    try:
        mail = IMAP4_SSL(settings.imap_host, settings.imap_port)
        mail.login(settings.imap_user, settings.imap_password)
        mail.select("INBOX")

        _, data = mail.search(None, "UNSEEN")
        ids = data[0].split()

        for eid in ids:
            _, msg_data = mail.fetch(eid, "(RFC822)")
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            processed = await _process_email(db, msg)
            if processed:
                mail.store(eid, "+FLAGS", "\\Seen")
                count += 1

        mail.logout()
    except Exception:
        logger.exception("Error polling emails")

    return count


async def _process_email(db: AsyncSession, msg: email.message.Message) -> bool:
    """Process a single email message."""
    message_id = msg.get("Message-ID", "")
    from_header = msg.get("From", "")
    subject = _decode_str(msg.get("Subject", ""))

    _, from_addr = parseaddr(from_header)
    from_name = _decode_str(from_header.split("<")[0].strip().strip('"'))
    if not from_name:
        from_name = from_addr.split("@")[0]

    # Extract body
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
                break
            elif ct == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode("utf-8", errors="replace")

    if not from_addr or not body:
        return False

    # Dedup by external_id
    if message_id:
        existing = await db.execute(select(Message).where(Message.external_id == message_id))
        if existing.scalar_one_or_none():
            return False

    # Find or create contact
    result = await db.execute(
        select(Contact).where(
            Contact.email == from_addr,
            Contact.deleted_at.is_(None),
        )
    )
    contact = result.scalar_one_or_none()

    if not contact:
        contact = Contact(
            id=str(uuid.uuid4()),
            name=from_name or from_addr,
            email=from_addr,
            status="lead",
        )
        db.add(contact)
        await db.flush()
        assigned = await routing_service.assign_contact(db, contact)
        if assigned:
            contact.assigned_to = assigned

    # Save message
    message = Message(
        id=str(uuid.uuid4()),
        contact_id=contact.id,
        channel="email",
        direction="inbound",
        sender_id=from_addr,
        recipient_id=settings.imap_user,
        subject=subject,
        body=body,
        external_id=message_id or None,
        assigned_to=contact.assigned_to,
    )
    db.add(message)
    await db.commit()
    return True


async def send_email(db: AsyncSession, contact_id: str, subject: str, body: str) -> Optional[dict]:
    """Send an email to a contact via SMTP.

    Returns None when the contact is missing or has no email. Raises
    EmailSendError when SMTP is not configured or the send fails; nothing is
    persisted then. On success, returns the persisted message as a dict.
    """
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))
    )
    contact = result.scalar_one_or_none()
    if not contact or not contact.email:
        return None

    if not (settings.smtp_host and settings.smtp_user):
        logger.warning("SMTP not configured — outbound email not sent")
        raise EmailSendError("not_configured", "SMTP host or user is not configured")

    try:
        msg = MIMEMultipart()
        msg["From"] = settings.smtp_from or settings.smtp_user
        msg["To"] = contact.email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
    except Exception as exc:
        logger.exception("Failed to send email")
        raise EmailSendError("api_error", str(exc)) from exc

    # Save outbound message
    message = Message(
        id=str(uuid.uuid4()),
        contact_id=contact_id,
        channel="email",
        direction="outbound",
        sender_id=settings.smtp_from or settings.smtp_user or "system",
        recipient_id=contact.email,
        subject=subject,
        body=body,
        is_read=True,
        assigned_to=contact.assigned_to,
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)

    return {
        "id": message.id,
        "contact_id": message.contact_id,
        "channel": message.channel,
        "direction": message.direction,
        "sender_id": message.sender_id,
        "recipient_id": message.recipient_id,
        "subject": message.subject,
        "body": message.body,
        "is_read": message.is_read,
        "assigned_to": message.assigned_to,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


def _decode_str(s: str) -> str:
    """Decode RFC2047 encoded string."""
    try:
        parts = decode_header(s)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return "".join(decoded)
    except Exception:
        return s
