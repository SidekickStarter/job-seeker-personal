from jobradar.sources import SOURCES

keep_all = lambda *_: True  # noqa: E731


def test_greenhouse_jobs(fake_http):
    jobs, n = SOURCES["greenhouse"].fetch_jobs({"name": "Acme", "track": "payments", "slug": "acme"}, keep_all)
    assert n == 4
    j = jobs[0]
    assert (j.key, j.title, j.location, j.posted) == (
        "greenhouse:Acme:101", "Senior Software Engineer, Payments Ledger", "Remote - US", "2026-01-05")
    assert j.description == "Build our payment ledger on Kafka and Java. This role is fully remote."  # unescaped
    assert j.mode == ""          # Greenhouse has no workplace field; the matcher infers it


def test_ashby_trusts_workplace_type_over_is_remote(fake_http):
    jobs, n = SOURCES["ashby"].fetch_jobs({"name": "Beta", "track": "backend", "slug": "beta"}, keep_all)
    assert n == 3 and len(jobs) == 2                        # unlisted posting skipped
    hybrid, remote = jobs
    assert (hybrid.mode, hybrid.mode_src) == ("hybrid", "listed")   # isRemote is true, but it's Hybrid
    assert hybrid.location == "New York / Remote"
    assert remote.mode == "remote" and remote.location == "Remote"


def test_lever_jobs(fake_http):
    jobs, n = SOURCES["lever"].fetch_jobs({"name": "Gamma", "track": "backend", "slug": "gamma"}, keep_all)
    assert n == 1
    j = jobs[0]
    assert (j.mode, j.location, j.posted) == ("remote", "United States / Remote", "2026-01-01")
    assert "Kafka" in j.description


def test_greenhouse_form(fake_http):
    job = {"slug": "acme", "job_id": "101"}
    qs, meta = SOURCES["greenhouse"].fetch_form(job)
    assert [q["key"] for q in qs] == ["first_name", "email", "resume", "cover_letter", "question_1", "question_2",
                                     "gender"]
    assert qs[4]["type"] == "select" and qs[4]["options"] == ["Yes", "No"]
    assert qs[5]["type"] == "textarea" and qs[5]["required"]
    assert meta["apply_url"] == "https://job-boards.greenhouse.io/embed/job_app?for=acme&token=101"


def test_every_source_is_registered():
    assert set(SOURCES) == {"greenhouse", "lever", "ashby", "workday"}
    assert SOURCES["greenhouse"].fill and SOURCES["ashby"].fill
