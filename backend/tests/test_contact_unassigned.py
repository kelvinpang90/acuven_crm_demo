"""The contact list can narrow to customers that have no owner (CRM-TASK-002).

Ownerless customers are visible to admins only, mixed in with everyone else's.
`unassigned=1` keeps just those, on top of every filter the list already has;
it never widens what a sales rep or manager can see.
"""

import pytest

from app.models.contact import Contact
from tests._people import ADMIN, BOSS, REP, logged_in_as, seed_people

URL = "/api/contacts"


async def _seed(session_maker) -> None:
    # seed_people brings c-mine (u-rep), c-theirs (u-other) and c-orphan (nobody).
    await seed_people(session_maker)
    async with session_maker() as session:
        session.add_all(
            [
                Contact(
                    id="c-orphan-2",
                    name="Orphan two",
                    phone="60155555555",
                    industry="Retail",
                    assigned_to=None,
                ),
                Contact(
                    id="c-orphan-old",
                    name="Archived orphan",
                    phone="60166666666",
                    assigned_to=None,
                    is_archived=1,
                ),
                Contact(
                    id="c-mine-old",
                    name="Archived mine",
                    phone="60177777777",
                    assigned_to="u-rep",
                    is_archived=1,
                ),
                Contact(
                    id="c-demo-orphan",
                    name="Demo orphan",
                    phone="60188888888",
                    assigned_to=None,
                    is_gateway=True,
                ),
            ]
        )
        await session.commit()


async def _list(client, user, **params):
    with logged_in_as(user):
        resp = await client.get(URL, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _ids(page) -> set[str]:
    return {c["id"] for c in page["data"]}


async def test_admin_sees_only_unassigned_with_matching_total(client, async_session_maker):
    """AC1: unassigned=1 returns only ownerless customers, and total counts just those
    (demo contacts stay excluded)."""
    await _seed(async_session_maker)
    page = await _list(client, ADMIN, unassigned=1)
    assert _ids(page) == {"c-orphan", "c-orphan-2"}
    assert page["total"] == 2
    assert all(c["assigned_to"] is None for c in page["data"])


async def test_total_is_filtered_count_across_pages(client, async_session_maker):
    """AC1: total is the filtered count, not the page length, when paging applies."""
    await _seed(async_session_maker)
    page = await _list(client, ADMIN, unassigned=1, page_size=1)
    assert len(page["data"]) == 1
    assert page["total"] == 2


async def test_unassigned_combines_with_archive(client, async_session_maker):
    """AC1: unassigned=1 and is_archived=1 apply together."""
    await _seed(async_session_maker)
    page = await _list(client, ADMIN, unassigned=1, is_archived=1)
    assert _ids(page) == {"c-orphan-old"}
    assert page["total"] == 1


async def test_unassigned_combines_with_search_and_industry(client, async_session_maker):
    """AC1: unassigned=1 applies together with the search and industry filters."""
    await _seed(async_session_maker)
    by_industry = await _list(client, ADMIN, unassigned=1, industry="Retail")
    by_search = await _list(client, ADMIN, unassigned=1, search="two")
    assert _ids(by_industry) == {"c-orphan-2"}
    assert by_industry["total"] == 1
    assert _ids(by_search) == {"c-orphan-2"}
    assert by_search["total"] == 1


@pytest.mark.parametrize("user", [REP, BOSS])
async def test_sales_and_manager_get_empty_list(client, async_session_maker, user):
    """AC3: sales and managers passing unassigned=1 get an empty list, total 0, no error."""
    await _seed(async_session_maker)
    page = await _list(client, user, unassigned=1)
    assert page["data"] == []
    assert page["total"] == 0


async def test_assigned_to_with_unassigned_is_empty(client, async_session_maker):
    """AC3: assigned_to together with unassigned=1 returns an empty list."""
    await _seed(async_session_maker)
    page = await _list(client, ADMIN, unassigned=1, assigned_to="u-rep")
    assert page["data"] == []
    assert page["total"] == 0


@pytest.mark.parametrize(
    "user, expected",
    [
        (ADMIN, {"c-mine", "c-theirs", "c-orphan", "c-orphan-2"}),
        (BOSS, {"c-mine"}),
        (REP, {"c-mine"}),
    ],
)
async def test_omitted_or_zero_leaves_list_unchanged(client, async_session_maker, user, expected):
    """AC2: leaving unassigned out or passing 0 gives the same list as before."""
    await _seed(async_session_maker)
    omitted = await _list(client, user)
    zero = await _list(client, user, unassigned=0)
    assert _ids(omitted) == expected
    assert omitted["total"] == len(expected)
    assert zero == omitted


@pytest.mark.parametrize("value", ["2", "-1", "abc", "true"])
async def test_invalid_value_is_422(client, async_session_maker, value):
    """AC1: any unassigned value other than 0 or 1 is rejected with 422."""
    await _seed(async_session_maker)
    with logged_in_as(ADMIN):
        resp = await client.get(URL, params={"unassigned": value})
    assert resp.status_code == 422
