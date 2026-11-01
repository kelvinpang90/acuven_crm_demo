"""CRM-TASK-093 rules for conversation claims (docs/chatbot-phase3.md 5.2, 14.4, 14.8).

Names follow design section 12: contact C1, sales S1, manager M1, now 10:00.
"""

from datetime import datetime, timedelta

import pytest

from app.models.contact import Contact
from app.services import claim_service
from app.services.claim_service import CLAIM_DURATION, CLAIMED, NOT_FOUND, OK

NOW = datetime(2026, 10, 30, 10, 0)
S1 = "s1"
M1 = "m1"


def _contact(**kwargs):
    values = {"id": "c1", "name": "Visitor", "phone": "60123456789", "assigned_to": S1}
    values.update(kwargs)
    return Contact(**values)


async def _seed(maker, *rows):
    async with maker() as session:
        session.add_all(rows)
        await session.commit()


async def _stored_claim(maker):
    async with maker() as session:
        contact = await session.get(Contact, "c1")
        return contact.reply_claimed_by, contact.reply_claimed_until


async def _claim(maker, user_id, *, now=NOW, takeover=False, contact_id="c1"):
    async with maker() as session:
        result = await claim_service.claim(
            session, contact_id=contact_id, user_id=user_id, takeover=takeover, now=now
        )
        await session.commit()
    return result


async def _release(maker, user_id, *, now=NOW, takeover=False, contact_id="c1"):
    async with maker() as session:
        result = await claim_service.release(
            session, contact_id=contact_id, user_id=user_id, takeover=takeover, now=now
        )
        await session.commit()
    return result


def test_claim_duration_is_fifteen_minutes():
    """Guards AC1: CLAIM_DURATION is 15 minutes."""
    assert CLAIM_DURATION == timedelta(minutes=15)


@pytest.mark.parametrize(
    ("claimed_by", "claimed_until", "expected"),
    [
        (None, None, None),
        (S1, None, None),
        (None, NOW + timedelta(minutes=5), None),
        (S1, NOW + timedelta(minutes=5), (S1, NOW + timedelta(minutes=5))),
        (S1, NOW, None),
        (S1, NOW - timedelta(minutes=1), None),
    ],
)
def test_active_claim_requires_holder_and_future_expiry(claimed_by, claimed_until, expected):
    """Guards AC1: a claim is valid only with a holder and now strictly before its expiry."""
    contact = _contact(reply_claimed_by=claimed_by, reply_claimed_until=claimed_until)
    assert claim_service.active_claim(contact, NOW) == expected


async def test_claim_unclaimed_contact(async_session_maker):
    """Guards AC2 (claim succeeds with nobody holding it); design section 12 example 1."""
    await _seed(async_session_maker, _contact())
    result = await _claim(async_session_maker, S1)
    assert (result.category, result.claimed_by, result.claimed_until) == (
        OK,
        S1,
        NOW + timedelta(minutes=15),
    )
    assert result.taken_over_from is None
    assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=15))


async def test_claim_own_claim_extends_it(async_session_maker):
    """Guards AC2 (the holder renews the claim to now + 15 minutes)."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=3)),
    )
    result = await _claim(async_session_maker, S1)
    assert result.category == OK
    assert await _stored_claim(async_session_maker) == (S1, NOW + timedelta(minutes=15))


async def test_claim_while_another_user_holds_it_is_refused(async_session_maker):
    """Guards AC2 (zero rows updated -> CLAIMED with holder and expiry, nothing changed);
    design section 12 example 2."""
    until = NOW + timedelta(minutes=15)
    await _seed(async_session_maker, _contact(reply_claimed_by=S1, reply_claimed_until=until))
    result = await _claim(async_session_maker, M1, now=NOW + timedelta(minutes=5))
    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, S1, until)
    assert await _stored_claim(async_session_maker) == (S1, until)


@pytest.mark.parametrize(
    "now", [NOW + timedelta(minutes=15), NOW + timedelta(minutes=16)], ids=["exact", "past"]
)
async def test_claim_after_another_users_claim_expired(async_session_maker, now):
    """Guards AC1 and AC2 (an expired claim, including one expiring exactly now, can be
    taken); design section 12 example 5."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=15)),
    )
    result = await _claim(async_session_maker, M1, now=now)
    assert result.category == OK
    assert await _stored_claim(async_session_maker) == (M1, now + timedelta(minutes=15))


async def test_takeover_returns_the_previous_holder(async_session_maker):
    """Guards AC2 (takeover overwrites and names the other valid holder); design section
    12 example 3."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=15)),
    )
    now = NOW + timedelta(minutes=5)
    result = await _claim(async_session_maker, M1, now=now, takeover=True)
    assert (result.category, result.claimed_by, result.taken_over_from) == (OK, M1, S1)
    assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=20))


@pytest.mark.parametrize(
    ("claimed_by", "claimed_until"),
    [(None, None), (S1, NOW), (M1, NOW + timedelta(minutes=10))],
    ids=["unclaimed", "expired", "own"],
)
async def test_takeover_without_another_valid_holder_names_nobody(
    async_session_maker, claimed_by, claimed_until
):
    """Guards AC2 (taken_over_from is empty when there is no valid claim or it is the
    caller's own)."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=claimed_by, reply_claimed_until=claimed_until),
    )
    result = await _claim(async_session_maker, M1, takeover=True)
    assert (result.category, result.taken_over_from) == (OK, None)
    assert await _stored_claim(async_session_maker) == (M1, NOW + timedelta(minutes=15))


@pytest.mark.parametrize(
    ("claimed_by", "claimed_until"),
    [(None, None), (S1, NOW + timedelta(minutes=10)), (M1, NOW)],
    ids=["unclaimed", "own", "other-expired"],
)
async def test_release_clears_unclaimed_own_or_expired(
    async_session_maker, claimed_by, claimed_until
):
    """Guards AC2 release (no holder, own claim or expired claim: both columns cleared)."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=claimed_by, reply_claimed_until=claimed_until),
    )
    assert (await _release(async_session_maker, S1)).category == OK
    assert await _stored_claim(async_session_maker) == (None, None)


async def test_release_of_another_users_valid_claim_is_refused(async_session_maker):
    """Guards AC2 release (another user's valid claim without takeover: CLAIMED, unchanged)."""
    until = NOW + timedelta(minutes=10)
    await _seed(async_session_maker, _contact(reply_claimed_by=S1, reply_claimed_until=until))
    result = await _release(async_session_maker, M1)
    assert (result.category, result.claimed_by, result.claimed_until) == (CLAIMED, S1, until)
    assert await _stored_claim(async_session_maker) == (S1, until)


async def test_release_with_takeover_clears_another_users_claim(async_session_maker):
    """Guards AC2 release (takeover clears unconditionally)."""
    await _seed(
        async_session_maker,
        _contact(reply_claimed_by=S1, reply_claimed_until=NOW + timedelta(minutes=10)),
    )
    assert (await _release(async_session_maker, M1, takeover=True)).category == OK
    assert await _stored_claim(async_session_maker) == (None, None)


@pytest.mark.parametrize("takeover", [False, True])
@pytest.mark.parametrize("deleted", [False, True], ids=["missing", "deleted"])
async def test_missing_or_deleted_contact_is_not_found(async_session_maker, takeover, deleted):
    """Guards AC2 (claim and release of a missing or soft-deleted contact return NOT_FOUND
    and change nothing)."""
    until = NOW + timedelta(minutes=10)
    if deleted:
        await _seed(
            async_session_maker,
            _contact(deleted_at=NOW, reply_claimed_by=S1, reply_claimed_until=until),
        )
    contact_id = "c1" if deleted else "missing"
    claimed = await _claim(async_session_maker, M1, takeover=takeover, contact_id=contact_id)
    released = await _release(async_session_maker, M1, takeover=takeover, contact_id=contact_id)
    assert claimed.category == NOT_FOUND
    assert released.category == NOT_FOUND
    if deleted:
        assert await _stored_claim(async_session_maker) == (S1, until)
