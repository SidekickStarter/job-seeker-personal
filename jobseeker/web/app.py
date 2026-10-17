"""Local web UI: browse matches, prep and edit application forms, launch the browser pre-fill,
draft cover letters, track status, and edit settings and notes.

Local only: binds to 127.0.0.1. Nothing is ever submitted automatically.
"""
from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
import threading
import tomllib
import webbrowser
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from jobseeker import config_edit, discover, knockout, network, store, util
from jobseeker.answers import load_profile
from jobseeker.applications import (
    company_slug,
    find_folder,
    job_from_row,
    knockouts,
    load_form,
    load_matches,
    prep_job,
    save_form,
)
from jobseeker.sources import SOURCES
from jobseeker.workspace import JobSeekerError, Workspace

STATIC = Path(__file__).parent / "static"
PKG_ROOT = Path(__file__).resolve().parents[2]


def create_app(ws: Workspace) -> Flask:
    app = Flask(__name__)
    home = ws.home

    def fresh() -> Workspace:
        ws.reload()            # re-read config each request so edits (UI, CLI or editor) apply at once
        return ws

    def fail(msg, code=400):
        return jsonify(error=msg), code

    # ----------------------------------------------------------------------- safety
    # Personal data, local only: refuse other Host headers (DNS rebinding) and require a custom
    # header on writes, which a cross-site page can't send without a CORS preflight (never approved).
    @app.before_request
    def guard():
        if (request.host or "").split(":")[0] not in ("127.0.0.1", "localhost"):
            abort(403)
        if request.method == "POST" and request.headers.get("X-JobSeeker") != "1":
            abort(403)

    @app.errorhandler(JobSeekerError)
    def user_error(e):
        return fail(str(e))

    # ----------------------------------------------------------------------- jobs
    def form_info(folder):
        """(todo count, question count) for a prepared folder, or (None, None)."""
        f = folder and ws.resolve(folder) / "form.toml"
        if not f or not f.exists():
            return None, None
        qs = load_form(f.parent).get("q", [])
        return sum(q.get("source") == "TODO" for q in qs), len(qs)

    def find_job(key):
        """(job dict, folder or None) for a match key or a tracked application key."""
        for r in load_matches(ws):
            if r["key"] == key:
                job = job_from_row(ws, r)
                return job, find_folder(ws, job)
        a = store.applications(ws).get(key)
        if not a:
            return None, None
        folder = ws.resolve(a["folder"])
        if folder and (folder / "form.toml").exists():
            return load_form(folder)["job"], folder
        source, company, job_id = key.split(":", 2)
        return {"key": key, "source": source, "company": company, "job_id": job_id,
                "slug": company_slug(ws, source, company), "title": a["title"], "url": a["url"],
                "location": "", "mode": "", "score": ""}, folder

    def cover_pdf(folder):
        pdfs = sorted(folder.glob("*_Cover_Letter.pdf"))
        return pdfs[-1] if pdfs else None

    @app.get("/api/overview")
    def overview():
        fresh()
        apps, found = store.applications(ws), store.first_seen(ws)
        aliases = {c["name"]: c.get("aliases", []) for c in ws.cfg["companies"]}
        people = network.counts_by_company(network.load_contacts(ws), [
            {"name": n, "aliases": aliases.get(n, [])} for n in {r["company"] for r in load_matches(ws)}])
        rows = []
        for i, r in enumerate(load_matches(ws), 1):
            a = apps.get(r["key"], {})
            todo, nq = form_info(a.get("folder"))
            rows.append({"n": i, "key": r["key"], "score": float(r["score"]), "new": r["new"] == "True",
                         "company": r["company"], "title": r["title"].strip(), "location": r["location"],
                         "mode": r["mode"], "posted": r["posted"], "url": r["url"], "why": r["why"],
                         "track": r["track"], "status": a.get("status", ""), "todo": todo, "questions": nq,
                         "found": found.get(r["key"], ""),
                         "flags": [vars(f) for f in knockout.loads(r.get("flags", ""))],
                         "people": people.get(r["company"], 0)})
        in_matches = {r["key"] for r in rows}
        tracked = []
        for a in sorted(apps.values(), key=lambda a: a["updated"] or "", reverse=True):
            todo, _ = form_info(a["folder"])
            tracked.append({**a, "todo": todo, "in_matches": a["key"] in in_matches})
        log = ws.logs_dir / "daily.log"
        runs = re.findall(r"=== (\S+ \S+) daily run\n(Scanned[^\n]*)", log.read_text()) if log.exists() else []
        return jsonify(matches=rows, applications=tracked, statuses=store.STATUSES,
                       last_run=runs[-1] if runs else None,
                       companies=sum(x.get("enabled", True) for x in ws.cfg["companies"]))

    @app.get("/api/job")
    def job_detail():
        fresh()
        job, folder = find_job(request.args.get("key", ""))
        if not job:
            return fail("Unknown job", 404)
        src = SOURCES.get(job["source"])
        try:
            flags = knockouts(ws, job, folder, load_profile(ws))
        except JobSeekerError:          # no profile.toml yet
            flags = []
        out = {"job": job, "folder": ws.rel(folder) if folder else None, "flags": [vars(f) for f in flags],
               "status": store.applications(ws).get(job["key"], {}).get("status", ""),
               "fillable": bool(src and src.fill)}
        if folder:
            if (folder / "form.toml").exists():
                form = load_form(folder)
                out["apply_url"] = form.get("apply_url")
                out["questions"] = form.get("q", [])
            md = folder / "job.md"
            out["description"] = md.read_text() if md.exists() else ""
            ref = folder / "referral.md"
            out["referral"] = ref.read_text() if ref.exists() else None
            cl, pdf = folder / "cover_letter.md", cover_pdf(folder)
            out["cover_letter"] = cl.read_text() if cl.exists() else None
            out["cover_pdf"] = pdf.name if pdf else None
            out["cover_pdf_stale"] = bool(pdf and cl.exists() and cl.stat().st_mtime > pdf.stat().st_mtime)
            out["cover_field"] = any(re.search(r"cover\s*letter", q["label"], re.I) or q["key"] == "cover_letter"
                                     for q in out.get("questions", []))
        return jsonify(out)

    def greenhouse_ids(job):
        ids = {"posting": job["job_id"]}
        if job["source"] == "greenhouse" and job.get("slug"):
            try:
                d = util.http_json(f"https://boards-api.greenhouse.io/v1/boards/{job['slug']}/jobs/{job['job_id']}")
                ids["requisition"] = d.get("requisition_id") or ""
                ids["internal"] = d.get("internal_job_id") or ""
                for m in d.get("metadata") or []:
                    if m.get("name") == "Position ID" and m.get("value"):
                        ids["position"] = m["value"]
            except Exception as e:
                ids["error"] = util.describe_error(e)
        return ids

    @app.get("/api/ids")
    def job_ids():
        """Requisition / position IDs from Greenhouse (what referrers search for internally)."""
        job, _ = find_job(request.args.get("key", ""))
        if not job:
            return fail("Unknown job", 404)
        return jsonify(greenhouse_ids(job))

    # ----------------------------------------------------------------------- people
    def company_aliases(name):
        return next((c.get("aliases", []) for c in ws.cfg["companies"] if c["name"] == name), [])

    @app.get("/api/people")
    def people():
        """Your connections at the job's company, ranked by referral likelihood, plus search links."""
        fresh()
        job, _ = find_job(request.args.get("key", ""))
        if not job:
            return fail("Unknown job", 404)
        try:
            prof = load_profile(ws)
        except JobSeekerError:
            prof = {}
        me = {**prof.get("me", {}), **prof.get("work", {})}
        contacts = network.load_contacts(ws)
        leads = network.rank(contacts, job["company"], job["title"], company_aliases(job["company"]))
        req = greenhouse_ids(job).get("requisition", "") if leads else ""
        return jsonify(
            has_export=(ws.network_dir / network.CONNECTIONS).exists(), total=len(contacts),
            closeness=[k for k in network.CLOSENESS if k],
            leads=[{"name": ld.contact.name, "position": ld.contact.position, "url": ld.contact.url,
                    "connected_on": ld.contact.connected_on, "closeness": ld.contact.closeness,
                    "note": ld.contact.note, "role": ld.role, "score": ld.score, "likelihood": ld.likelihood,
                    "reasons": ld.reasons, "draft": network.draft_note(ld, job, me, req)} for ld in leads],
            links=network.search_links(job["company"], job["title"], me.get("past_employers", [])))

    @app.post("/api/people/closeness")
    def people_closeness():
        b = request.get_json(force=True)
        contact = next((c for c in network.load_contacts(ws)
                        if (b.get("url") and c.url == b["url"]) or c.name == b.get("name")), None)
        if not contact:
            return fail("Unknown contact", 404)
        try:
            network.set_closeness(ws, contact, b.get("closeness", ""), b.get("note"))
        except ValueError as e:
            return fail(str(e))
        return jsonify(ok=True)

    @app.post("/api/network/import")
    def network_import():
        try:
            n = network.save_import(ws, request.get_json(force=True).get("text", ""))
        except ValueError as e:
            return fail(str(e))
        return jsonify(ok=True, count=n)

    @app.post("/api/prep")
    def prep():
        fresh()
        body = request.get_json(force=True)
        job, folder = find_job(body.get("key", ""))
        if not job:
            return fail("Unknown job", 404)
        if folder and not body.get("force"):
            return fail("Already prepared. Re-prep overwrites your edits to the form.")
        try:
            folder, todo = prep_job(ws, load_profile(ws), job, folder)
        except JobSeekerError:
            raise
        except Exception as e:
            return fail(f"Prep failed: {util.describe_error(e)}", 502)
        return jsonify(folder=ws.rel(folder), todo=None if todo is None else len(todo))

    @app.post("/api/form")
    def save_answers():
        """Save edited answers back to form.toml (the same file the CLI uses)."""
        body = request.get_json(force=True)
        job, folder = find_job(body.get("key", ""))
        if not folder or not (folder / "form.toml").exists():
            return fail("Not prepared yet")
        form = load_form(folder)
        qs, answers = form.get("q", []), body.get("answers", [])
        if len(answers) != len(qs):
            return fail("Form changed on disk; reload and try again", 409)
        for q, ans in zip(qs, answers):
            if ans != q.get("answer"):
                q["answer"] = ans
                q["source"] = "edited" if (ans if not isinstance(ans, list) else any(ans)) else (
                    "TODO" if q.get("required") else "skip")
        return jsonify(ok=True, todo=save_form(folder, form, qs))

    @app.post("/api/mark")
    def mark():
        body = request.get_json(force=True)
        if body.get("status") not in store.STATUSES:
            return fail("Unknown status")
        job, folder = find_job(body.get("key", ""))
        if not job:
            return fail("Unknown job", 404)
        store.set_status(ws, job, folder, body["status"])
        return jsonify(ok=True)

    # ----------------------------------------------------------------------- background tasks
    tasks, ids = {}, itertools.count(1)

    def start_task(kind, args, key=None):
        """Run `jobseeker <args>` in a subprocess, collecting its output for the UI to poll."""
        for t in tasks.values():
            if t["kind"] == kind and t["key"] == key and t["code"] is None:
                return t["id"]          # already running
        tid = next(ids)
        t = tasks[tid] = {"id": tid, "kind": kind, "key": key, "lines": [], "code": None}
        env = {**os.environ, "PYTHONUNBUFFERED": "1",
               "PYTHONPATH": os.pathsep.join(filter(None, [str(PKG_ROOT), os.environ.get("PYTHONPATH")]))}
        proc = subprocess.Popen([sys.executable, "-m", "jobseeker", "--home", str(home), *args], cwd=home,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, env=env)

        def pump():
            for line in proc.stdout:
                t["lines"].append(line.rstrip("\n"))
            t["code"] = proc.wait()
        threading.Thread(target=pump, daemon=True).start()
        return tid

    @app.post("/api/fill")
    def fill():
        job, folder = find_job(request.get_json(force=True).get("key", ""))
        if not folder:
            return fail("Prep it first")
        src = SOURCES.get(job["source"])
        if not src or not src.fill:
            return fail(f"{job['source']} forms can't be pre-filled; apply on the site")
        return jsonify(task=start_task("fill", ["fill", str(folder), "--no-prompt"], job["key"]))

    @app.post("/api/cover/generate")
    def cover_generate():
        body = request.get_json(force=True)
        job, folder = find_job(body.get("key", ""))
        if not folder:
            return fail("Prep the application first (the letter is written from the full posting)")
        length = body.get("length") if body.get("length") in ("short", "standard", "long") else "standard"
        args = ["cover", "generate", str(folder), "--length", length, "--notes", body.get("notes", "")[:1000]]
        return jsonify(task=start_task("cover", args, job["key"]))

    @app.post("/api/cover/pdf")
    def cover_make_pdf():
        job, folder = find_job(request.get_json(force=True).get("key", ""))
        if not folder or not (folder / "cover_letter.md").exists():
            return fail("Write or generate the letter first")
        return jsonify(task=start_task("cover_pdf", ["cover", "pdf", str(folder)], job["key"]))

    @app.get("/api/cover/pdf")
    def cover_download():
        job, folder = find_job(request.args.get("key", ""))
        pdf = folder and cover_pdf(folder)
        if not pdf:
            abort(404)
        return send_file(pdf, mimetype="application/pdf", download_name=pdf.name,
                         as_attachment=request.args.get("download") == "1")

    @app.post("/api/run")
    def run():
        return jsonify(task=start_task("run", ["daily", "--no-notify"]))

    @app.get("/api/tasks/<int:tid>")
    def task(tid):
        t = tasks.get(tid)
        return jsonify(t) if t else fail("No such task", 404)

    # ----------------------------------------------------------------------- files
    def editable_files():
        files = [ws.config_path.name, ws.profile_path.name]
        for pattern in (ws.notes_dir.glob("*.md"), (ws.apps_dir / "_answers").glob("*.md"),
                        ws.apps_dir.glob("*/referral.md")):
            files += sorted(ws.rel(p) for p in pattern)
        return [f for f in files if (home / f).exists()]

    def safe_path(rel, new_ok=False):
        p = (home / rel).resolve()
        if not p.is_relative_to(home) or p.suffix not in (".md", ".toml"):
            abort(403)
        notes, apps = ws.rel(ws.notes_dir), ws.rel(ws.apps_dir)
        allowed = rel in editable_files() or (new_ok and re.fullmatch(
            rf"({re.escape(notes)}|{re.escape(apps)}/_answers)/[\w .-]+\.md|"
            rf"{re.escape(apps)}/[\w.-]+/(referral|cover_letter)\.md", rel))
        if not allowed:
            abort(403)
        return p

    @app.get("/api/files")
    def files():
        return jsonify(files=editable_files())

    @app.get("/api/file")
    def read_file():
        p = safe_path(request.args.get("path", ""))
        return jsonify(path=request.args["path"], text=p.read_text())

    @app.post("/api/file")
    def write_file():
        body = request.get_json(force=True)
        rel, text = body.get("path", ""), body.get("text", "")
        p = safe_path(rel, new_ok=True)
        if p.suffix == ".toml":
            try:
                parsed = tomllib.loads(text)
                if p == ws.config_path:
                    config_edit.validate(parsed)
            except (tomllib.TOMLDecodeError, re.error, KeyError) as e:
                return fail(f"Not saved: {e}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return jsonify(ok=True)

    @app.get("/api/digest")
    def digest():
        ds = sorted(ws.digests_dir.glob("digest_*.md"), reverse=True)
        name = request.args.get("name") or (ds[0].name if ds else "")
        p = ws.digests_dir / name
        if not re.fullmatch(r"digest_[\d-]+\.md", name) or not p.exists():
            return jsonify(names=[d.name for d in ds], name=None, text="")
        return jsonify(names=[d.name for d in ds], name=name, text=p.read_text())

    @app.get("/api/log")
    def log():
        p = ws.logs_dir / "daily.log"
        return jsonify(text="\n".join(p.read_text().splitlines()[-200:]) if p.exists() else "")

    # ----------------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        fresh()
        counts = {}
        for r in load_matches(ws):
            counts[r["company"]] = counts.get(r["company"], 0) + 1
        return jsonify(config_edit.settings_view(ws, counts))

    @app.post("/api/settings")
    def save_settings():
        try:
            config_edit.apply_settings(ws, request.get_json(force=True))
        except (ValueError, re.error, tomllib.TOMLDecodeError) as e:
            return fail(f"Not saved: {e}")
        return jsonify(ok=True)

    @app.post("/api/companies/detect")
    def detect_company():
        q = request.get_json(force=True).get("query", "").strip()
        if not q:
            return fail("Enter a company name or careers link")
        name, found, note = discover.detect(q)
        return jsonify(name=name, found=found, note=note)

    @app.post("/api/companies/add")
    def add_company():
        b = request.get_json(force=True)
        try:
            config_edit.add_company(ws, b.get("name"), b.get("ats"), b.get("track"), slug=b.get("slug"),
                                    host=b.get("host"), tenant=b.get("tenant"), site=b.get("site"))
        except (ValueError, re.error) as e:
            return fail(f"Not saved: {e}")
        return jsonify(ok=True)

    @app.post("/api/companies/update")
    def update_company():
        b = request.get_json(force=True)
        config_edit.update_company(ws, b.get("name"), enabled=b.get("enabled"), track=b.get("track"),
                                   remove=bool(b.get("remove")))
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return send_file(STATIC / "index.html")

    return app


def serve(ws: Workspace, port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://127.0.0.1:{port}"
    print(f"Job Seeker UI: {url}  (workspace {ws.home}; Ctrl-C to stop)")
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    create_app(ws).run(host="127.0.0.1", port=port, threaded=True)
