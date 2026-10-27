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
SAMPLE_COUNTS = {"conversation_brief": 10, "reply_draft": 16, "translate": 10}
INJECTION_COUNTS = {"conversation_brief": 1, "reply_draft": 2, "translate": 1}
DOCUMENT_SAMPLE_IDS = [f"reply-{number}" for number in range(11, 17)]


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


def _cases(features):
    return [
        pytest.param(feature, sample, id=sample["id"])
        for feature in features
        for sample in _samples(feature)
    ]


def test_each_file_has_its_sample_count_with_unique_ids_and_covers():
    """Guards criterion 1 (10 brief and translate samples, unique ids, covers), test case 1,
    and CRM-TASK-080 criterion 1 (reply draft has 16 samples, reply-11 to reply-16 added)."""
    for feature in INBOX_FEATURES:
        samples = _samples(feature)
        assert len(samples) == SAMPLE_COUNTS[feature], feature
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


def test_reply_draft_channels_are_eight_whatsapp_and_eight_email():
    """Guards criterion 3 (WhatsApp and email reply drafts), test case 4, and CRM-TASK-080
    criterion 1 (3 WhatsApp and 3 email among the new six, so 8 of each)."""
    channels = Counter(sample["params"]["channel"] for sample in _samples("reply_draft"))
    assert channels == {"whatsapp": 8, "email": 8}


@pytest.mark.parametrize("feature", INBOX_FEATURES)
def test_injection_samples_per_file(feature):
    """Guards criteria 2, 3 and 4 (injection samples, must_not_contain), test case 5, and
    CRM-TASK-080 criterion 3 (reply draft has 2 injection samples, the others 1)."""
    injected = [sample for sample in _samples(feature) if sample.get("injection", False) is True]
    assert len(injected) == INJECTION_COUNTS[feature]
    expected = [] if feature == "translate" else [INJECTION_MARK]
    for sample in injected:
        assert sample.get("must_not_contain", []) == expected, sample["id"]


def test_new_reply_draft_samples_carry_autocount_documents():
    """Guards CRM-TASK-080 criterion 2 (each of reply-11 to reply-16 has a document for its
    contact; a quotation with line_items and an invoice with outstanding_amount among them)."""
    samples = {sample["id"]: sample for sample in _samples("reply_draft")}
    documents = []
    for sample_id in DOCUMENT_SAMPLE_IDS:
        sample = samples[sample_id]
        contact = sample["params"]["contact"]
        own = [doc for doc in sample.get("documents", []) if doc.get("contact") == contact]
        assert own, sample_id
        documents.extend(own)
    assert any(doc["doc_type"] == "quotation" and doc.get("line_items") for doc in documents)
    assert any(
        doc["doc_type"] == "invoice" and doc.get("outstanding_amount") is not None
        for doc in documents
    )
