"""CRM-TASK-046 tests for the inbox evaluation samples in backend/evals/samples.

The samples are only seeded and turned into snapshots; no model is called.
"""

import json
from collections import Counter

import pytest
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample
from evals.seed import seed_sample

from app.services.ai_assist_service import build_conversation_snapshot

INBOX_FEATURES = ("conversation_brief", "reply_draft", "translate")
SNAPSHOT_FEATURES = ("conversation_brief", "reply_draft")
INJECTION_MARK = "EVAL-INJECTED"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


def _cases(features):
    return [
        pytest.param(feature, sample, id=sample["id"])
        for feature in features
        for sample in _samples(feature)
    ]


def test_each_file_has_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 1 (three files, 10 samples each, unique ids, covers), test case 1."""
    for feature in INBOX_FEATURES:
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


@pytest.mark.parametrize(("feature", "sample"), _cases(INBOX_FEATURES))
async def test_every_sample_seeds_into_sqlite(feature, sample):
    """Guards criterion 6 (samples seed with the CRM-TASK-045 seeding), test case 2."""
    await check_sample(FEATURES[feature], sample)


@pytest.mark.parametrize(("feature", "sample"), _cases(SNAPSHOT_FEATURES))
async def test_brief_and_reply_samples_build_a_conversation_snapshot(feature, sample):
    """Guards criterion 6 (snapshot builds without error for brief and reply), test case 3."""
    seeded = await seed_sample(sample, feature)
    try:
        async with seeded.session_maker() as session:
            snapshot = await build_conversation_snapshot(
                session, seeded.params["contact"], user_id=seeded.acting_user_id
            )
        assert snapshot["messages"]
    finally:
        await seeded.dispose()


def test_reply_draft_channels_are_five_whatsapp_and_five_email():
    """Guards criterion 3 (5 WhatsApp and 5 email reply drafts), test case 4."""
    channels = Counter(sample["params"]["channel"] for sample in _samples("reply_draft"))
    assert channels == {"whatsapp": 5, "email": 5}


@pytest.mark.parametrize("feature", INBOX_FEATURES)
def test_exactly_one_injection_sample_per_file(feature):
    """Guards criteria 2, 3 and 4 (one injection sample each, must_not_contain), test case 5."""
    injected = [sample for sample in _samples(feature) if sample.get("injection", False) is True]
    assert len(injected) == 1
    expected = [] if feature == "translate" else [INJECTION_MARK]
    assert injected[0].get("must_not_contain", []) == expected
