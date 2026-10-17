import copy

import pytest

from jobseeker.matching import Matcher, hits, kw_regex
from jobseeker.models import Job


def job(title="Senior Software Engineer", location="Remote - US", description="", mode="", track="backend"):
    return Job("Acme", track, "greenhouse", "1", title, location, "https://example.com/1",
               description=description, mode=mode, mode_src="listed" if mode else "")


@pytest.fixture
def m(ws):
    return Matcher(ws.cfg)


# --------------------------------------------------------------------------- keywords
def test_keywords_match_word_prefixes():
    assert kw_regex("forecast").search("forecasting demand")
    assert not kw_regex("forecast").search("reforecast")          # must start a word


def test_trailing_dollar_means_whole_word():
    assert kw_regex("java$").search("we use java and go")
    assert not kw_regex("java$").search("javascript only")
    assert hits(["java$", "kafka"], "kafka and javascript") == ["kafka"]


# --------------------------------------------------------------------------- titles
@pytest.mark.parametrize("title, ok", [
    ("Senior Software Engineer", True),
    ("Staff Engineer, Internal Tools", True),            # "intern" must not match "Internal"
    ("Software Engineering Intern", False),
    ("Senior Solutions Architect", False),
    ("Senior Product Designer", False),                  # not an engineering title
    ("Engineering Manager, Platform", False),            # managers excluded by default
    ("Software Engineer", True),                         # unlabeled seniority allowed (scored lower)
])
def test_title_filter(m, title, ok):
    assert m.title_ok(title) is ok


def test_managers_can_be_included(ws):
    cfg = copy.deepcopy(ws.cfg)
    cfg["settings"]["include_managers"] = True
    assert Matcher(cfg).title_ok("Engineering Manager, Platform")


# --------------------------------------------------------------------------- location + work mode
@pytest.mark.parametrize("location, mode, ok", [
    ("Remote - US", "remote", True),
    ("Remote - Canada", "remote", False),
    ("New York, NY", "hybrid", True),
    ("San Francisco, CA", "hybrid", False),
    ("San Francisco, CA; New York, NY", "hybrid", True),  # any listed office in the metro passes
    ("Rochester, NY", "onsite", False),                    # matches '\bny\b' but is in metro_exclude
])
def test_location_rules(m, location, mode, ok):
    j = job(location=location, mode=mode)
    assert m.keep(j) is ok


def test_modes_setting_drops_onsite(ws):
    cfg = copy.deepcopy(ws.cfg)
    cfg["location"]["modes"] = ["remote", "hybrid"]
    assert not Matcher(cfg).keep(job(location="New York, NY", mode="onsite"))


@pytest.mark.parametrize("location, description, expected", [
    ("New York, NY", "This is a hybrid role.", ("hybrid", "inferred")),
    ("New York, NY", "Expect three days a week in the office.", ("hybrid", "inferred")),
    ("New York, NY", "We use hybrid cloud infrastructure.", ("office", "inferred")),  # not a work mode
    ("United States", "This role is fully remote.", ("remote", "inferred")),
    ("Remote - US", "", ("remote", "inferred")),
])
def test_work_mode_inference(m, location, description, expected):
    assert m.work_mode(job(location=location, description=description)) == expected


def test_listed_mode_wins_over_text(m):
    assert m.work_mode(job(location="Remote", description="fully remote", mode="hybrid")) == ("hybrid", "listed")


# --------------------------------------------------------------------------- scoring
def test_scoring_prefers_title_hits_and_company_track(m):
    title_hit = m.score(job(title="Senior Engineer, Payments Ledger", track="payments"))
    desc_only = m.score(job(title="Senior Engineer", description="payments ledger", track="payments"))
    assert title_hit.score > desc_only.score
    assert title_hit.best_track == "payments"
    assert "title: payment, ledger" in title_hit.why


def test_unlabeled_seniority_and_penalties_lower_the_score(m):
    senior = m.score(job(title="Senior Backend Engineer"))
    plain = m.score(job(title="Backend Engineer"))
    rusty = m.score(job(title="Senior Backend Engineer", description="written in rust"))
    assert plain.score == senior.score - 2
    assert rusty.score < senior.score and "gap: rust" in rusty.why


def test_staff_and_principal_get_a_bump(m):
    assert m.score(job(title="Staff Backend Engineer")).score == m.score(job(title="Senior Backend Engineer")).score + 1
