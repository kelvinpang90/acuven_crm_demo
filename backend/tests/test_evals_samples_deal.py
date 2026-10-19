"""CRM-TASK-048 tests for the deal next-step evaluation samples.

The samples are only seeded and turned into snapshots; no model is called.
"""

import json

import pytest
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample
from evals.seed import seed_sample

from app.models import Contact, Deal, User
from app.services.deal_next_step_service import build_deal_snapshot

FEATURE = "deal_next_step"
INJECTION_MARK = "EVAL-INJECTED"
OPEN_STATUSES = {"lead", "following", "negotiating"}
TRUNCATED_SAMPLE = "deal-06"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


SAMPLES = _samples(FEATURE)


def _sample_id(sample):
    return sample["id"]


def _keys(sample, section):
    return {item["key"]: item for item in sample.get(section, [])}


async def _snapshot(sample):
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            deal = await session.get(Deal, seeded.params["deal"])
            contact = await session.get(Contact, deal.contact_id)
            return await build_deal_snapshot(
                session, user=user, deal=deal, contact=contact, now=seeded.now
            )
    finally:
        await seeded.dispose()


def test_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 1 (10 samples, ids unique repo-wide, covers), test case 1."""
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
async def test_every_sample_seeds_into_sqlite(sample):
    """Guards criteria 1 and 2 (samples seed with the CRM-TASK-045 seeding), test case 2."""
    await check_sample(FEATURES[FEATURE], sample)


async def test_every_sample_builds_a_deal_snapshot():
    """Guards criterion 2 (at least 3 stalled, 3 not stalled; truncation sample), test case 3."""
    stalled = {}
    for sample in SAMPLES:
        snapshot = await _snapshot(sample)
        stalled[sample["id"]] = snapshot["deal"]["stalled"]
        if sample["id"] == TRUNCATED_SAMPLE:
            assert snapshot["truncated"] is True
    assert sum(value is False for value in stalled.values()) >= 3, stalled
    assert sum(value is True for value in stalled.values()) >= 3, stalled


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
def test_deal_is_open_and_has_history(sample):
    """Guards criterion 2 (open status; a non-status-change follow-up or a message), test case 4."""
    deal_key = sample["params"]["deal"]
    deal = _keys(sample, "deals")[deal_key]
    assert deal.get("status", "lead") in OPEN_STATUSES
    has_activity = any(
        activity["deal"] == deal_key and activity.get("type", "phone") != "status change"
        for activity in sample.get("activities", [])
    )
    has_message = any(
        message["contact"] == deal["contact"] for message in sample.get("messages", [])
    )
    assert has_activity or has_message


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample in a follow-up, must_not_contain), test case 5."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == [INJECTION_MARK]
    assert any(
        INJECTION_MARK in (activity.get("content") or "")
        for activity in injected[0].get("activities", [])
    )
