import pytest

from jobseeker import knockout
from jobseeker.knockout import Candidate, check, check_form, check_posting, degree_level, sentences


def profile(**work):
    base = {"authorized": "Yes", "sponsorship": "No", "citizenship": "U.S. Citizen", "salary_min": "180000",
            "years_experience": "10", "degree": "Bachelor's", "clearance": ""}
    return {"work": {**base, **work}}


def kinds(flags):
    return {(f.level, f.kind) for f in flags}


# --------------------------------------------------------------------------- profile
def test_candidate_from_profile():
    me = Candidate.from_profile(profile())
    assert (me.needs_sponsorship, me.authorized, me.citizen, me.clearance) == (False, True, True, "")
    assert (me.years, me.degree, me.salary_min) == (10, 1, 180000)
    me = Candidate.from_profile({"work": {"citizenship": "Non-citizen", "clearance": "None", "years_experience": ""}})
    assert not me.citizen and me.clearance == "" and me.years is None and me.degree is None


def test_degree_levels():
    assert degree_level("B.S. in Computer Science") == 1
    assert degree_level("Master's degree") == 2
    assert degree_level("PhD or M.S.") == 3


def test_sentences_keep_abbreviations_together():
    assert sentences("Requirements: B.S., M.S., or Ph.D. in Computer Science. Kafka e.g. streaming.") == [
        "Requirements: B.S., M.S., or Ph.D. in Computer Science.", "Kafka e.g. streaming."]


# --------------------------------------------------------------------------- posting
@pytest.mark.parametrize("text, expected", [
    ("This role requires an active Top Secret Clearance and work in a SCIF.", ("block", "clearance")),
    ("*This role requires an active Secret or Top Secret clearance*", ("block", "clearance")),
    ("Candidates must hold an active TS/SCI clearance with polygraph.", ("block", "clearance")),
    ("A Master's degree in Computer Science is required.", ("block", "degree")),
    ("PhD in Machine Learning or a related field.", ("warn", "degree")),
    ("You have 15+ years of software engineering experience.", ("warn", "experience")),
    ("The base salary range is $130,000 — $172,000.", ("warn", "salary")),
    ("Pay: $120K-$165K plus equity.", ("warn", "salary")),
])
def test_posting_flags(text, expected):
    assert expected in kinds(check_posting(text, profile()))


@pytest.mark.parametrize("text", [
    "B.S., M.S., or PhD in Computer Science, or equivalent experience.",           # bachelor's satisfies it
    "Hands-on experience or an M.Sc./Ph.D. in distributed systems.",                # experience is an alternative
    "A Master's degree is a plus.",                                                 # not required
    "Security clearance is preferred but not required.",
    "You have 8+ years of experience with Java.",                                   # within 10 years
    "Experience with Kafka; 3 years in a row we were named a top workplace.",       # not an experience requirement
    "The base salary range is $190,000 - $260,000.",                                # above the floor
    "Hourly rate: $60 - $90 per hour.",                                             # not an annual range
    "To conform to US export control regulations, candidates should be eligible for any required authorizations.",
    "We are unable to sponsor visas for this role.",                                # this candidate needs none
])
def test_posting_non_flags(text):
    assert check_posting(text, profile()) == []


def test_sponsorship_and_citizenship_depend_on_the_candidate():
    text = "At this time, Acme will not sponsor a new applicant for employment authorization. Must be a U.S. citizen."
    assert check_posting(text, profile()) == []
    flags = check_posting(text, profile(sponsorship="Yes", citizenship="Permanent resident"))
    assert {("block", "sponsorship"), ("block", "citizenship")} <= kinds(flags)
    itar = check_posting("This position is subject to ITAR.", profile(citizenship="Permanent resident"))
    assert kinds(itar) == {("warn", "citizenship")}


def test_evidence_quotes_the_triggering_words():
    long = ("We build integration platforms and automation for thousands of teams across the company and beyond, "
            "with a focus on reliability and observability across every service we run in production today. "
            "At this time, Acme will not sponsor a new applicant for employment authorization for this position")
    [f] = check_posting(long.replace(". At", ", and at"), profile(sponsorship="Yes"))
    assert "will not sponsor" in f.evidence and f.evidence.startswith("…")


def test_one_flag_per_kind_and_highest_years_wins():
    text = "5+ years of Python experience. 12+ years of industry experience. 14 years of experience leading teams."
    [f] = check_posting(text, profile())
    assert f.kind == "experience" and "14+" in f.message


def test_checks_are_skipped_when_profile_fields_are_blank():
    text = "Master's degree required. 20+ years of experience. Salary $100,000 - $120,000."
    assert check_posting(text, {"work": {}}) == []


# --------------------------------------------------------------------------- form
def q(label, answer="", key="question_1"):
    return {"key": key, "label": label, "answer": answer, "type": "select", "options": ["Yes", "No"]}


def test_form_flags():
    me = profile(sponsorship="Yes", authorized="No")
    flags = check_form([q("Will you require visa sponsorship?"), q("Are you legally authorized to work in the US?")], me)
    assert kinds(flags) == {("block", "sponsorship"), ("block", "authorization")}
    flags = check_form([q("Do you hold an active security clearance?"),
                        q("Do you have 12+ years of experience with Go?"),
                        q("Are you willing to work on-site 5 days a week?", "No")], profile())
    assert kinds(flags) == {("block", "clearance"), ("warn", "experience"), ("warn", "onsite")}
    assert flags[0].question == "question_1"


def test_form_is_quiet_for_a_matching_candidate():
    qs = [q("Will you require visa sponsorship?", "No"), q("Are you legally authorized to work in the US?", "Yes"),
          q("Do you have 5+ years of experience?", "Yes"), q("Are you open to hybrid work 3 days a week in office?", "Yes")]
    assert check_form(qs, profile()) == []


def test_check_merges_form_and_posting_blocks_first():
    flags = check("Salary $100,000 - $150,000. Requires an active Secret clearance.",
                  [q("Do you have an active clearance?")], profile())
    assert [f.kind for f in flags] == ["clearance", "salary"]
    assert flags[0].question                     # the form's version of the clearance flag wins


def test_roundtrip_and_worst():
    flags = check_posting("Requires an active Secret clearance. Salary $100,000 - $150,000.", profile())
    assert knockout.loads(knockout.dumps(flags)) == flags
    assert knockout.worst(flags) == "block" and knockout.worst([]) == ""
    assert knockout.loads("not json") == [] and knockout.dumps([]) == ""
