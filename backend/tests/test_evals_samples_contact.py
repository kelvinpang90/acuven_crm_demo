"""CRM-TASK-047 tests for the contact summary and follow-up evaluation samples.

The samples are only seeded and turned into snapshots; no model is called.
"""

import json

import pytest
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample
from evals.seed import seed_sample

from app.models import User
from app.services.contact_summary_service import build_contact_snapshot

CONTACT_FEATURES = ("contact_summary", "followup_draft")
INJECTION_MARK = "EVAL-INJECTED"
# The sample whose contact has a deal under another salesperson, and that deal's key.
OTHER_OWNER_SAMPLE = "summary-05"
OTHER_OWNER_DEAL = "d2"
TRUNCATED_SAMPLE = "summary-08"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


def _cases(features):
    return [
        pytest.param(feature, sample, id=sample["id"])
        for feature in features
        for sample in _samples(feature)
    ]


def _keys(sample, section):
    return {item["key"]: item for item in sample.get(section, [])}


def test_each_file_has_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 1 (two files, 10 samples each, ids unique repo-wide, covers), test case 1."""
    for feature in CONTACT_FEATURES:
        samples = _samples(feature)
        assert len(samples) == 10, feature
        for sample in samples:
            assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]
    ids = [
        sample["id"]
        for path in sorted(SAMPLES_DIR.glob("*.json"))
        for sample in _samples(path.stem)
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(("feature", "sample"), _cases(CONTACT_FEATURES))
async def test_every_sample_seeds_into_sqlite(feature, sample):
    """Guards criteria 1 and 5 (samples seed with the CRM-TASK-045 seeding), test case 2."""
    await check_sample(FEATURES[feature], sample)


@pytest.mark.parametrize("sample", _samples("contact_summary"), ids=lambda sample: sample["id"])
async def test_summary_samples_build_a_contact_snapshot(sample):
    """Guards criteria 2 and 5 (snapshot builds; other owner's deal left out; truncation), test case 3."""
    seeded = await seed_sample(sample, "contact_summary")
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            snapshot = await build_contact_snapshot(
                session, user=user, contact_id=seeded.params["contact"]
            )
    finally:
        await seeded.dispose()

    assert any(
        snapshot[section]
        for section in ("deals", "open_tasks", "documents", "activities", "messages")
    )
    titles = {deal["title"] for deal in snapshot["deals"]}
    if sample["id"] == OTHER_OWNER_SAMPLE:
        hidden = _keys(sample, "deals")[OTHER_OWNER_DEAL]
        assert hidden["assigned_to"] != sample["acting_user"]
        assert hidden["title"] not in titles
        assert titles
    if sample["id"] == TRUNCATED_SAMPLE:
        assert snapshot["truncated"] is True


@pytest.mark.parametrize("sample", _samples("followup_draft"), ids=lambda sample: sample["id"])
def test_followup_params_have_a_note_and_existing_contact_and_deal(sample):
    """Guards criteria 3 and 5 (every follow-up has a note and real references), test case 4."""
    params = sample["params"]
    assert isinstance(params.get("note"), str) and params["note"].strip()
    contacts = _keys(sample, "contacts")
    deals = _keys(sample, "deals")
    assert params["contact"] in contacts
    assert params["deal"] in deals
    assert deals[params["deal"]]["contact"] == params["contact"]


@pytest.mark.parametrize("feature", CONTACT_FEATURES)
def test_exactly_one_injection_sample_per_file(feature):
    """Guards criteria 2 and 3 (one injection sample each, must_not_contain), test case 5."""
    injected = [sample for sample in _samples(feature) if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == [INJECTION_MARK]
