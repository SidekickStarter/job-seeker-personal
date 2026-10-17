"""A run: fetch every enabled company's board, filter and score postings, record new ones, and
write the digest (new matches) and matches_latest.csv (all current matches)."""
from __future__ import annotations

import csv
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from jobseeker.matching import Matcher
from jobseeker.models import Job
from jobseeker.sources import SOURCES
from jobseeker.store import open_db
from jobseeker.util import describe_error
from jobseeker.workspace import JobSeekerError, Workspace

MODES = ("remote", "hybrid", "onsite")


@dataclass
class RunResult:
    scanned: int
    companies: int
    matched: list[Job]
    new: list[Job]
    errors: list[tuple[str, str, str]] = field(default_factory=list)
    digest: str = ""


def fetch_all(cfg, matcher, companies):
    results, errors = [], []
    workers = cfg["settings"].get("max_workers", 8)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(SOURCES[c["ats"]].fetch_jobs, c, matcher.keep): c for c in companies}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                jobs, _ = fut.result()
                results.extend(jobs)
            except Exception as e:
                errors.append((c["name"], c["ats"], describe_error(e)))
    return results, errors


def mode_label(j: Job) -> str:
    label = "in office (hybrid/onsite not stated)" if j.mode == "office" else j.mode
    return label + (" (inferred)" if j.mode_src == "inferred" and j.mode != "office" else "")


def run(ws: Workspace, show_all=False, track=None, min_score=None, modes=None) -> RunResult:
    cfg = ws.cfg
    if modes:
        modes = {m.strip().lower() for m in modes}
        bad = modes - set(MODES)
        if bad:
            raise JobSeekerError(f"unknown work mode {', '.join(sorted(bad))} (use remote, hybrid, onsite)")
        cfg = {**cfg, "location": {**cfg["location"], "modes": sorted(modes)}}
    m = Matcher(cfg)
    companies = [c for c in cfg["companies"] if c.get("enabled", True)]
    if track:
        companies = [c for c in companies if c["track"] == track]

    raw, errors = fetch_all(cfg, m, companies)
    matched = [m.score(j) for j in raw if m.keep(j)]
    if track:
        matched = [j for j in matched if j.best_track == track or j.company_track == track]
    min_score = min_score if min_score is not None else cfg["settings"]["min_score"]
    matched = sorted((j for j in matched if j.score >= min_score), key=lambda j: -j.score)

    db = open_db(ws)
    known = {r[0] for r in db.execute("select key from seen")}
    today = dt.date.today().isoformat()
    new = [j for j in matched if j.key not in known]
    db.executemany("insert or ignore into seen values (?,?,?,?,?,?,?)",
                   [(j.key, today, j.company, j.title, j.url, j.score, j.best_track) for j in new])
    db.commit()

    ws.digests_dir.mkdir(parents=True, exist_ok=True)
    digest = ws.digests_dir / f"digest_{today}.md"
    digest.write_text(render_digest(cfg, matched if show_all else new, len(raw), len(matched), len(new),
                                    errors, show_all))
    write_csv(ws.matches_csv, matched, known)
    return RunResult(len(raw), len(companies), matched, new, errors, str(digest))


def render_digest(cfg, jobs, scanned, matched, new, errors, show_all):
    labels = {k: v.get("label", k) for k, v in cfg["tracks"].items()}
    lines = [f"# Job Seeker — {dt.date.today():%a %b %d, %Y}", "",
             f"Scanned {scanned} postings · {matched} matches · **{new} new**", ""]
    if not jobs:
        lines.append("_No new matches today._")
    for track in cfg["tracks"]:
        group = [j for j in jobs if j.best_track == track]
        if not group:
            continue
        lines += [f"## {labels[track]} ({len(group)})", ""]
        for j in group:
            lines += [f"### [{j.title}]({j.url}) — {j.company}  ·  score {j.score}",
                      f"{j.location or 'location n/a'} · **{mode_label(j)}**"
                      + (f" · posted {j.posted}" if j.posted else ""),
                      f"_Why:_ {j.why}", ""]
    if errors:
        lines += ["## Boards that failed", "",
                  "Fix the slug in config.toml, or run `jobseeker probe` to find the right board.", ""]
        lines += [f"- {n} ({ats}): {msg}" for n, ats, msg in sorted(errors)]
    return "\n".join(lines) + "\n"


def write_csv(path, jobs, known):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["score", "new", "track", "company", "title", "location", "mode", "posted", "url", "why", "key"])
        for j in jobs:
            w.writerow([j.score, j.key not in known, j.best_track, j.company, j.title,
                        j.location, mode_label(j), j.posted, j.url, j.why, j.key])


def verify(ws: Workspace):
    """(company, status, posting count) for every configured board, enabled or not."""
    never = lambda *_: False  # noqa: E731

    def check(c):
        try:
            _, n = SOURCES[c["ats"]].fetch_jobs(c, never)
            return c, ("OK" if n else "EMPTY (0 postings; slug may be wrong or no openings)"), n
        except Exception as e:
            return c, "FAIL " + describe_error(e), 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(check, ws.cfg["companies"]))
    return sorted(rows, key=lambda r: (not r[1].startswith("OK"), r[0]["name"]))
