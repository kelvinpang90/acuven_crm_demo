"""`seed.py --reset` clears every table that points at contacts or users.

`wipe` must empty users even though reps point at their manager through
`users.manager_id` (MySQL checks that foreign key row by row inside a single
DELETE), and must clear the AutoCount documents and AI audit rows that would
otherwise be left pointing at deleted contacts and users. Projects are not tied
to contacts and stay.
"""

import seed
from sqlalchemy import func, select

from app.models.ai_request import AiRequest
from app.models.autocount_document import AutocountDocument
from app.models.project import Project
from app.models.user import User


async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def test_wipe_clears_users_and_leftovers_but_keeps_projects(async_session_maker):
    async with async_session_maker() as session:
        # The manager's id sorts first, so a plain DELETE reaches it before its reports.
        session.add(
            User(id="a-manager", name="M", email="m@example.com", password_hash="x", role="manager")
        )
        await session.flush()
        session.add(
            User(
                id="b-rep",
                name="R",
                email="r@example.com",
                password_hash="x",
                role="sales",
                manager_id="a-manager",
            )
        )
        session.add(AiRequest(id="ai-1", user_id="b-rep", feature="contact_summary", outcome="ok"))
        session.add(
            AutocountDocument(id="doc-1", doc_type="invoice", doc_no="IV-1", customer_code="C-1")
        )
        session.add(Project(id="p-1", customer_name="Kept"))
        await session.commit()

        await seed.wipe(session)

        assert await _count(session, User) == 0
        assert await _count(session, AiRequest) == 0
        assert await _count(session, AutocountDocument) == 0
        assert await _count(session, Project) == 1
