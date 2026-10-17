"""The scheduled run: fetch boards, auto-prep strong new matches, notify.

  1. run: fetch boards, write today's digest of new matches
  2. auto-prep new matches scoring >= auto_prep_min_score (fetch form, fill answers; never submits)
  3. desktop notification with the counts (macOS), only when there's something new or it failed
"""
from __future__ import annotations

import datetime as dt
import platform
import shutil
import subprocess

from jobseeker import knockout, radar
from jobseeker.answers import load_profile
from jobseeker.applications import find_folder, job_from_row, load_matches, prep_job
from jobseeker.workspace import Workspace


def notify(title: str, message: str) -> None:
    if platform.system() != "Darwin" or not shutil.which("osascript"):
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}"'],
                       check=False, timeout=10)
    except Exception:
        pass


def daily(ws: Workspace, notify_user: bool = True) -> str:
    print(f"=== {dt.datetime.now():%Y-%m-%d %H:%M} daily run")
    s = ws.settings
    cutoff = float(s.get("auto_prep_min_score", 10))
    cap = int(s.get("auto_prep_max", 10))

    res = radar.run(ws)
    print(f"Scanned {res.scanned} postings from {res.companies} companies; "
          f"{len(res.matched)} matches, {len(res.new)} new. Errors: {len(res.errors)}")
    print(f"Digest: {res.digest}")
    for j in res.new[:10]:
        print(f"  {j.score:5.1f}  {j.company:<14} {j.title[:55]:<55} {j.mode:<7} {j.location[:30]}")

    rows = load_matches(ws)
    new = [r for r in rows if r["new"] == "True"]
    strong = [r for r in new if float(r["score"]) >= cutoff]

    prof = load_profile(ws) if strong else None
    ready, todo, manual, failed, knocked = [], [], [], [], []
    for r in strong:
        if len(ready) + len(todo) + len(manual) + len(failed) >= cap:
            break
        flags = knockout.loads(r.get("flags", ""))
        if knockout.worst(flags) == "block":      # don't spend effort on a likely auto-reject
            knocked.append(r)
            print(f"  skipped {r['company']} — {r['title']}: {flags[0].message}")
            continue
        job = job_from_row(ws, r)
        if find_folder(ws, job):
            continue
        try:
            folder, qs = prep_job(ws, prof, job)
        except Exception as e:
            failed.append(r)
            print(f"  prep failed: {r['company']} — {r['title']}: {e}")
            continue
        (manual if qs is None else todo if qs else ready).append(r)
        print(f"  prepped {ws.rel(folder)}" + ("" if qs is None else f" ({len(qs)} TODO)"))

    parts = [f"{len(new)} new match{'es' if len(new) != 1 else ''}"]
    if ready:
        parts.append(f"{len(ready)} ready to apply")
    if todo:
        parts.append(f"{len(todo)} need answers")
    if manual:
        parts.append(f"{len(manual)} apply on site")
    if failed:
        parts.append(f"{len(failed)} prep failed")
    if knocked:
        parts.append(f"{len(knocked)} skipped (knock-out)")
    summary = ", ".join(parts)
    print(f"Summary: {summary}")
    if new and notify_user:
        top = max(new, key=lambda r: float(r["score"]))
        notify("Job Seeker", f"{summary}. Top: {top['company']} — {top['title'][:60]}")
    return summary
