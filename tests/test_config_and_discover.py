import re

import pytest

from jobradar import config_edit
from jobradar.discover import boards_in, name_guesses
from jobradar.workspace import JobRadarError, Workspace


@pytest.fixture
def cws(example_config):
    return Workspace.load(example_config.parent)


def test_saving_unchanged_settings_leaves_the_file_byte_identical(cws):
    before = cws.config_path.read_text()
    view = config_edit.settings_view(cws)
    config_edit.apply_settings(cws, {k: view[k] for k in ("settings", "filters", "location", "tracks", "stack")})
    assert cws.config_path.read_text() == before


def test_changes_are_minimal_and_keep_comments(cws):
    config_edit.apply_settings(cws, {"settings": {"min_score": 7.5}, "location": {"modes": ["remote", "hybrid"]}})
    text = cws.config_path.read_text()
    assert "min_score = 7.5" in text and "modes = ['remote', 'hybrid']" in text
    assert "# Your commute area for hybrid/onsite roles" in text
    assert cws.cfg["settings"]["min_score"] == 7.5            # workspace reloaded


def test_invalid_regex_is_rejected_and_file_untouched(cws):
    before = cws.config_path.read_text()
    with pytest.raises(re.error):
        config_edit.apply_settings(cws, {"filters": {"title_exclude": "ok\nbad(regex"}})
    with pytest.raises(ValueError):
        config_edit.apply_settings(cws, {"location": {"modes": []}})
    assert cws.config_path.read_text() == before


def test_long_lists_are_packed_several_per_line():
    arr = config_edit.tarray([f"keyword{i}" for i in range(30)]).as_string()
    assert 3 < arr.count("\n") < 15


def test_add_update_remove_company(cws):
    config_edit.add_company(cws, "Acme", "greenhouse", "payments", slug="acme")
    with pytest.raises(JobRadarError, match="already"):
        config_edit.add_company(cws, "acme", "greenhouse", slug="acme")
    config_edit.update_company(cws, "Acme", enabled=False, track="backend")
    acme = next(c for c in cws.cfg["companies"] if c["name"] == "Acme")
    assert (acme["enabled"], acme["track"]) == (False, "backend")
    config_edit.update_company(cws, "Acme", remove=True)
    assert all(c["name"] != "Acme" for c in cws.cfg["companies"])


def test_workday_company_needs_its_site(cws):
    with pytest.raises(JobRadarError, match="Workday needs"):
        config_edit.add_company(cws, "Big Co", "workday", host="bigco.wd5.myworkdayjobs.com")


@pytest.mark.parametrize("text, expected", [
    ("https://job-boards.greenhouse.io/acme/jobs/123", {"ats": "greenhouse", "slug": "acme"}),
    ("https://boards.greenhouse.io/embed/job_board?for=acme", {"ats": "greenhouse", "slug": "acme"}),
    ("https://jobs.lever.co/gamma/abc-123", {"ats": "lever", "slug": "gamma"}),
    ("https://jobs.ashbyhq.com/beta/aaaa/application", {"ats": "ashby", "slug": "beta"}),
    ("https://bigco.wd5.myworkdayjobs.com/en-US/BigCo_Careers/job/NY/Eng_R1",
     {"ats": "workday", "host": "bigco.wd5.myworkdayjobs.com", "tenant": "bigco", "site": "BigCo_Careers"}),
])
def test_boards_in_links(text, expected):
    assert boards_in(text)[0] == expected


def test_boards_in_html_page():
    html = '<iframe src="https://boards.greenhouse.io/embed/job_board?for=acme"></iframe>'
    assert boards_in(html) == [{"ats": "greenhouse", "slug": "acme"}]


def test_name_guesses():
    slugs = {g["slug"] for g in name_guesses("Uber Freight")}
    assert {"uberfreight", "uber-freight", "uberfreightinc"} <= slugs
