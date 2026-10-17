import tomllib

import pytest

from jobradar import radar, store
from jobradar.answers import load_profile
from jobradar.applications import job_from_url, load_form, load_matches, prep_job, resolve, save_form, write_form
from jobradar.workspace import JobRadarError


def test_run_marks_only_unseen_matches_new(ws, fake_http):
    first = radar.run(ws)
    titles = {j.title for j in first.matched}
    assert "Senior Software Engineer, Payments Ledger" in titles      # remote US
    assert "Staff Data Engineer, Streaming" in titles                             # hybrid, NYC
    assert "Software Engineering Intern" not in titles                 # excluded title
    assert not any(j.location == "San Francisco, CA" for j in first.matched)  # hybrid outside the metro
    assert len(first.new) == len(first.matched) and not first.errors

    second = radar.run(ws)
    assert second.new == [] and len(second.matched) == len(first.matched)
    rows = load_matches(ws)
    assert [r["new"] for r in rows] == ["False"] * len(rows)
    assert "No new matches today" in open(second.digest).read()


def test_failing_board_is_reported_not_fatal(ws, fake_http):
    ws.cfg["companies"].append({"name": "Missing", "track": "backend", "ats": "greenhouse", "slug": "nope"})
    res = radar.run(ws)
    assert res.errors == [("Missing", "greenhouse", "HTTP 404 (slug/tenant probably wrong)")]
    assert res.matched


def test_prep_writes_folder_form_and_status(ws, fake_http):
    radar.run(ws)
    job, folder = resolve(ws, "1")
    assert job["key"] == "greenhouse:Acme:101" and folder is None
    folder, todo = prep_job(ws, load_profile(ws), job)
    assert [q["label"] for q in todo] == ["Why do you want to work here?"]
    form = load_form(folder)
    answers = {q["key"]: (q["answer"], q["source"]) for q in form["q"]}
    assert answers["first_name"] == ("Alex", "profile")
    assert answers["question_1"] == ("No", "profile")                 # sponsorship
    assert answers["gender"][0].startswith("Decline")
    assert "Build our payment ledger." in (folder / "job.md").read_text()
    assert store.applications(ws)[job["key"]]["status"] == "prepared"
    assert resolve(ws, str(folder))[1] == folder.resolve()            # folders resolve too
    assert resolve(ws, job["url"])[1] == folder                       # and URLs


def test_form_roundtrip_keeps_tricky_text(tmp_path):
    job = {k: "x" for k in ("key", "source", "company", "slug", "job_id", "title", "url", "location", "mode", "score")}
    tricky = 'He said "hi"\nLine two with \'\'\' quotes and a \\ backslash'
    qs = [{"key": "a", "label": "Q", "type": "textarea", "required": True, "options": [], "answer": tricky,
           "source": "edited"},
          {"key": "b", "label": "M", "type": "multiselect", "required": False, "options": ["A", "B"],
           "answer": ["A", "B"], "source": "profile"}]
    write_form(tmp_path / "form.toml", job, {"title": "T", "apply_url": "https://x"}, qs)
    form = tomllib.loads((tmp_path / "form.toml").read_text())
    assert form["q"][0]["answer"] == tricky.replace("'''", "’’’")
    assert form["q"][1]["answer"] == ["A", "B"]
    form["q"][0]["source"] = "TODO"
    assert save_form(tmp_path, form, form["q"]) == 1


def test_job_from_url(ws):
    gh = job_from_url(ws, "https://job-boards.greenhouse.io/acme/jobs/555")
    assert (gh["source"], gh["company"], gh["job_id"]) == ("greenhouse", "Acme", "555")
    ab = job_from_url(ws, "https://jobs.ashbyhq.com/beta/aaaaaaaa-0000-0000-0000-000000000009/application")
    assert (ab["source"], ab["company"]) == ("ashby", "Beta")
    with pytest.raises(JobRadarError):
        job_from_url(ws, "https://example.com/careers/1")
