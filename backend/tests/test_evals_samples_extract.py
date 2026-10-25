"""CRM-TASK-054 tests for the contact extract evaluation samples.

The samples are only seeded and turned into snapshots; no model is called.
"""

import json

import pytest
from evals import FEATURE_NAMES
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, text_values
from evals.seed import seed_sample

from app.models import Contact, User
from app.services.contact_extract_service import build_extract_snapshot

FEATURE = "contact_extract"
INJECTION_MARK = "EVAL-INJECTED"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


SAMPLES = _samples(FEATURE)


def _sample_id(sample):
    return sample["id"]


def test_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 2 (10 samples, ids unique repo-wide, covers), test case 1."""
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
async def test_every_sample_builds_an_extract_snapshot(sample):
    """Guards criterion 2 (samples seed; something to fill; no nothing_to_fill sample), test case 2."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            contact = await session.get(Contact, seeded.params["contact"])
            snapshot = await build_extract_snapshot(session, contact=contact, now=seeded.now)
    finally:
        await seeded.dispose()

    assert snapshot["contact"]["empty_fields"] or snapshot["contact"]["name_is_phone"] is True
    assert any(message["direction"] == "inbound" for message in snapshot["messages"])


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample in an inbound message, must_not_contain), test case 3."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == [INJECTION_MARK]
    assert any(
        INJECTION_MARK in message["body"] and message.get("direction", "inbound") == "inbound"
        for message in injected[0]["messages"]
    )


def test_feature_is_registered_between_deal_and_dashboard():
    """Guards criterion 1 (contact_extract after deal_next_step, others unchanged), test case 4."""
    index = FEATURE_NAMES.index(FEATURE)
    assert index > 0 and FEATURE_NAMES[index - 1] == "deal_next_step"
    feature = FEATURES[FEATURE]
    assert feature.module_name == "app.services.contact_extract_service"
    assert feature.function_name == "generate_contact_extract"
    assert feature.output_fields == ("suggestions", "truncated")
    outputs = {
        "suggestions": [
            {"field": "company", "value": "Acme", "evidence": "from Acme", "message_index": 0},
            {"field": "email", "value": "a@example.com", "evidence": "a@example.com pls"},
        ],
        "truncated": False,
    }
    assert sorted(text_values(feature, outputs)) == sorted(
        ["Acme", "from Acme", "a@example.com", "a@example.com pls"]
    )


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_adapter_builds_arguments_from_the_sample(sample):
    """Guards criterion 1 (user object, contact id and output_language), test case 4."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            arguments = FEATURES[FEATURE].arguments(user, seeded.params)
    finally:
        await seeded.dispose()

    assert arguments == {
        "user": user,
        "contact_id": seeded.ids[sample["params"]["contact"]],
        "output_language": sample["params"]["output_language"],
    }
    assert arguments["user"].id == seeded.acting_user_id
