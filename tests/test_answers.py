import pytest

from jobradar.answers import auto_answer, fmt, load_profile, pick_option
from jobradar.workspace import JobRadarError


def q(label, type="select", options=("Yes", "No"), required=True, key="question_9"):
    return {"key": key, "label": label, "type": type, "required": required, "options": list(options)}


def test_pick_option_respects_preference_order():
    # "Other" contains no candidate earlier in the list, so the first real match wins
    opts = ["Other", "LinkedIn (Job Posting)", "Company Website"]
    assert pick_option(opts, ["LinkedIn (Job Posting)", "LinkedIn", "Other"]) == "LinkedIn (Job Posting)"
    assert pick_option(opts, ["Careers Page", "Other"]) == "Other"
    assert pick_option(["U.S. Citizen", "Permanent Resident"], ["u.s. citizen"]) == "U.S. Citizen"
    assert pick_option(["Yes", "No"], ["Maybe"]) is None


@pytest.fixture
def prof(ws):
    return load_profile(ws)


@pytest.mark.parametrize("label, expected", [
    ("Will you now or in the future require sponsorship for employment visa status?", "No"),
    ("Are you legally authorized to work in the United States?", "Yes"),
    ("Are you legally authorized to work in Canada?", "No"),
    ("Are you able to work 3 days a week in our office?", "Yes"),
])
def test_rules_answer_common_questions(prof, label, expected):
    assert auto_answer(q(label), prof) == (expected, "profile")


def test_profile_fields_fill_by_key(prof):
    assert auto_answer(q("First Name", "text", (), key="first_name"), prof) == ("Alex", "profile")


def test_eeo_questions_decline_by_policy(prof):
    ans, src = auto_answer(q("Gender", options=["Male", "Female", "Decline To Self Identify"]), prof)
    assert (ans, src) == ("Decline To Self Identify", "profile")


def test_unanswerable_questions_become_todo_or_skip(prof):
    assert auto_answer(q("Why do you want to work here?", "textarea", ()), prof) == ("", "TODO")
    assert auto_answer(q("Favorite color?", "text", (), required=False), prof) == ("", "skip")
    assert auto_answer(q("Cover Letter", "file", (), required=False), prof) == ("", "skip")


def test_fmt_blanks_out_empty_templates():
    assert fmt("{city}, {state}", {"city": "Brooklyn", "state": "New York"}) == "Brooklyn, New York"
    assert fmt("{city}, {state}", {}) == ""


def test_missing_profile_is_a_friendly_error(ws):
    ws.profile_path.unlink()
    with pytest.raises(JobRadarError, match="jobradar init"):
        load_profile(ws)
