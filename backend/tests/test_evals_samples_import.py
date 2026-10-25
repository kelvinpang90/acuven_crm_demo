"""CRM-TASK-059 tests for the import column recognition evaluation samples.

The samples are only seeded and turned into call arguments; no model is called.
"""

import json

import pytest
from evals import FEATURE_NAMES
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, text_values
from evals.seed import seed_sample

from app.models import User
from app.services.import_columns_service import (
    MAX_COLUMNS,
    MAX_SAMPLE_LENGTH,
    MAX_SAMPLES,
    strip_blanks,
)

FEATURE = "import_columns"

SAMPLES = json.loads((SAMPLES_DIR / f"{FEATURE}.json").read_text(encoding="utf-8"))


def _sample_id(sample):
    return sample["id"]


def test_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 2 (10 samples, covers says what each covers), test case 1."""
    assert len(SAMPLES) == 10
    ids = [sample["id"] for sample in SAMPLES]
    assert len(ids) == len(set(ids))
    for sample in SAMPLES:
        assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_every_sample_builds_acting_user_within_column_limits(sample):
    """Guards criteria 1 and 3 (acting_user only; columns within the business limits), test case 2."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
    finally:
        await seeded.dispose()
    assert user is not None

    columns = sample["params"]["columns"]
    assert 1 <= len(columns) <= MAX_COLUMNS == 40
    headers = [strip_blanks(column["header"]) for column in columns]
    assert all(headers)
    assert len(headers) == len(set(headers))
    for column in columns:
        assert len(column["samples"]) <= MAX_SAMPLES == 3
        assert all(len(value) <= MAX_SAMPLE_LENGTH == 50 for value in column["samples"])


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample, must_not_contain left empty), test case 3."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == []


def test_feature_is_registered_right_after_contact_extract():
    """Guards criterion 1 (import_columns after contact_extract, no text scanned), test case 4."""
    index = FEATURE_NAMES.index(FEATURE)
    assert index > 0 and FEATURE_NAMES[index - 1] == "contact_extract"
    feature = FEATURES[FEATURE]
    assert feature.module_name == "app.services.import_columns_service"
    assert feature.function_name == "suggest_import_columns"
    assert feature.output_fields == ("mappings",)
    assert feature.text_fields == ()
    outputs = {"mappings": [{"header": "Tel", "field": "phone"}, {"header": "No.", "field": None}]}
    assert text_values(feature, outputs) == []


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_adapter_builds_arguments_from_the_sample(sample):
    """Guards criterion 1 (user object, columns and output_language), test case 4."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            arguments = FEATURES[FEATURE].arguments(user, seeded.params)
    finally:
        await seeded.dispose()

    assert arguments == {
        "user": user,
        "columns": sample["params"]["columns"],
        "output_language": sample["params"]["output_language"],
    }
    assert arguments["user"].id == seeded.acting_user_id
