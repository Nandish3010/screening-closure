"""M7-1 (after the M7-3 review): the fairness watch covers the "other" language group and contact quality, and flags a
group only when it is served worse than the REST of the cases by more than the gap and beyond sampling noise."""

from types import SimpleNamespace

from app.agency import agents
from app.agency.facts import contact_quality
from app.state_machine import CaseModel

WATCH = agents.FairnessWatch()


class Box:
    def __init__(self, cases, abstention=None):
        self.cases, self.ab = cases, abstention or {"rate": None}

    def call(self, name, **_):
        return self.ab if name == "read_abstention" else self.cases


def case(lang="en", overdue=False, contact="ok"):
    return SimpleNamespace(open=True, overdue=overdue, language=lang, contact_quality=contact)


def cohort(groups):
    """groups: [(n, overdue_n, lang, contact)]"""
    out = []
    for n, k, lang, contact in groups:
        out += [case(lang, i < k, contact) for i in range(n)]
    return out


def kinds(outcome):
    return {(f["kind"], f["group"]) for f in (outcome.output.flags or [])}


def test_the_other_language_group_is_watched():
    cases = cohort([(40, 4, "en", "ok"), (40, 4, "ar", "ok"), (30, 15, None, "ok")])
    assert ("overdue_language", "other") in kinds(WATCH.run(Box(cases), None, None))


def test_contact_quality_is_watched():
    cases = cohort([(60, 6, "en", "ok"), (25, 14, "en", "wrong"), (10, 1, "ar", "ok")])
    flagged = kinds(WATCH.run(Box(cases), None, None))
    assert ("overdue_contact", "wrong") in flagged and ("overdue_contact", "ok") not in flagged


def test_a_large_group_is_compared_with_the_rest_not_with_an_average_it_dominates():
    # 90 Arabic cases at 40% overdue against 10 English at 0%: overall is 36%, so "4 points above overall" would hide it
    cases = cohort([(90, 36, "ar", "ok"), (10, 0, "en", "ok")])
    assert ("overdue_language", "ar") in kinds(WATCH.run(Box(cases), None, None))


def test_a_gap_inside_sampling_noise_is_not_flagged():
    # 5 cases, 2 overdue (40%) against 100 at 20%: 20 points above, but the Wilson interval of 2/5 starts near 12%
    cases = cohort([(100, 20, "en", "ok"), (5, 2, None, "ok")])
    assert not kinds(WATCH.run(Box(cases), None, None))


def test_no_flag_when_groups_are_served_alike():
    cases = cohort([(50, 10, "en", "ok"), (50, 11, "ar", "ok"), (30, 6, None, "ok"), (20, 4, "en", "wrong")])
    assert WATCH.run(Box(cases), None, None).status == "done"


def test_contact_quality_from_the_read_model():
    m = CaseModel(state="patient_contacted")
    assert contact_quality(m, ["sms"], []) == "ok"
    assert contact_quality(CaseModel(state="patient_contacted", outreach_holds=("wrong_number",)), ["sms"], []) == "wrong"
    assert contact_quality(m, ["sms"], [{"intent": "wrong_number", "needs_human": True}]) == "wrong"
    assert contact_quality(m, [], []) == "missing"
