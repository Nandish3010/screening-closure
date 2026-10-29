"""M7-1: `state_machine.replace` (a fast copy of the frozen read model) is `dataclasses.replace` for these classes."""

import dataclasses
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app import state_machine as sm


def test_the_fast_copy_is_used_and_equals_dataclasses_replace():
    assert sm.replace is not dataclasses.replace
    base = sm.CaseModel(state="acknowledged", owner="primary_care_owner:a", milestones={"m": sm.MilestoneState()})
    changes = {"state": "owner_accepted", "overdue": True, "escalation_level": 2, "clock_started_event_id": uuid4(),
               "clock_started_at": datetime(2026, 1, 5, tzinfo=UTC)}
    fast, slow = sm.replace(base, **changes), dataclasses.replace(base, **changes)
    assert fast == slow and type(fast) is sm.CaseModel
    assert base.state == "acknowledged"  # the original is untouched
    ms = sm.MilestoneState(overdue=True)
    assert sm.replace(ms, escalation_level=1) == dataclasses.replace(ms, escalation_level=1)


def test_the_fast_copy_is_still_frozen_and_refuses_unknown_fields():
    m = sm.replace(sm.CaseModel(state="acknowledged"), overdue=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.state = "x"  # type: ignore[misc]
    with pytest.raises(TypeError):
        sm.replace(m, not_a_field=1)
