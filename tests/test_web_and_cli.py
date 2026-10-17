import pytest

from jobseeker import cli, coverletter
from jobseeker.applications import load_form
from jobseeker.demo import build_demo
from jobseeker.web import create_app

H = {"Host": "127.0.0.1:8765"}
W = {**H, "X-JobSeeker": "1"}


@pytest.fixture
def demo(tmp_path):
    return build_demo(tmp_path / "demo")


@pytest.fixture
def client(demo):
    return create_app(demo).test_client()


def test_overview_and_detail(client):
    d = client.get("/api/overview", headers=H).get_json()
    assert len(d["matches"]) == 13 and d["matches"][0]["score"] == 14.6
    assert {a["status"] for a in d["applications"]} == {"prepared", "applied", "interviewing"}
    top = d["matches"][0]
    assert top["todo"] == 1 and top["found"]
    j = client.get("/api/job", query_string={"key": top["key"]}, headers=H).get_json()
    assert len(j["questions"]) == 12 and j["cover_field"] and j["cover_letter"] and j["referral"]


def test_saving_answers_updates_form_and_todo(client, demo):
    key = client.get("/api/overview", headers=H).get_json()["matches"][0]["key"]
    qs = client.get("/api/job", query_string={"key": key}, headers=H).get_json()["questions"]
    answers = [q["answer"] for q in qs]
    answers[10] = "Our ledger migration: dual writes, reconciliation, then cutover."
    r = client.post("/api/form", json={"key": key, "answers": answers}, headers=W)
    assert r.get_json() == {"ok": True, "todo": 0}
    folder = next(demo.apps_dir.glob("*lumen-pay*"))
    assert load_form(folder)["q"][10]["source"] == "edited"


def test_status_changes(client):
    key = client.get("/api/overview", headers=H).get_json()["matches"][0]["key"]
    assert client.post("/api/mark", json={"key": key, "status": "applied"}, headers=W).status_code == 200
    assert client.post("/api/mark", json={"key": key, "status": "hired!"}, headers=W).status_code == 400


def test_guards(client):
    assert client.get("/api/overview", headers={"Host": "evil.example"}).status_code == 403   # DNS rebinding
    assert client.post("/api/mark", json={}, headers=H).status_code == 403                      # no X-JobSeeker header
    assert client.get("/api/file", query_string={"path": "../../etc/passwd"}, headers=H).status_code == 403
    assert client.post("/api/file", json={"path": "jobseeker/cli.py", "text": ""}, headers=W).status_code == 403


def test_file_editing_validates_config(client, demo):
    files = client.get("/api/files", headers=H).get_json()["files"]
    assert "config.toml" in files and "profile.toml" in files and "referrals/intro.md" in files
    bad = client.post("/api/file", json={"path": "config.toml", "text": "min_score = ["}, headers=W)
    assert bad.status_code == 400 and "Not saved" in bad.get_json()["error"]
    ok = client.post("/api/file", json={"path": "referrals/new.md", "text": "hello"}, headers=W)
    assert ok.status_code == 200 and (demo.notes_dir / "new.md").read_text() == "hello"


def test_settings_endpoints(client):
    s = client.get("/api/settings", headers=H).get_json()
    assert {c["name"] for c in s["companies"]} >= {"Stripe", "Capital One"}
    r = client.post("/api/settings", json={"settings": {"min_score": 5}}, headers=W)
    assert r.status_code == 200
    assert client.get("/api/settings", headers=H).get_json()["settings"]["min_score"] == 5


def test_cover_letter_generate_and_attach(demo, monkeypatch):
    folder = next(demo.apps_dir.glob("*lumen-pay*"))
    prompts = []
    letter = coverletter.generate(demo, folder, notes="mention ledgers", draft=lambda p: prompts.append(p) or "Dear X")
    assert letter == "Dear X" and (folder / "cover_letter.md").read_text() == "Dear X\n"
    assert "Only facts" in prompts[0] or "Use only facts" in prompts[0]
    assert "mention ledgers" in prompts[0] and "Lumen Pay" in prompts[0]
    assert coverletter.attach(folder, folder / "Alex_Rivera_Cover_Letter.pdf", "Dear X") == ["Cover Letter"]
    cover = next(q for q in load_form(folder)["q"] if q["key"] == "cover_letter")
    assert cover["answer"].endswith("Alex_Rivera_Cover_Letter.pdf")


def test_cli_init_and_list(tmp_path, demo, capsys):
    cli.main(["--home", str(tmp_path / "new"), "init"])
    assert (tmp_path / "new" / "config.toml").exists() and (tmp_path / "new" / "profile.toml").exists()
    cli.main(["--home", str(demo.home), "list", "--top", "3"])
    out = capsys.readouterr().out
    assert "Lumen Pay" in out and "... 10 more" in out


def test_cli_friendly_error_without_config(tmp_path):
    with pytest.raises(SystemExit, match="No config.toml"):
        cli.main(["--home", str(tmp_path), "list"])


def test_knockouts_in_api_and_cli(client, demo, capsys):
    rows = client.get("/api/overview", headers=H).get_json()["matches"]
    fed = next(r for r in rows if "Federal" in r["title"])
    assert fed["flags"][0]["level"] == "block" and fed["flags"][0]["kind"] == "clearance"
    detail = client.get("/api/job", query_string={"key": fed["key"]}, headers=H).get_json()
    assert detail["flags"][0]["evidence"].startswith("This role requires an active Secret clearance")
    n = next(i for i, r in enumerate(rows, 1) if r["key"] == fed["key"])
    cli.main(["--home", str(demo.home), "check", str(n)])
    out = capsys.readouterr().out
    assert "✗ BLOCK" in out and "security clearance" in out
    cli.main(["--home", str(demo.home), "list"])
    assert "✗ likely knock-out" in capsys.readouterr().out


def test_people_api_and_cli(client, demo, capsys, monkeypatch):
    rows = client.get("/api/overview", headers=H).get_json()["matches"]
    lumen = next(r for r in rows if r["company"] == "Lumen Pay")
    assert lumen["people"] == 4
    d = client.get("/api/people", query_string={"key": lumen["key"]}, headers=H).get_json()
    assert [ld["name"] for ld in d["leads"]][:2] == ["Priya Shah", "Marcus Lee"] and d["has_export"]
    assert d["leads"][0]["likelihood"] == "High" and "Ex-Initech at Lumen Pay" in [x["label"] for x in d["links"]]
    r = client.post("/api/people/closeness", json={"name": "Marcus Lee", "closeness": "close"}, headers=W)
    assert r.status_code == 200
    d = client.get("/api/people", query_string={"key": lumen["key"]}, headers=H).get_json()
    marcus = next(ld for ld in d["leads"] if ld["name"] == "Marcus Lee")
    assert (marcus["score"], marcus["likelihood"], marcus["closeness"]) == (80, "High", "close")
    bad = client.post("/api/network/import", json={"text": "not,a,linkedin,export"}, headers=W)
    assert bad.status_code == 400
    cli.main(["--home", str(demo.home), "network", "companies"])
    assert "Lumen Pay" in capsys.readouterr().out


def test_network_data_is_gitignored():
    ignored = open(".gitignore").read().split()
    assert "linkedin/" in ignored and "contacts.toml" in ignored
