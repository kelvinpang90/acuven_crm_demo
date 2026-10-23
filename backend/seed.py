"""
Demo seed script: full-coverage production simulation data.
Covers all features: users, routing rules, sales targets, contacts,
activities, tasks, and messages.

Run:
    python seed.py           # seed if empty
    python seed.py --reset   # wipe and re-seed
"""

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from passlib.context import CryptContext
from sqlalchemy import text

from app.database import AsyncSessionLocal, engine
from app.models.activity import Activity
from app.models.contact import Contact
from app.models.deal import Deal
from app.models.message import Message
from app.models.routing_rule import RoutingRule
from app.models.sales_target import SalesTarget
from app.models.task import Task
from app.models.user import User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hp(password: str) -> str:
    return pwd_context.hash(password)


def uid() -> str:
    return str(uuid.uuid4())


TODAY = date.today()
NOW = datetime.utcnow()
YEAR = TODAY.year
MONTH = TODAY.month
PREV_MONTH = MONTH - 1 if MONTH > 1 else 12
PREV_YEAR = YEAR if MONTH > 1 else YEAR - 1


async def wipe(session):
    for table in [
        "messages",
        "activities",
        "tasks",
        "sales_targets",
        "routing_rules",
        "deals",
        "contacts",
        "users",
    ]:
        await session.execute(text(f"DELETE FROM {table}"))
    await session.commit()
    print("Database wiped.")


async def seed():
    reset = "--reset" in sys.argv

    async with AsyncSessionLocal() as session:
        if not reset:
            result = await session.execute(text("SELECT COUNT(*) FROM users"))
            if (result.scalar() or 0) > 0:
                print("Database already seeded. Use --reset to re-seed.")
                return
        else:
            await wipe(session)

        # ------------------------------------------------------------------ #
        #  USERS                                                               #
        # ------------------------------------------------------------------ #
        # IDs
        admin_id = uid()
        mgr1_id = uid()
        mgr2_id = uid()
        sales1_id = uid()
        sales2_id = uid()
        sales3_id = uid()

        users = [
            User(
                id=admin_id,
                name="Alex Turner",
                email="admin@crm.com",
                password_hash=hp("Admin123"),
                role="admin",
                language="en",
            ),
            User(
                id=mgr1_id,
                name="Sarah Mitchell",
                email="sarah@crm.com",
                password_hash=hp("Manager123"),
                role="manager",
                language="en",
            ),
            User(
                id=mgr2_id,
                name="James Wilson",
                email="james@crm.com",
                password_hash=hp("Manager123"),
                role="manager",
                language="en",
            ),
            User(
                id=sales1_id,
                name="Ryan Chen",
                email="ryan@crm.com",
                password_hash=hp("Sales123"),
                role="sales",
                language="en",
                manager_id=mgr1_id,
            ),
            User(
                id=sales2_id,
                name="Emma Davis",
                email="emma@crm.com",
                password_hash=hp("Sales123"),
                role="sales",
                language="en",
                manager_id=mgr1_id,
            ),
            User(
                id=sales3_id,
                name="Marcus Johnson",
                email="marcus@crm.com",
                password_hash=hp("Sales123"),
                role="sales",
                language="en",
                manager_id=mgr2_id,
            ),
        ]
        session.add_all(users)

        # ------------------------------------------------------------------ #
        #  ROUTING RULES                                                       #
        # ------------------------------------------------------------------ #
        routing_rules = [
            RoutingRule(
                id=uid(),
                priority=1,
                is_active=True,
                name="High-Value Leads (>$100k)",
                strategy="workload",
                conditions={"deal_value_min": 100000},
                target_users=[sales1_id, sales2_id, sales3_id],
                created_by=admin_id,
            ),
            RoutingRule(
                id=uid(),
                priority=2,
                is_active=True,
                name="West Coast Region",
                strategy="region",
                conditions={
                    "address_keywords": ["CA", "WA", "OR", "California", "Seattle", "Portland"]
                },
                target_users=[sales1_id, sales2_id],
                created_by=admin_id,
            ),
            RoutingRule(
                id=uid(),
                priority=3,
                is_active=True,
                name="Default – Best Win Rate",
                strategy="win_rate",
                conditions={},
                target_users=[sales1_id, sales2_id, sales3_id],
                created_by=admin_id,
            ),
        ]
        session.add_all(routing_rules)

        # ------------------------------------------------------------------ #
        #  SALES TARGETS                                                       #
        # ------------------------------------------------------------------ #
        targets = []
        target_data = [
            # (user_id, amount, count)
            (sales1_id, Decimal("180000.00"), 4),
            (sales2_id, Decimal("150000.00"), 3),
            (sales3_id, Decimal("200000.00"), 5),
            (mgr1_id, Decimal("400000.00"), 8),
            (mgr2_id, Decimal("250000.00"), 6),
        ]
        for uid_, amount, count in target_data:
            # current month
            targets.append(
                SalesTarget(
                    id=uid(),
                    user_id=uid_,
                    year=YEAR,
                    month=MONTH,
                    target_amount=amount,
                    target_count=count,
                )
            )
            # previous month
            targets.append(
                SalesTarget(
                    id=uid(),
                    user_id=uid_,
                    year=PREV_YEAR,
                    month=PREV_MONTH,
                    target_amount=amount,
                    target_count=count,
                )
            )
        session.add_all(targets)

        # ------------------------------------------------------------------ #
        #  CONTACTS                                                            #
        # ------------------------------------------------------------------ #
        # 20 contacts covering all statuses, industries, priorities
        c = {k: uid() for k in range(20)}

        contact_rows = [
            # id_key, name, company, industry, status, priority, deal_value,
            # email, phone, address, tags, notes, assigned_to, days_ago
            (
                0,
                "Daniel Foster",
                "Apex Cloud Solutions",
                "Technology/IT",
                "won",
                "high",
                Decimal("145000.00"),
                "d.foster@apexcloud.io",
                "+1-415-555-0101",
                "San Francisco, CA",
                ["key account", "cloud"],
                "Closed deal for enterprise cloud migration package. 2-year contract.",
                sales1_id,
                45,
            ),
            (
                1,
                "Patricia Moore",
                "Meridian Finance Group",
                "Finance/Insurance",
                "won",
                "high",
                Decimal("220000.00"),
                "p.moore@meridianfin.com",
                "+1-212-555-0202",
                "New York, NY",
                ["key account", "fintech"],
                "Signed annual SaaS license. Upsell opportunity in Q3 for analytics module.",
                sales2_id,
                30,
            ),
            (
                2,
                "Kevin Nakamura",
                "BioCore Diagnostics",
                "Healthcare",
                "negotiating",
                "high",
                Decimal("310000.00"),
                "k.nakamura@biocore.com",
                "+1-617-555-0303",
                "Boston, MA",
                ["healthcare", "enterprise"],
                "Final round of negotiation. Legal reviewing MSA. Decision expected by end of month.",
                sales1_id,
                5,
            ),
            (
                3,
                "Laura Simmons",
                "GreenLeaf Energy",
                "Energy/Utilities",
                "negotiating",
                "high",
                Decimal("185000.00"),
                "l.simmons@greenleaf.energy",
                "+1-512-555-0404",
                "Austin, TX",
                ["energy", "sustainability"],
                "Proposal submitted. Awaiting board approval. Champion is VP of Operations.",
                sales3_id,
                8,
            ),
            (
                4,
                "Chris Patel",
                "NexGen Retail",
                "Retail/E-commerce",
                "following",
                "high",
                Decimal("95000.00"),
                "c.patel@nexgenretail.com",
                "+1-310-555-0505",
                "Los Angeles, CA",
                ["retail", "omnichannel"],
                "Interested in inventory CRM integration. Needs demo of WhatsApp broadcast feature.",
                sales2_id,
                12,
            ),
            (
                5,
                "Amanda Torres",
                "Vertex Manufacturing",
                "Manufacturing",
                "following",
                "mid",
                Decimal("78000.00"),
                "a.torres@vertexmfg.com",
                "+1-216-555-0606",
                "Cleveland, OH",
                ["manufacturing"],
                "Pain point is after-sales service tracking. Sent comparison doc vs competitor.",
                sales3_id,
                15,
            ),
            (
                6,
                "Brian O'Sullivan",
                "Summit Real Estate",
                "Real Estate",
                "following",
                "mid",
                Decimal("130000.00"),
                "b.osullivan@summitrealty.com",
                "+1-602-555-0707",
                "Phoenix, AZ",
                ["real estate", "proptech"],
                "Uses legacy CRM. Evaluating us and two other vendors. Decision in 6 weeks.",
                sales1_id,
                20,
            ),
            (
                7,
                "Jennifer Kim",
                "Clarity Consulting",
                "Consulting/Services",
                "following",
                "mid",
                Decimal("55000.00"),
                "j.kim@clarityconsult.com",
                "+1-206-555-0808",
                "Seattle, WA",
                ["consulting", "smb"],
                "Small team of 12. Needs simple contact management + task reminders.",
                sales2_id,
                18,
            ),
            (
                8,
                "Robert Nguyen",
                "FreshBite Foods",
                "Food/Hospitality",
                "lead",
                "mid",
                Decimal("42000.00"),
                "r.nguyen@freshbite.com",
                "+1-713-555-0909",
                "Houston, TX",
                ["food", "franchise"],
                "Inbound inquiry via website. Franchise with 30 locations. Initial call scheduled.",
                sales3_id,
                3,
            ),
            (
                9,
                "Samantha Lee",
                "SwiftLog Logistics",
                "Logistics/Supply Chain",
                "lead",
                "mid",
                Decimal("67000.00"),
                "s.lee@swiftlog.com",
                "+1-630-555-1010",
                "Chicago, IL",
                ["logistics", "3pl"],
                "Referred by existing client Apex Cloud. Needs route optimization CRM hooks.",
                sales1_id,
                2,
            ),
            (
                10,
                "Thomas Brown",
                "EduPath Online",
                "Education/Training",
                "lead",
                "low",
                Decimal("28000.00"),
                "t.brown@edupathonline.com",
                "+1-919-555-1111",
                "Raleigh, NC",
                ["edtech", "startup"],
                "Early-stage edtech startup. Budget limited but growing fast. Watch for Q4.",
                sales2_id,
                1,
            ),
            (
                11,
                "Nancy Wright",
                "CoreLogic Analytics",
                "Technology/IT",
                "lead",
                "high",
                Decimal("260000.00"),
                "n.wright@corelogic.ai",
                "+1-408-555-1212",
                "San Jose, CA",
                ["ai", "analytics", "enterprise"],
                "Warm intro from board member. Looking for CRM with custom pipeline stages.",
                sales1_id,
                0,
            ),
            (
                12,
                "Michael Scott",
                "Dunder Paper Co.",
                "Manufacturing",
                "lost",
                "low",
                Decimal("35000.00"),
                "m.scott@dunderpaper.com",
                "+1-570-555-1313",
                "Scranton, PA",
                ["smb", "paper"],
                "Lost to competitor on price. Team of 5, very cost-sensitive. May revisit in 2025.",
                sales3_id,
                60,
            ),
            (
                13,
                "Rachel Green",
                "Central Perk Ventures",
                "Food/Hospitality",
                "lost",
                "mid",
                Decimal("48000.00"),
                "r.green@centralperk.vc",
                "+1-212-555-1414",
                "New York, NY",
                ["hospitality", "vc-backed"],
                "Project frozen due to funding round. Contact again after Series B closes.",
                sales2_id,
                50,
            ),
            (
                14,
                "David Park",
                "MedTech Innovations",
                "Healthcare",
                "won",
                "high",
                Decimal("390000.00"),
                "d.park@medtechinno.com",
                "+1-858-555-1515",
                "San Diego, CA",
                ["healthtech", "key account"],
                "Largest deal this quarter. Full platform license + implementation services.",
                sales3_id,
                25,
            ),
            (
                15,
                "Linda Martinez",
                "Pacific Realty Group",
                "Real Estate",
                "negotiating",
                "mid",
                Decimal("112000.00"),
                "l.martinez@pacificrealty.com",
                "+1-619-555-1616",
                "San Diego, CA",
                ["real estate"],
                "Shortlisted us. Last hurdle is IT security review. Expected close next week.",
                sales1_id,
                6,
            ),
            (
                16,
                "James Clark",
                "Atlas Insurance",
                "Finance/Insurance",
                "following",
                "high",
                Decimal("175000.00"),
                "j.clark@atlasinsure.com",
                "+1-312-555-1717",
                "Chicago, IL",
                ["insurance", "enterprise"],
                "Had a great demo call. Mapping our features to their compliance requirements.",
                sales2_id,
                10,
            ),
            (
                17,
                "Olivia White",
                "TechForward Staffing",
                "Consulting/Services",
                "lead",
                "mid",
                Decimal("52000.00"),
                "o.white@techforward.hr",
                "+1-404-555-1818",
                "Atlanta, GA",
                ["hr-tech", "staffing"],
                "Found us on G2. Evaluating 3 CRMs simultaneously. Send comparison matrix.",
                sales3_id,
                1,
            ),
            (
                18,
                "Henry Adams",
                "CleanTech Systems",
                "Energy/Utilities",
                "following",
                "mid",
                Decimal("88000.00"),
                "h.adams@cleantech.systems",
                "+1-303-555-1919",
                "Denver, CO",
                ["cleantech", "iot"],
                "POC underway. Evaluating API integration with their IoT platform.",
                sales1_id,
                9,
            ),
            (
                19,
                "Sophie Turner",
                "Bloom E-commerce",
                "Retail/E-commerce",
                "negotiating",
                "high",
                Decimal("142000.00"),
                "s.turner@bloomeco.com",
                "+1-503-555-2020",
                "Portland, OR",
                ["ecommerce", "d2c"],
                "D2C brand scaling fast. Negotiating on seat count and custom onboarding.",
                sales2_id,
                4,
            ),
        ]

        # contact_key -> the ai_chatbot_demo industry bot the deal came in through,
        # picked from the company's line of business.
        deal_sources = {
            0: "saas",  # Apex Cloud Solutions
            1: "banking",  # Meridian Finance Group
            2: "saas",  # BioCore Diagnostics
            3: "saas",  # GreenLeaf Energy
            4: "retail",  # NexGen Retail
            5: "saas",  # Vertex Manufacturing
            6: "realestate",  # Summit Real Estate
            7: "saas",  # Clarity Consulting
            8: "food",  # FreshBite Foods
            9: "saas",  # SwiftLog Logistics
            10: "saas",  # EduPath Online
            11: "saas",  # CoreLogic Analytics
            12: "retail",  # Dunder Paper Co.
            13: "hotel",  # Central Perk Ventures
            14: "saas",  # MedTech Innovations
            15: "realestate",  # Pacific Realty Group
            16: "banking",  # Atlas Insurance
            17: "saas",  # TechForward Staffing
            18: "saas",  # CleanTech Systems
            19: "retail",  # Bloom E-commerce
        }

        contacts = []
        seed_deals = []
        d: dict[int, str] = {}  # contact_key → deal_id
        for (
            key,
            name,
            company,
            industry,
            status,
            priority,
            deal_value,
            email,
            phone,
            address,
            tags,
            notes,
            assigned_to,
            days_ago,
        ) in contact_rows:
            deal_id = uid()
            d[key] = deal_id
            created_ts = NOW - timedelta(days=days_ago)
            contacts.append(
                Contact(
                    id=c[key],
                    name=name,
                    company=company,
                    industry=industry,
                    email=email,
                    phone=phone,
                    address=address,
                    tags=tags,
                    notes=notes,
                    assigned_to=assigned_to,
                    last_contact=TODAY - timedelta(days=days_ago),
                )
            )
            seed_deals.append(
                Deal(
                    id=deal_id,
                    contact_id=c[key],
                    status=status,
                    priority=priority,
                    amount=deal_value,
                    source=deal_sources[key],
                    assigned_to=assigned_to,
                    won_at=created_ts if status == "won" else None,
                    created_at=created_ts,
                    updated_at=created_ts,
                )
            )
        session.add_all(contacts)
        session.add_all(seed_deals)

        # ------------------------------------------------------------------ #
        #  ACTIVITIES                                                          #
        # ------------------------------------------------------------------ #
        activities = []

        def act(contact_key, user_id, atype, content, days_ago, hours=0):
            activities.append(
                Activity(
                    id=uid(),
                    contact_id=c[contact_key],
                    deal_id=d[contact_key],
                    user_id=user_id,
                    type=atype,
                    content=content,
                    follow_date=NOW - timedelta(days=days_ago, hours=hours),
                )
            )

        # Won contacts – rich history
        act(
            0,
            sales1_id,
            "phone",
            "Initial discovery call with Daniel. He confirmed they're actively evaluating CRMs for their 200-seat cloud ops team.",
            50,
        )
        act(
            0,
            sales1_id,
            "email",
            "Sent product overview deck and pricing sheet. Daniel responded positively within 2 hours.",
            47,
        )
        act(
            0,
            sales1_id,
            "meeting",
            "Live demo with Daniel and his IT Director. Showcased pipeline automation and WhatsApp integration. Very positive reaction.",
            43,
        )
        act(
            0,
            sales1_id,
            "email",
            "Sent customized proposal for 200-seat enterprise plan at $145,000/year.",
            40,
        )
        act(
            0,
            sales1_id,
            "phone",
            "Negotiation call. Daniel asked for 10% discount and extended payment terms. Agreed on 5% + net-60.",
            46,
            hours=3,
        )
        act(
            0,
            sales1_id,
            "other",
            "Contract signed. Deal closed at $145,000. Kickoff call scheduled for next Monday.",
            45,
        )

        act(
            1,
            sales2_id,
            "phone",
            "Intro call with Patricia. Meridian is unhappy with Salesforce costs and looking for a modern alternative.",
            38,
        )
        act(
            1,
            sales2_id,
            "meeting",
            "Full platform demo for Patricia and 3 stakeholders. Deep-dived into analytics dashboard and role-based access.",
            34,
        )
        act(
            1,
            sales2_id,
            "WhatsApp",
            "Quick check-in via WhatsApp. Patricia confirmed the team loved the demo and is moving to approval stage.",
            32,
        )
        act(
            1,
            sales2_id,
            "email",
            "Sent MSA and SOW for legal review. Included reference customer contacts as requested.",
            31,
        )
        act(
            1,
            sales2_id,
            "other",
            "Deal signed. $220,000 annual contract. CSM introduced. Migration support starts next week.",
            30,
        )

        act(
            14,
            sales3_id,
            "phone",
            "First contact with David. MedTech needs HIPAA-aware CRM with full audit trail.",
            55,
        )
        act(
            14,
            sales3_id,
            "meeting",
            "Security and compliance review session with David's IT and legal team.",
            45,
        )
        act(
            14,
            sales3_id,
            "email",
            "Provided SOC 2 Type II report and HIPAA compliance documentation.",
            40,
        )
        act(
            14,
            sales3_id,
            "meeting",
            "Executive sponsor meeting with MedTech CTO. Discussed implementation roadmap.",
            35,
        )
        act(
            14,
            sales3_id,
            "phone",
            "Final pricing negotiation. Agreed on $390,000 for full platform + implementation bundle.",
            26,
        )
        act(
            14,
            sales3_id,
            "other",
            "Contract executed. Largest deal this quarter. Implementation kickoff in 2 weeks.",
            25,
        )

        # Negotiating contacts
        act(
            2,
            sales1_id,
            "phone",
            "Initial call with Kevin. BioCore needs CRM for their 50-person sales team across 3 divisions.",
            30,
        )
        act(
            2,
            sales1_id,
            "meeting",
            "Product demo tailored to lab equipment sales workflow. Kevin was impressed by activity timeline.",
            22,
        )
        act(
            2,
            sales1_id,
            "email",
            "Sent proposal for $310,000 including custom onboarding and dedicated CSM.",
            15,
        )
        act(
            2,
            sales1_id,
            "phone",
            "Negotiation call. Kevin pushed back on price. Held firm but offered free implementation support.",
            10,
        )
        act(
            2,
            sales1_id,
            "email",
            "Legal sent back MSA with 12 redlines. Forwarded to our legal team. Targeting close by end of month.",
            5,
        )

        act(
            3,
            sales3_id,
            "email",
            "Responded to inbound inquiry from Laura about CRM for energy project management.",
            25,
        )
        act(
            3,
            sales3_id,
            "meeting",
            "Demo call with Laura and her VP of Operations. Focused on task management and pipeline views.",
            18,
        )
        act(
            3,
            sales3_id,
            "email",
            "Submitted formal proposal for $185,000. Laura confirmed she is championing this internally.",
            10,
        )
        act(
            3,
            sales3_id,
            "WhatsApp",
            "Laura messaged: board presentation is Thursday. Will have decision Friday.",
            8,
        )

        act(
            15,
            sales1_id,
            "phone",
            "Discovery with Linda. Pacific Realty wants CRM with mobile-first design for their field agents.",
            20,
        )
        act(
            15,
            sales1_id,
            "meeting",
            "Demo focused on mobile experience and WhatsApp integration for property follow-ups.",
            14,
        )
        act(
            15,
            sales1_id,
            "email",
            "Proposal sent: $112,000 for 60-seat license. Linda said it looks good, pending IT review.",
            8,
        )
        act(
            15,
            sales1_id,
            "phone",
            "IT security review complete – passed. Waiting on final sign-off from CFO.",
            6,
        )

        act(
            19,
            sales2_id,
            "phone",
            "Intro call with Sophie. Bloom is growing 3x YoY and their spreadsheet-based system is breaking.",
            20,
        )
        act(
            19,
            sales2_id,
            "meeting",
            "Full demo for Sophie and Head of Growth. They loved the pipeline and inbox features.",
            14,
        )
        act(
            19,
            sales2_id,
            "email",
            "Sent proposal for $142,000 with tiered onboarding. Sophie countered asking for 15 extra seats free.",
            8,
        )
        act(
            19,
            sales2_id,
            "WhatsApp",
            "Sophie: 'We're very close to yes. Just finalizing the seat count internally. Talk Thursday?'",
            4,
        )

        # Following contacts
        act(
            4,
            sales2_id,
            "phone",
            "Discovery call with Chris. NexGen runs 8 retail brands and needs unified customer view.",
            20,
        )
        act(
            4,
            sales2_id,
            "email",
            "Sent case study for a similar retail client. Chris forwarded to his CTO.",
            15,
        )
        act(
            4,
            sales2_id,
            "WhatsApp",
            "Confirmed demo date for next Tuesday. Chris asked specifically about WhatsApp broadcast feature.",
            12,
        )

        act(
            5,
            sales3_id,
            "email",
            "Reached out to Amanda after trade show. Vertex struggling with tracking post-sale service requests.",
            22,
        )
        act(
            5,
            sales3_id,
            "phone",
            "Discovery call. 15-person sales team, mainly B2B industrial. Interested in activity log and tasks.",
            15,
        )

        act(
            6,
            sales1_id,
            "phone",
            "Brian reached out via referral. Currently using a 10-year-old CRM. Looking to modernize.",
            30,
        )
        act(
            6,
            sales1_id,
            "meeting",
            "Demo with Brian and two of his agents. Strong interest. They're comparing us with HubSpot and Pipedrive.",
            20,
        )

        act(
            7,
            sales2_id,
            "email",
            "Jennifer filled out the contact form. Small consultancy, budget around $50k.",
            22,
        )
        act(
            7,
            sales2_id,
            "phone",
            "Qualification call. Clear fit for our Standard plan. Sent pricing.",
            18,
        )

        act(
            16,
            sales2_id,
            "phone",
            "James Clark inbound from LinkedIn ad. Atlas has 40 sales reps across 5 states.",
            18,
        )
        act(
            16,
            sales2_id,
            "meeting",
            "Demo went well. James highlighted compliance reporting as critical requirement.",
            10,
        )

        act(
            18,
            sales1_id,
            "email",
            "Henry reached out after reading our blog post on IoT + CRM integration.",
            15,
        )
        act(
            18,
            sales1_id,
            "meeting",
            "Technical deep-dive call with Henry and their CTO on API capabilities.",
            9,
        )

        # New leads
        act(
            8,
            sales3_id,
            "email",
            "Responded to Robert's website inquiry. Scheduled intro call for Thursday.",
            3,
        )

        act(
            9,
            sales1_id,
            "phone",
            "Samantha called – referral from Daniel Foster at Apex Cloud. Very warm intro. Demo booked for next week.",
            2,
        )

        act(
            11,
            sales1_id,
            "email",
            "Nancy was introduced by a board member. Sent calendar link for discovery call.",
            0,
        )

        act(
            17,
            sales3_id,
            "email",
            "Olivia filled the G2 lead form. Sent comparison matrix vs HubSpot and Salesforce.",
            1,
        )

        act(
            10,
            sales2_id,
            "email",
            "Thomas reached out on LinkedIn. Early-stage but promising. Added to nurture sequence.",
            1,
        )

        # Lost contacts – brief history
        act(
            12,
            sales3_id,
            "phone",
            "Discovery with Michael. Small paper company, 5 reps. Very price-sensitive.",
            75,
        )
        act(
            12,
            sales3_id,
            "email",
            "Sent proposal. Michael said our price is 40% above what they budgeted.",
            68,
        )
        act(
            12,
            sales3_id,
            "other",
            "Lost to a cheaper competitor. Left door open for 2025 when they plan to expand.",
            60,
        )

        act(
            13,
            sales2_id,
            "phone",
            "Rachel expressed strong interest. Central Perk has 12 hospitality venues.",
            65,
        )
        act(
            13,
            sales2_id,
            "meeting",
            "Stakeholder demo. Strong positive feedback. Moving to proposal stage.",
            58,
        )
        act(
            13,
            sales2_id,
            "email",
            "Rachel called – project frozen due to unexpected funding delay. Will re-engage post Series B.",
            50,
        )

        session.add_all(activities)

        # ------------------------------------------------------------------ #
        #  TASKS                                                               #
        # ------------------------------------------------------------------ #
        tasks = [
            Task(
                id=uid(),
                title="Send updated MSA to BioCore legal team",
                contact_id=c[2],
                assigned_to=sales1_id,
                priority="high",
                due_date=TODAY + timedelta(days=1),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Follow up with Laura Simmons – board decision expected Friday",
                contact_id=c[3],
                assigned_to=sales3_id,
                priority="high",
                due_date=TODAY + timedelta(days=2),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Demo WhatsApp broadcast to Chris Patel (NexGen Retail)",
                contact_id=c[4],
                assigned_to=sales2_id,
                priority="high",
                due_date=TODAY + timedelta(days=3),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Prepare Q2 pipeline review deck for Sarah",
                contact_id=None,
                assigned_to=mgr1_id,
                priority="high",
                due_date=TODAY + timedelta(days=2),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Call Sophie Turner – negotiate extra seat request",
                contact_id=c[19],
                assigned_to=sales2_id,
                priority="high",
                due_date=TODAY,
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Get CFO sign-off from Linda Martinez",
                contact_id=c[15],
                assigned_to=sales1_id,
                priority="high",
                due_date=TODAY + timedelta(days=1),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Send comparison matrix to Olivia White (TechForward)",
                contact_id=c[17],
                assigned_to=sales3_id,
                priority="mid",
                due_date=TODAY + timedelta(days=2),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Schedule discovery call with Nancy Wright (CoreLogic)",
                contact_id=c[11],
                assigned_to=sales1_id,
                priority="mid",
                due_date=TODAY + timedelta(days=1),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Book demo for Samantha Lee (SwiftLog)",
                contact_id=c[9],
                assigned_to=sales1_id,
                priority="mid",
                due_date=TODAY + timedelta(days=5),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Send POC results summary to Henry Adams (CleanTech)",
                contact_id=c[18],
                assigned_to=sales1_id,
                priority="mid",
                due_date=TODAY + timedelta(days=3),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Add Atlas Insurance to compliance feature newsletter",
                contact_id=c[16],
                assigned_to=sales2_id,
                priority="low",
                due_date=TODAY + timedelta(days=7),
                is_done=False,
            ),
            Task(
                id=uid(),
                title="Prepare onboarding plan for Apex Cloud (Daniel Foster)",
                contact_id=c[0],
                assigned_to=sales1_id,
                priority="high",
                due_date=TODAY - timedelta(days=2),
                is_done=True,
                done_at=NOW - timedelta(days=2, hours=3),
            ),
            Task(
                id=uid(),
                title="Send welcome email to MedTech Innovations (David Park)",
                contact_id=c[14],
                assigned_to=sales3_id,
                priority="high",
                due_date=TODAY - timedelta(days=5),
                is_done=True,
                done_at=NOW - timedelta(days=5, hours=1),
            ),
            Task(
                id=uid(),
                title="Log contract details for Meridian Finance (Patricia Moore)",
                contact_id=c[1],
                assigned_to=sales2_id,
                priority="mid",
                due_date=TODAY - timedelta(days=3),
                is_done=True,
                done_at=NOW - timedelta(days=3),
            ),
            Task(
                id=uid(),
                title="Compile team monthly performance report – March",
                contact_id=None,
                assigned_to=mgr2_id,
                priority="mid",
                due_date=TODAY - timedelta(days=10),
                is_done=True,
                done_at=NOW - timedelta(days=10),
            ),
            # Overdue task (due yesterday, not done)
            Task(
                id=uid(),
                title="Follow up with Brian O'Sullivan – vendor comparison update",
                contact_id=c[6],
                assigned_to=sales1_id,
                priority="mid",
                due_date=TODAY - timedelta(days=1),
                is_done=False,
            ),
        ]
        session.add_all(tasks)

        # ------------------------------------------------------------------ #
        #  MESSAGES                                                            #
        # ------------------------------------------------------------------ #
        messages = []

        def msg(
            contact_key,
            channel,
            direction,
            sender,
            recipient,
            body,
            days_ago,
            hours=0,
            subject=None,
            is_read=True,
            assigned_to=None,
        ):
            messages.append(
                Message(
                    id=uid(),
                    contact_id=c[contact_key],
                    channel=channel,
                    direction=direction,
                    sender_id=sender,
                    recipient_id=recipient,
                    subject=subject,
                    body=body,
                    is_read=is_read,
                    assigned_to=assigned_to,
                    created_at=NOW - timedelta(days=days_ago, hours=hours),
                )
            )

        # WhatsApp thread – Chris Patel (NexGen Retail)
        msg(
            4,
            "whatsapp",
            "inbound",
            "+13105550505",
            sales2_id,
            "Hi Emma! This is Chris from NexGen. I saw your email about the WhatsApp broadcast feature. Can we schedule a quick demo this week?",
            13,
            is_read=True,
            assigned_to=sales2_id,
        )
        msg(
            4,
            "whatsapp",
            "outbound",
            sales2_id,
            "+13105550505",
            "Hi Chris! Absolutely – I'm free Tuesday at 2pm or Thursday at 10am. Which works better for you?",
            12,
            is_read=True,
            assigned_to=sales2_id,
        )
        msg(
            4,
            "whatsapp",
            "inbound",
            "+13105550505",
            sales2_id,
            "Tuesday 2pm works great. Looking forward to it!",
            12,
            hours=2,
            is_read=True,
            assigned_to=sales2_id,
        )

        # WhatsApp thread – Sophie Turner (Bloom E-commerce) – unread
        msg(
            19,
            "whatsapp",
            "inbound",
            "+15035550202",
            sales2_id,
            "Hey Emma, we're very close to a yes here. Just need to finalize the seat count internally. Can we talk Thursday at 11am?",
            4,
            is_read=False,
            assigned_to=sales2_id,
        )

        # WhatsApp thread – Laura Simmons (GreenLeaf Energy)
        msg(
            3,
            "whatsapp",
            "inbound",
            "+15125550404",
            sales3_id,
            "Marcus, just a heads up – board presentation is Thursday. I'll have a decision for you by Friday EOD.",
            8,
            is_read=True,
            assigned_to=sales3_id,
        )
        msg(
            3,
            "whatsapp",
            "outbound",
            sales3_id,
            "+15125550404",
            "Thanks Laura! Fingers crossed. Let me know if you need any additional materials before the presentation.",
            8,
            hours=1,
            is_read=True,
            assigned_to=sales3_id,
        )

        # Email thread – Kevin Nakamura (BioCore Diagnostics)
        msg(
            2,
            "email",
            "outbound",
            sales1_id,
            "k.nakamura@biocore.com",
            "Kevin, please find attached the revised MSA with the changes your legal team requested. Happy to jump on a call to walk through the redlines.",
            subject="RE: BioCore CRM Agreement – Revised MSA",
            days_ago=5,
            is_read=True,
            assigned_to=sales1_id,
        )
        msg(
            2,
            "email",
            "inbound",
            "k.nakamura@biocore.com",
            sales1_id,
            "Ryan, thanks for the quick turnaround. Legal is reviewing and we expect to have feedback by Wednesday. We're still on track to sign by end of month.",
            subject="RE: BioCore CRM Agreement – Revised MSA",
            days_ago=4,
            is_read=True,
            assigned_to=sales1_id,
        )

        # Email thread – Daniel Foster (Apex Cloud – Won) – post-close
        msg(
            0,
            "email",
            "outbound",
            sales1_id,
            "d.foster@apexcloud.io",
            "Daniel, congratulations again on the partnership! I've connected you with your dedicated Customer Success Manager, Priya. She'll reach out within 24 hours to schedule the kickoff.",
            subject="Welcome to the Team – Apex Cloud Onboarding",
            days_ago=44,
            is_read=True,
            assigned_to=sales1_id,
        )
        msg(
            0,
            "email",
            "inbound",
            "d.foster@apexcloud.io",
            sales1_id,
            "Thanks Ryan! The team is excited to get started. Priya already reached out – we're set for Monday.",
            subject="RE: Welcome to the Team – Apex Cloud Onboarding",
            days_ago=44,
            hours=3,
            is_read=True,
            assigned_to=sales1_id,
        )

        # Email thread – Nancy Wright (CoreLogic – new lead) – unread
        msg(
            11,
            "email",
            "inbound",
            "n.wright@corelogic.ai",
            sales1_id,
            "Hi Ryan, I was introduced to you by our board member John Hughes. We're currently evaluating CRM platforms for our 80-person sales team. Do you have time for a 30-minute intro call this week?",
            subject="CRM Evaluation – Introduction",
            days_ago=0,
            hours=2,
            is_read=False,
            assigned_to=sales1_id,
        )

        # Email thread – Olivia White (TechForward – new lead) – unread
        msg(
            17,
            "email",
            "inbound",
            "o.white@techforward.hr",
            sales3_id,
            "Hi Marcus, I found your CRM on G2 with great reviews. We're evaluating 3 platforms and would love to see a demo. Could you send me a comparison vs HubSpot and Salesforce first?",
            subject="Demo Request – TechForward Staffing",
            days_ago=1,
            is_read=False,
            assigned_to=sales3_id,
        )

        # Email – Amanda Torres (Vertex Manufacturing)
        msg(
            5,
            "email",
            "outbound",
            sales3_id,
            "a.torres@vertexmfg.com",
            "Hi Amanda, as discussed, I'm attaching a comparison of how our CRM handles after-sales service tracking vs your current workflow. Would love to get your thoughts.",
            subject="Vertex Manufacturing – After-Sales CRM Comparison",
            days_ago=15,
            is_read=True,
            assigned_to=sales3_id,
        )
        msg(
            5,
            "email",
            "inbound",
            "a.torres@vertexmfg.com",
            sales3_id,
            "Marcus, this is really helpful! I'll share with the ops team. Can we do a follow-up call next week?",
            subject="RE: Vertex Manufacturing – After-Sales CRM Comparison",
            days_ago=14,
            is_read=True,
            assigned_to=sales3_id,
        )

        session.add_all(messages)

        await session.commit()

        print("\n✓ Seed data created successfully!")
        print("\n  LOGIN CREDENTIALS")
        print("  ─────────────────────────────────────────")
        print("  Role     │ Email                │ Password")
        print("  ─────────────────────────────────────────")
        print("  Admin    │ admin@crm.com         │ Admin123")
        print("  Manager  │ sarah@crm.com         │ Manager123")
        print("  Manager  │ james@crm.com         │ Manager123")
        print("  Sales    │ ryan@crm.com          │ Sales123")
        print("  Sales    │ emma@crm.com          │ Sales123")
        print("  Sales    │ marcus@crm.com        │ Sales123")
        print("  ─────────────────────────────────────────")
        print("\n  DATA SUMMARY")
        print("  Users:          6  (1 admin, 2 managers, 3 sales)")
        print("  Routing Rules:  3  (workload / region / win_rate)")
        print(f"  Sales Targets: {len(targets)}  (current + previous month × 5 users)")
        print("  Contacts:      20  (all statuses, industries, priorities)")
        print(f"  Activities:    {len(activities)}  (full timeline histories)")
        print(f"  Tasks:         {len(tasks)}  (open, done, overdue)")
        print(f"  Messages:      {len(messages)}  (WhatsApp + email, read + unread)")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
