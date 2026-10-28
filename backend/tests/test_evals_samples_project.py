"""CRM-TASK-085 tests for the roofing project report evaluation samples.

The samples are only seeded and turned into call arguments; no model is called.
"""

import json
from collections import Counter

import pytest
from evals import FEATURE_NAMES
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample
from evals.seed import seed_sample
from sqlalchemy import select

from app.models import Project, ProjectStepHistory, User

FEATURE = "project_report"
MIN_STEP = {"inspection": 3, "completion": 10}

SAMPLES = json.loads((SAMPLES_DIR / f"{FEATURE}.json").read_text(encoding="utf-8"))


def _sample_id(sample):
    return sample["id"]


def test_ten_samples_with_unique_ids_and_covers():
    """Guards criterion 3 (10 samples, covers says what each covers), test case 1."""
    assert len(SAMPLES) == 10
    ids = [sample["id"] for sample in SAMPLES]
    assert len(ids) == len(set(ids))
    for sample in SAMPLES:
        assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_every_sample_seeds_with_project_reaching_report_step(sample):
    """Guards criteria 2 and 3 (projects and project_steps seed, params.project resolves to
    an existing project, the report type's step is reached), test case 2."""
    await check_sample(FEATURES[FEATURE], sample)

    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            project_id = seeded.params["project"]
            assert project_id == seeded.ids[sample["params"]["project"]]
            project = await session.get(Project, project_id)
            assert project is not None
            steps = (
                (
                    await session.execute(
                        select(ProjectStepHistory).where(
                            ProjectStepHistory.project_id == project_id
                        )
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await seeded.dispose()

    assert project.current_step >= MIN_STEP[sample["params"]["report_type"]]
    assert steps


def test_exactly_one_injection_sample():
    """Guards criterion 3 (one injection sample, must_not_contain is EVAL-INJECTED),
    test case 3."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain") == ["EVAL-INJECTED"]


def test_samples_cover_repeated_step_and_step_beyond_current():
    """Guards criterion 3 (a step with two records, a record beyond current_step),
    test case 4."""
    repeated = 0
    beyond = 0
    for sample in SAMPLES:
        current = {project["key"]: project["current_step"] for project in sample["projects"]}
        counts = Counter((step["project"], step["step_no"]) for step in sample["project_steps"])
        if any(count > 1 for count in counts.values()):
            repeated += 1
        if any(step["step_no"] > current[step["project"]] for step in sample["project_steps"]):
            beyond += 1
    assert repeated >= 1
    assert beyond >= 1


def test_feature_is_registered_right_after_message_triage():
    """Guards criterion 1 (project_report after message_triage, report fields scanned),
    test case 5."""
    index = FEATURE_NAMES.index(FEATURE)
    assert index > 0 and FEATURE_NAMES[index - 1] == "message_triage"
    feature = FEATURES[FEATURE]
    assert feature.module_name == "app.services.project_report_service"
    assert feature.function_name == "generate_project_report"
    assert feature.output_fields == ("title", "body", "note_for_agent")
    assert set(feature.text_fields) == {"title", "body", "note_for_agent"}


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_adapter_passes_user_project_and_report_params(sample):
    """Guards criterion 1 (user, project id, report_type, notes or null, offset 0 and
    output_language), test case 5."""
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            user = await session.get(User, seeded.acting_user_id)
            arguments = FEATURES[FEATURE].arguments(user, seeded.params)
    finally:
        await seeded.dispose()

    params = sample["params"]
    assert arguments == {
        "user": user,
        "project_id": seeded.ids[params["project"]],
        "report_type": params["report_type"],
        "notes": params.get("notes"),
        "utc_offset_minutes": 0,
        "output_language": params["output_language"],
    }
    if "notes" not in params:
        assert arguments["notes"] is None
