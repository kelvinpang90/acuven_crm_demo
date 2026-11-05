"""Former gateway contacts are ordinary contacts (CRM-TASK-018).

docs/chatbot-replies.md section 8 and section 11, case 11: the two contacts
still flagged `is_gateway` show up in the contact list, deal list, pipeline,
admin dashboard counts and analytics like every chatbot visitor does. Each test
guards CRM-TASK-018 AC1 (the queries no longer filter on is_gateway, other
conditions unchanged) and AC3 (the case 11 visibility test).
"""

from sqlalchemy import select

from app.dependencies import get_current_user
from app.main import app
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.user import User
from app.routers import analytics
from app.services import contact_service, dashboard_service, deal_service

_ADMIN = User(
    id="u-admin",
    name="Admin",
    email="admin@example.com",
    password_hash="x",
    role="admin",
)


async def _seed(session_maker) -> None:
    """One ordinary lead and one former gateway visitor, both created 'today'."""
    async with session_maker() as session:
        session.add_all(
            [
                Contact(id="c-real", name="Genuine Sdn Bhd", phone="60111111111"),
                Contact(id="c-gw", name="60122222222", phone="60122222222", is_gateway=True),
                Deal(
                    id="d-real", contact_id="c-real", status="lead", priority="mid", amount=5000.0
                ),
                Deal(id="d-gw", contact_id="c-gw", status="lead", priority="mid", amount=0.0),
            ]
        )
        await session.commit()


async def test_contact_list_includes_gateway_contact(async_session_maker):
    """AC1 / AC3: the contact list returns the is_gateway contact and counts it."""
    await _seed(async_session_maker)

    async with async_session_maker() as session:
        result = await contact_service.list_contacts(session, _ADMIN)

    assert result["total"] == 2
    assert {c["id"] for c in result["data"]} == {"c-real", "c-gw"}


async def test_deal_list_includes_gateway_deal(async_session_maker):
    """AC1 / AC3: the deal list returns the gateway contact's deal."""
    await _seed(async_session_maker)

    async with async_session_maker() as session:
        deals = await deal_service.list_deals(session, _ADMIN)

    assert {d["id"] for d in deals} == {"d-real", "d-gw"}


async def test_pipeline_includes_gateway_deal(client, async_session_maker):
    """AC1 / AC3: the pipeline lead stage holds and counts the gateway contact's deal."""
    await _seed(async_session_maker)

    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    try:
        resp = await client.get("/api/pipeline")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200
    lead_stage = next(s for s in resp.json()["data"]["stages"] if s["status"] == "lead")
    assert lead_stage["count"] == 2
    assert {d["id"] for d in lead_stage["deals"]} == {"d-real", "d-gw"}


async def test_admin_dashboard_counts_gateway_contact(async_session_maker):
    """AC1 / AC3: the admin dashboard counts the gateway contact as a new lead
    and its deal in the new-lead funnel stage."""
    await _seed(async_session_maker)

    async with async_session_maker() as session:
        result = await dashboard_service.get_admin_dashboard(session)

    kpis = {k["key"]: k["value"] for k in result["kpis"]}
    assert kpis["new_leads_today"] == 2

    new_lead_stage = next(s for s in result["funnel"] if s["stage"] == "newLead")
    assert new_lead_stage["count"] == 2
    assert new_lead_stage["amount"] == 5000.0


async def test_analytics_deal_scope_includes_gateway_deal(async_session_maker):
    """AC1 / AC3: analytics builds every deal query from these scope conditions;
    the endpoint itself can't run here because its trend query uses MySQL-only
    `date_format`."""
    await _seed(async_session_maker)

    async with async_session_maker() as session:
        conditions = await analytics._get_scoped_deal_conditions(_ADMIN, session)
        deal_ids = (await session.execute(select(Deal.id).where(*conditions))).scalars().all()

    assert set(deal_ids) == {"d-real", "d-gw"}
