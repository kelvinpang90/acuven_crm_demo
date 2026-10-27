"""CRM-TASK-078 tests for the follow-up from chat evaluation samples.

The samples are only seeded and the adapter's arguments built; no business
function and no model is called.
"""

import json
from datetime import datetime, timezone

import pytest
from evals import FEATURE_NAMES
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, text_values
from evals.seed import seed_sample
from sqlalchemy import func, select

from app.models import Activity, Deal, Message, User

FEATURE = "followup_from_chat"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


SAMPLES = _samples(FEATURE)


def _sample_id(sample):
    return sample["id"]


def _utc_today():
    return datetime.now(timezone.utc).date().isoformat()


def test_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 2 (10 samples, ids unique, each has covers), test case 1."""
    assert len(SAMPLES) == 10
    for sample in SAMPLES:
        assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]
    ids = [
        sample["id"]
        for path in sorted(SAMPLES_DIR.glob("*.json"))
        for sample in _samples(path.stem)
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_every_sample_seeds_with_messages_after_the_start(sample):
    """Guards criterion 2 (samples seed; the deal belongs to the contact; at least one
    message after the last follow-up), test case 2."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            contact_id = seeded.params["contact"]
            deal = await session.get(Deal, seeded.params["deal"])
            since = (
                await session.execute(
                    select(func.max(Activity.created_at)).where(
                        Activity.contact_id == contact_id, Activity.type != "status change"
                    )
                )
            ).scalar_one()
            query = (
                select(func.count()).select_from(Message).where(Message.contact_id == contact_id)
            )
            if since is not None:
                query = query.where(Message.created_at > since)
            after = (await session.execute(query)).scalar_one()
    finally:
        await seeded.dispose()

    assert deal is not None and deal.contact_id == contact_id
    assert after >= 1


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample, must_not_contain left empty), test case 3."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == []


def test_feature_is_registered_right_after_followup_draft():
    """Guards criterion 1 (followup_from_chat after followup_draft, report fields, scanned
    fields), test case 4."""
    index = FEATURE_NAMES.index(FEATURE)
    assert index > 0 and FEATURE_NAMES[index - 1] == "followup_draft"
    feature = FEATURES[FEATURE]
    assert feature.module_name == "app.services.followup_draft_service"
    assert feature.function_name == "generate_followup_from_chat"
    assert feature.output_fields == ("activity_type", "content", "tasks", "since", "truncated")
    outputs = {
        "activity_type": "WhatsApp",
        "content": "Customer asked for a quote",
        "tasks": [{"title": "Send the quote", "due_date": None, "priority": "high"}],
        "since": None,
        "truncated": False,
    }
    assert sorted(text_values(feature, outputs)) == sorted(
        ["Customer asked for a quote", "Send the quote"]
    )


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_adapter_builds_arguments_from_the_sample(sample):
    """Guards criterion 1 (user, contact and deal ids, UTC today, offset 0, output_language),
    test case 4."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            before = _utc_today()
            arguments = FEATURES[FEATURE].arguments(user, seeded.params)
            after = _utc_today()
    finally:
        await seeded.dispose()

    assert arguments["today"] in {before, after}
    assert {name: value for name, value in arguments.items() if name != "today"} == {
        "user": user,
        "contact_id": seeded.ids[sample["params"]["contact"]],
        "deal_id": seeded.ids[sample["params"]["deal"]],
        "utc_offset_minutes": 0,
        "output_language": sample["params"]["output_language"],
    }
    assert arguments["user"].id == seeded.acting_user_id
