"""CRM-TASK-049 tests for the manager dashboard insight evaluation samples.

The samples are only seeded and turned into team snapshots; no model is called.
"""

import json

import pytest
from evals.__main__ import SAMPLES_DIR
from evals.features import FEATURES, check_sample
from evals.seed import seed_sample

from app.models import User
from app.services.dashboard_insight_service import build_team_snapshot

FEATURE = "dashboard_insight"
INJECTION_MARK = "EVAL-INJECTED"
NO_TARGET_SAMPLE = "dash-04"
MANAGER_ONLY_SAMPLE = "dash-07"


def _samples(feature):
    return json.loads((SAMPLES_DIR / f"{feature}.json").read_text(encoding="utf-8"))


SAMPLES = _samples(FEATURE)


def _sample_id(sample):
    return sample["id"]


async def _snapshot(sample):
    seeded = await seed_sample(sample, FEATURE)
    try:
        async with seeded.session_maker() as session:
            manager = await session.get(User, seeded.acting_user_id)
            return await build_team_snapshot(session, manager=manager, now=seeded.now)
    finally:
        await seeded.dispose()


def test_eight_samples_with_unique_ids_covers_and_now():
    """Guards criterion 1 (8 samples, ids unique repo-wide, covers and a fixed now), test case 1."""
    assert len(SAMPLES) == 8
    for sample in SAMPLES:
        assert isinstance(sample.get("covers"), str) and sample["covers"].strip(), sample["id"]
        assert isinstance(sample.get("now"), str) and sample["now"].endswith("Z"), sample["id"]
    ids = [
        sample["id"]
        for path in sorted(SAMPLES_DIR.glob("*.json"))
        for sample in _samples(path.stem)
    ]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_every_sample_seeds_into_sqlite(sample):
    """Guards criterion 1 (samples seed with the CRM-TASK-045 seeding), test case 2."""
    await check_sample(FEATURES[FEATURE], sample)


async def test_every_sample_builds_a_team_snapshot():
    """Guards criterion 2 (no target, manager-only team, a day-3 now), test case 3."""
    snapshots = {sample["id"]: await _snapshot(sample) for sample in SAMPLES}
    team = snapshots[NO_TARGET_SAMPLE]["team"]
    assert team["this_month"]["target_rate"] is None
    assert team["last_month"]["target_rate"] is None
    assert len(snapshots[MANAGER_ONLY_SAMPLE]["members"]) == 1
    assert any(snapshot["period"]["day_of_month"] == 3 for snapshot in snapshots.values())


@pytest.mark.parametrize("sample", SAMPLES, ids=_sample_id)
async def test_no_sample_is_judged_as_no_data(sample):
    """Guards criterion 2 (no sample is judged as having no data), test case 4."""
    team = (await _snapshot(sample))["team"]
    assert any(
        team[period]["won_count"] > 0 or team[period]["created_count"] > 0
        for period in ("this_month", "last_month")
    ) or any(values["count"] > 0 for values in team["open_deals"].values())


def test_exactly_one_injection_sample():
    """Guards criterion 2 (one injection sample in a member name, must_not_contain), test case 5."""
    injected = [sample for sample in SAMPLES if sample.get("injection", False) is True]
    assert len(injected) == 1
    assert injected[0].get("must_not_contain", []) == [INJECTION_MARK]
    names = [user["name"] for user in injected[0]["users"] if INJECTION_MARK in user["name"]]
    assert names and all(len(name) <= 100 for name in names)
