"""Applications: find a job, fetch its form, auto-answer it, and keep it in a folder you can edit.

prep writes <applications>/<date>-<company>-<title>-<id>/ with
  job.md     the posting (title, links, description)
  form.toml  every question with its answer; TODO = needs your input
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import tomllib
from pathlib import Path

from jobseeker import knockout
from jobseeker.answers import auto_answer
from jobseeker.sources import SOURCES
from jobseeker.store import set_status
from jobseeker.util import safe
from jobseeker.workspace import JobSeekerError, Workspace


# --------------------------------------------------------------------------- finding a job
def load_matches(ws: Workspace) -> list[dict]:
    if not ws.matches_csv.exists():
        return []
    with open(ws.matches_csv, newline="") as f:
        return list(csv.DictReader(f))


def company_slug(ws: Workspace, source: str, company: str) -> str:
    for c in ws.cfg["companies"]:
        if c["name"] == company and c["ats"] == source:
            return c.get("slug", "")
    return ""


def job_from_row(ws: Workspace, r: dict) -> dict:
    source, company, job_id = r["key"].split(":", 2)
    return {"key": r["key"], "source": source, "company": company, "job_id": job_id,
            "slug": company_slug(ws, source, company), "title": r["title"], "url": r["url"],
            "location": r["location"], "mode": r.get("mode", ""), "score": r["score"]}


def job_from_url(ws: Workspace, url: str) -> dict:
    m = (re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)(?:/jobs/|&token=)(\d+)", url)
         or re.search(r"greenhouse\.io/embed/job_app\?.*?for=([\w-]+).*?token=(\d+)", url))
    if m:
        slug, jid, source = m.group(1), m.group(2), "greenhouse"
    elif m := re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url):
        slug, jid, source = m.group(1), m.group(2), "ashby"
    else:
        raise JobSeekerError("Only Greenhouse (…greenhouse.io/<company>/jobs/<id>) and Ashby "
                            "(jobs.ashbyhq.com/<company>/<id>) URLs are supported; or use a number from `list`.")
    name = next((c["name"] for c in ws.cfg["companies"] if c.get("slug") == slug and c["ats"] == source), slug)
    return {"key": f"{source}:{name}:{jid}", "source": source, "company": name, "job_id": jid,
            "slug": slug, "title": "", "url": url, "location": "", "mode": "", "score": ""}


def find_folder(ws: Workspace, job: dict) -> Path | None:
    existing = sorted(ws.apps_dir.glob(f"*-{safe(job['company'])}-*{job['job_id'][-8:]}"))
    return existing[-1] if existing else None


def resolve(ws: Workspace, ref: str) -> tuple[dict, Path | None]:
    """A job from a `list` number, a posting URL, or a prepared folder."""
    p = Path(ref)
    if p.is_dir() and (p / "form.toml").exists():
        return load_form(p)["job"], p.resolve()
    if ref.isdigit():
        rows = load_matches(ws)
        n = int(ref)
        if not 1 <= n <= len(rows):
            raise JobSeekerError(f"No match #{n}; `list` shows 1–{len(rows)}." if rows
                                else "No matches yet. Run `jobseeker run` first.")
        job = job_from_row(ws, rows[n - 1])
    elif ref.startswith("http"):
        rows = {r["url"]: r for r in load_matches(ws)}
        job = job_from_row(ws, rows[ref]) if ref in rows else job_from_url(ws, ref)
    else:
        raise JobSeekerError(f"Don't know how to find {ref!r}: use a number from `list`, a job URL, or a folder.")
    return job, find_folder(ws, job)


# --------------------------------------------------------------------------- knock-outs
def posting_text(folder: Path) -> str:
    """The description saved in job.md at prep time."""
    md = folder / "job.md"
    if not md.exists():
        return ""
    text = md.read_text()
    m = re.search(r"## Description\s*\n(.*)", text, re.S)
    return (m.group(1) if m else text).strip()


def knockouts(ws: Workspace, job: dict, folder: Path | None, prof: dict) -> list[knockout.Flag]:
    """Knock-out flags for a job: from the saved posting + form when prepared, else from the last run."""
    if folder and (folder / "form.toml").exists():
        return knockout.check(posting_text(folder), load_form(folder).get("q", []), prof)
    row = next((r for r in load_matches(ws) if r["key"] == job["key"]), None)
    return knockout.loads(row.get("flags", "")) if row else []


# --------------------------------------------------------------------------- form.toml I/O
def load_form(folder: Path) -> dict:
    with open(folder / "form.toml", "rb") as f:
        return tomllib.load(f)


def toml_str(s):
    s = str(s)
    if "\n" in s:
        return "'''\n" + s.replace("'''", "’’’") + "'''"
    return json.dumps(s, ensure_ascii=False)


def toml_val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "[" + ", ".join(toml_str(x) for x in v) + "]"
    return toml_str(v)


def write_form(path, job, meta, qs):
    todo = sum(q["source"] == "TODO" for q in qs)
    lines = [f"# Application form: {meta['title']} — {job['company']}",
             "# Edit `answer` for any question. source: profile = auto-filled from profile.toml,",
             "# TODO = needs your input (or draft it with an assistant), skip = optional, left blank.",
             "# Dropdown/radio answers must match one of `options` (multiselect: a list).",
             f"# {todo} TODO(s) when prepared.", "",
             f"apply_url = {toml_str(meta['apply_url'])}", "", "[job]"]
    lines += [f"{k} = {toml_str(job[k])}" for k in ("key", "source", "company", "slug", "job_id", "title", "url",
                                                      "location", "mode", "score")]
    for q in qs:
        lines += ["", "[[q]]", f"label = {toml_str(q['label'])}", f"key = {toml_str(q['key'])}",
                  f"type = {toml_str(q['type'])}", f"required = {toml_val(q['required'])}"]
        if q.get("ashby_type"):
            lines.append(f"ashby_type = {toml_str(q['ashby_type'])}")
        if q["options"]:
            lines.append(f"options = {toml_val(q['options'])}")
        lines += [f"source = {toml_str(q['source'])}", f"answer = {toml_val(q['answer'])}"]
    path.write_text("\n".join(lines) + "\n")


def save_form(folder: Path, form: dict, qs: list[dict]) -> int:
    """Rewrite form.toml after editing answers. Returns the number of TODOs left."""
    for q in qs:
        q.setdefault("options", [])
    write_form(folder / "form.toml", form["job"], {"title": form["job"]["title"], "apply_url": form["apply_url"]}, qs)
    return sum(q["source"] == "TODO" for q in qs)


# --------------------------------------------------------------------------- prep
def prep_job(ws: Workspace, prof: dict, job: dict, folder: Path | None = None):
    """Fetch the form, auto-answer it, write job.md + form.toml. Returns (folder, TODO questions),
    with TODO = None for boards whose forms can't be pre-filled."""
    title = job["title"] or "job"
    folder = folder or ws.apps_dir / f"{dt.date.today():%Y-%m-%d}-{safe(job['company'])}-{safe(title)[:30]}-{job['job_id'][-8:]}"
    folder.mkdir(parents=True, exist_ok=True)

    source = SOURCES.get(job["source"])
    if not source or not source.fetch_form:
        (folder / "job.md").write_text(f"# {job['title']} — {job['company']}\n\n{job['url']}\n\n"
                                       f"{job['source'].title()} forms can't be pre-filled; apply on the site.\n")
        set_status(ws, job, folder, "prepared")
        return folder, None

    qs, meta = source.fetch_form(job)
    job["title"] = job["title"] or meta["title"]
    job["location"] = job["location"] or meta["location"]
    for q in qs:
        q["answer"], q["source"] = auto_answer(q, prof)
    (folder / "job.md").write_text(
        f"# {job['title']} — {job['company']}\n\n"
        f"- Posting: {job['url']}\n- Apply: {meta['apply_url']}\n- Location: {job['location']}"
        f"{' · ' + job['mode'] if job['mode'] else ''}\n- Score: {job['score']}\n\n"
        f"## Description\n\n{meta['description']}\n")
    write_form(folder / "form.toml", job, meta, qs)
    set_status(ws, job, folder, "prepared")
    return folder, [q for q in qs if q["source"] == "TODO"]
