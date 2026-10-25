"""Possible duplicate customers: the three rules and who sees what
(docs/contact-duplicate-hints.md sections 3, 4, 6.1 and 9; CRM-TASK-060)."""

from datetime import datetime

import pytest

from app.models.contact import Contact
from app.services.duplicate_service import find_duplicates, normalize_company
from tests._people import ADMIN, BOSS, REP, seed_people


async def _add(session_maker, *contacts: Contact) -> None:
    async with session_maker() as session:
        session.add_all(contacts)
        await session.commit()


async def _check(session_maker, user=ADMIN, **details):
    fields = {"name": None, "company": None, "email": None, "phone": None}
    fields.update(details)
    async with session_maker() as session:
        return await find_duplicates(session, user=user, **fields)


def _ids(result) -> list[str]:
    return [contact.id for contact in result.visible]


@pytest.mark.parametrize(
    "left, right",
    [
        ("Tan Hardware Sdn Bhd", "TAN HARDWARE"),
        ("tan hardware sdn. bhd.", "TAN HARDWARE"),
        ("T.A.N. Hardware", "Tan Hardware"),
        ("陈氏贸易有限公司", "陈氏贸易"),
    ],
)
def test_normalize_company_same(left, right):
    """Acceptance 1 (normalize_company): punctuation, case, whole-word suffixes and
    Chinese trailing suffixes do not make two names differ."""
    assert normalize_company(left) == normalize_company(right)
    assert normalize_company(left) is not None


def test_normalize_company_keeps_partial_words_and_empty_is_none():
    """Acceptance 1 (normalize_company): a suffix that is only part of a word stays;
    an empty value is None."""
    assert normalize_company("Zinc") == "zinc"
    assert normalize_company("Mexico") == "mexico"
    assert normalize_company("") is None


async def test_phone_matches_after_normalizing(async_session_maker):
    """Acceptance 2 (phone rule): both sides normalized with normalize_phone."""
    await _add(async_session_maker, Contact(id="c-1", name="A", phone="60123456789"))
    result = await _check(async_session_maker, phone="012-345 6789")
    assert _ids(result) == ["c-1"]
    assert result.visible[0].rules == ["phone"]


async def test_phone_that_cannot_be_normalized_is_not_compared(async_session_maker):
    """Acceptance 2 (phone rule): numbers normalize_phone rejects are not compared."""
    await _add(async_session_maker, Contact(id="c-1", name="A", phone="12345"))
    result = await _check(async_session_maker, phone="12345")
    assert result.visible == [] and result.hidden_count == 0


async def test_email_ignores_case_and_outer_spaces(async_session_maker):
    """Acceptance 3 (email rule): trimmed and lowercased on both sides."""
    await _add(async_session_maker, Contact(id="c-1", name="A", email="  Tan@Example.COM "))
    result = await _check(async_session_maker, email=" tan@example.com")
    assert _ids(result) == ["c-1"]
    assert result.visible[0].rules == ["email"]


async def test_blank_email_is_not_compared(async_session_maker):
    """Acceptance 3 (email rule): an email that is empty once trimmed is not compared."""
    await _add(async_session_maker, Contact(id="c-1", name="A", email="   "))
    result = await _check(async_session_maker, email="  ")
    assert result.visible == [] and result.hidden_count == 0


async def test_same_name_and_company_match(async_session_maker):
    """Acceptance 4 (name_company rule): normalized name and company both equal."""
    await _add(
        async_session_maker,
        Contact(id="c-1", name="Tan Ah Kow", company="Tan Hardware Sdn Bhd"),
    )
    result = await _check(async_session_maker, name="tan ah-kow", company="TAN HARDWARE")
    assert _ids(result) == ["c-1"]
    assert result.visible[0].rules == ["name_company"]


@pytest.mark.parametrize(
    "name, company",
    [("Tan Ah Kow", "Lim Trading"), ("Lim Bee Ling", "Tan Hardware")],
)
async def test_only_name_or_only_company_does_not_match(async_session_maker, name, company):
    """Acceptance 4 (name_company rule): the same name alone or the same company
    alone is not a match."""
    await _add(async_session_maker, Contact(id="c-1", name="Tan Ah Kow", company="Tan Hardware"))
    result = await _check(async_session_maker, name=name, company=company)
    assert result.visible == [] and result.hidden_count == 0


async def test_number_as_name_takes_no_part(async_session_maker):
    """Acceptance 4 (name_company rule): a name that is only a number is not compared."""
    await _add(
        async_session_maker,
        Contact(id="c-1", name="+60 12-345 6789", company="Tan Hardware"),
    )
    result = await _check(async_session_maker, name="+60 12-345 6789", company="Tan Hardware")
    assert result.visible == [] and result.hidden_count == 0


async def test_every_rule_hit_is_listed(async_session_maker):
    """Acceptance 2 (rules list one entry per rule hit, in phone, email, name_company order)."""
    await _add(
        async_session_maker,
        Contact(id="c-1", name="A", phone="60123456789", email="a@example.com"),
    )
    result = await _check(async_session_maker, phone="0123456789", email="A@example.com")
    assert result.visible[0].rules == ["phone", "email"]


async def test_deleted_archived_and_excluded(async_session_maker):
    """Acceptance 2 (compared against non-deleted customers, archived included,
    other than exclude_id)."""
    await _add(
        async_session_maker,
        Contact(id="c-deleted", name="A", phone="60123456789", deleted_at=datetime(2026, 1, 1)),
        Contact(id="c-archived", name="B", phone="60123456789", is_archived=1),
        Contact(id="c-self", name="C", phone="60123456789"),
    )
    result = await _check(async_session_maker, phone="60123456789", exclude_id="c-self")
    assert _ids(result) == ["c-archived"]
    assert result.hidden_count == 0


async def _seed_owned(session_maker) -> None:
    await seed_people(session_maker)
    await _add(
        session_maker,
        Contact(id="d-rep", name="A", phone="60177777777", assigned_to="u-rep"),
        Contact(id="d-boss", name="B", phone="60177777777", assigned_to="u-boss"),
        Contact(id="d-other", name="C", phone="60177777777", assigned_to="u-other"),
        Contact(id="d-orphan", name="D", phone="60177777777", assigned_to=None),
    )


async def test_rep_sees_own_and_only_counts_others(async_session_maker):
    """Acceptance 3 (visibility): a rep's own match is visible with its owner's
    name; matches owned by others are only counted."""
    await _seed_owned(async_session_maker)
    result = await _check(async_session_maker, user=REP, phone="60177777777")
    assert _ids(result) == ["d-rep"]
    assert result.visible[0].assigned_to_name == "Rep"
    assert result.visible[0].name == "A"
    assert result.hidden_count == 3


async def test_manager_sees_team(async_session_maker):
    """Acceptance 3 (visibility): a manager sees matches owned by their team."""
    await _seed_owned(async_session_maker)
    result = await _check(async_session_maker, user=BOSS, phone="60177777777")
    assert set(_ids(result)) == {"d-rep", "d-boss"}
    assert result.hidden_count == 2


async def test_admin_sees_everything(async_session_maker):
    """Acceptance 3 (visibility): an admin sees every match."""
    await _seed_owned(async_session_maker)
    result = await _check(async_session_maker, user=ADMIN, phone="60177777777")
    assert set(_ids(result)) == {"d-rep", "d-boss", "d-other", "d-orphan"}
    assert result.hidden_count == 0


async def test_limit_and_order(async_session_maker):
    """Acceptance 3 (visible ordered by rules hit, most first, then oldest first,
    at most limit)."""
    await _add(
        async_session_maker,
        Contact(id="c-old", name="A", phone="60123456789", created_at=datetime(2026, 1, 1)),
        Contact(id="c-mid", name="B", phone="60123456789", created_at=datetime(2026, 2, 1)),
        Contact(id="c-new", name="C", phone="60123456789", created_at=datetime(2026, 3, 1)),
        Contact(
            id="c-both",
            name="D",
            phone="60123456789",
            email="d@example.com",
            created_at=datetime(2026, 4, 1),
        ),
    )
    result = await _check(async_session_maker, phone="60123456789", email="d@example.com", limit=3)
    assert _ids(result) == ["c-both", "c-old", "c-mid"]
    assert result.hidden_count == 0
