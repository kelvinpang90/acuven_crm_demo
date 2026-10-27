"""CRM-TASK-070 tests for the message triage evaluation samples.

The samples are only seeded, turned into snapshots and call arguments; no model is called.
"""

import json

import pytest
from evals import FEATURE_NAMES
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample, text_values
from evals.seed import seed_sample
from sqlalchemy import select

from app.models import Contact, Message, User
from app.services.message_triage_service import build_triage_snapshot

FEATURE = "message_triage"

SAMPLES = json.loads((SAMPLES_DIR / f"{FEATURE}.json").read_text(encoding="utf-8"))


def _sample_id(sample):
    return sample["id"]


def test_twelve_samples_with_unique_ids_and_covers():
    """Guards criterion 2 (12 samples, covers says what each covers), test case 1."""
    assert len(SAMPLES) == 12
    ids = [sample["id"] for sample in SAMPLES]
    assert len(ids) == len(set(ids))
    for sample in SAMPLES:
        assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_every_sample_seeds_with_latest_inbound_message_and_builds_snapshot(sample):
    """Guards criteria 1 and 2 (params.message resolves to the contact's latest inbound
    message, so it is not superseded; the snapshot builds), test case 2."""
    await check_sample(FEATURES[FEATURE], sample)

    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            contact_id = seeded.params["contact"]
            message_id = seeded.params["message"]
            assert contact_id == seeded.ids[sample["params"]["contact"]]
            assert message_id == seeded.ids[sample["params"]["message"]]

            latest_inbound = (
                await session.execute(
                    select(Message.id)
                    .where(Message.contact_id == contact_id, Message.direction == "inbound")
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            assert latest_inbound == message_id

            contact = await session.get(Contact, contact_id)
            snapshot = await build_triage_snapshot(session, contact=contact, now=seeded.now)
    finally:
        await seeded.dispose()

    assert snapshot["messages"]


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample, must_not_contain left empty), test case 3."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == []


def test_feature_is_registered_right_after_import_columns():
    """Guards criterion 1 (message_triage after import_columns, labels reported, no text
    scanned), test case 4."""
    index = FEATURE_NAMES.index(FEATURE)
    assert index > 0 and FEATURE_NAMES[index - 1] == "import_columns"
    feature = FEATURES[FEATURE]
    assert feature.module_name == "app.services.message_triage_service"
    assert feature.function_name == "classify_conversation"
    assert feature.output_fields == ("intent", "urgency")
    assert feature.text_fields == ()
    assert text_values(feature, {"intent": "complaint", "urgency": "urgent"}) == []


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_adapter_passes_contact_and_message_ids_without_user(sample):
    """Guards criterion 1 (contact id and message id from params, no user), test case 4."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            arguments = FEATURES[FEATURE].arguments(user, seeded.params)
    finally:
        await seeded.dispose()

    assert arguments == {
        "contact_id": seeded.ids[sample["params"]["contact"]],
        "message_id": seeded.ids[sample["params"]["message"]],
    }
