"""jobseeker: watch company job boards, score postings, and prepare applications for review.

  jobseeker init                      create config.toml + profile.toml in the current folder
  jobseeker web                       open the local web UI (everything below, in a browser)
  jobseeker demo                      try the UI with sample data, no setup needed

  jobseeker run [--all] [--mode remote,hybrid] [--track T] [--min-score N]
  jobseeker add "Stripe"              find a company's job board and add it (name, careers URL, or posting URL)
  jobseeker verify                    check every configured board

  jobseeker list [--top 30]           numbered matches from the latest run, with status
  jobseeker check <#|url|folder>      knock-out warnings: requirements you'd likely be rejected on
  jobseeker prep <#|url|folder>       fetch the form and auto-answer it from profile.toml
  jobseeker fill <#|url|folder>       pre-fill it in a browser; you review and click Submit
  jobseeker cover generate <job>      draft a cover letter (local `claude` CLI); `cover pdf <job>` renders it
  jobseeker mark <job> applied        prepared | filled | applied | interviewing | rejected | offer | withdrawn
  jobseeker status

  jobseeker daily                     run + auto-prep strong new matches + notification (for schedules)
  jobseeker schedule install [--at 08:00] [--every-day]

Nothing is ever submitted automatically.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from jobseeker import __version__
from jobseeker.workspace import JobSeekerError, Workspace, find_home

TEMPLATES = Path(__file__).parent / "templates"


# --------------------------------------------------------------------------- setup
def cmd_init(args):
    home = find_home(args.home)
    home.mkdir(parents=True, exist_ok=True)
    for src, dst in (("config.example.toml", "config.toml"), ("profile.example.toml", "profile.toml")):
        target = home / dst
        if target.exists() and not args.force:
            print(f"kept     {target} (exists; --force to overwrite)")
            continue
        shutil.copy(TEMPLATES / src, target)
        print(f"created  {target}")
    gi = home / ".gitignore"
    if not gi.exists():
        gi.write_text("# Job Seeker personal data\nprofile.toml\n*.db\ndigests/\napplications/\nlogs/\nreferrals/\n")
    print("\nNext: edit profile.toml (your details + resume path) and config.toml (companies, filters),\n"
          "then `jobseeker run` or `jobseeker web`.")


def cmd_demo(args):
    from jobseeker.demo import build_demo
    from jobseeker.web import serve
    ws = build_demo(Path(args.dir) if args.dir else None)
    print(f"Demo workspace with sample data: {ws.home}")
    serve(ws, args.port, not args.no_open)


# --------------------------------------------------------------------------- finding jobs
def cmd_run(ws, args):
    from jobseeker import radar
    res = radar.run(ws, show_all=args.all, track=args.track, min_score=args.min_score,
                    modes=args.mode.split(",") if args.mode else None)
    print(f"Scanned {res.scanned} postings from {res.companies} companies; "
          f"{len(res.matched)} matches, {len(res.new)} new. Errors: {len(res.errors)}")
    print(f"Digest: {res.digest}")
    for j in (res.matched if args.all else res.new)[:10]:
        print(f"  {j.score:5.1f}  {j.company:<14} {j.title[:55]:<55} {j.mode:<7} {j.location[:30]}")
    for name, ats, msg in res.errors:
        print(f"  ! {name} ({ats}): {msg}")


def cmd_verify(ws, args):
    from jobseeker import radar
    for c, status, n in radar.verify(ws):
        print(f"{c['name']:<18} {c['ats']:<10} {n:>5}  {status}")


def cmd_add(ws, args):
    from jobseeker import config_edit, discover
    name, found, note = discover.detect(args.query)
    if not found:
        raise JobSeekerError(note)
    for i, b in enumerate(found, 1):
        print(f"  {i}. {b['ats']:<10} {b.get('slug') or b['host'] + '/' + b['site']:<40} {b['count']:>5} open jobs")
    if args.dry_run:
        return
    b = found[args.pick - 1]
    name = args.name or name
    config_edit.add_company(ws, name, b["ats"], args.track, slug=b.get("slug"), host=b.get("host"),
                            tenant=b.get("tenant"), site=b.get("site"))
    print(f"Added {name} ({b['ats']}). Its jobs appear on the next `jobseeker run`.")


# --------------------------------------------------------------------------- applications
def cmd_list(ws, args):
    from jobseeker import store
    from jobseeker.applications import load_matches
    rows = load_matches(ws)
    if not rows:
        raise JobSeekerError("No matches yet. Run `jobseeker run` first.")
    from jobseeker import knockout
    status = {k: a["status"] for k, a in store.applications(ws).items()}
    marks = {"block": "✗", "warn": "!", "": " "}
    print(f"{'#':>3}  {'score':>5}  {'status':<12} {'company':<12} {'mode':<7}   title")
    for i, r in enumerate(rows[:args.top], 1):
        mode = "office" if r.get("mode", "").startswith("in office") else r.get("mode", "").split(" ")[0]
        mark = marks[knockout.worst(knockout.loads(r.get("flags", "")))]
        print(f"{i:>3}  {float(r['score']):5.1f}  {status.get(r['key'], ''):<12} {r['company'][:12]:<12} "
              f"{mode:<7} {mark} {r['title'][:70]}")
    if len(rows) > args.top:
        print(f"... {len(rows) - args.top} more (use --top)")
    if any(knockout.loads(r.get("flags", "")) for r in rows[:args.top]):
        print("✗ likely knock-out   ! check before applying   (details: jobseeker check <#>)")


def print_flags(flags) -> None:
    if not flags:
        print("No knock-out warnings.")
    for f in flags:
        print(f"  {'✗ BLOCK' if f.level == 'block' else '! WARN '}  {f.message}")
        if f.evidence:
            print(f"           \u201c{f.evidence}\u201d")


def cmd_check(ws, args):
    from jobseeker.answers import load_profile
    from jobseeker.applications import knockouts, resolve
    job, folder = resolve(ws, args.job)
    print(f"{job['company']} — {job['title'].strip()}")
    print_flags(knockouts(ws, job, folder, load_profile(ws)))
    if not folder:
        print("(posting only; `prep` it to also check the application questions)")


def cmd_prep(ws, args):
    from jobseeker.answers import load_profile
    from jobseeker.applications import prep_job, resolve
    job, folder = resolve(ws, args.job)
    if folder and not args.force:
        print(f"Already prepared: {ws.rel(folder)}\n(use --force to re-fetch; your edits to form.toml would be lost)")
        return
    prof = load_profile(ws)
    folder, todo = prep_job(ws, prof, job, folder)
    from jobseeker.applications import knockouts
    flags = knockouts(ws, job, folder, prof)
    if flags:
        print("Knock-out warnings:")
        print_flags(flags)
    if todo is None:
        print(f"Prepared {ws.rel(folder)} (manual apply: {job['source']} forms aren't pre-filled)")
        return
    print(f"Prepared {ws.rel(folder)}  ({len(todo)} TODO)")
    for q in todo:
        print(f"  TODO {'(required) ' if q['required'] else ''}{q['label'][:90]}")
    if todo:
        print("Answer them in form.toml (or in `jobseeker web`), then run `fill`.")


def cmd_fill(ws, args):
    from jobseeker import store
    from jobseeker.answers import load_profile
    from jobseeker.applications import resolve
    from jobseeker.browser import fill_job
    job, folder = resolve(ws, args.job)
    if not folder:
        raise JobSeekerError(f"Not prepared yet. Run `jobseeker prep {args.job}` first.")
    fill_job(ws, job, folder, load_profile(ws), headless=args.headless, screenshot=args.screenshot)
    if args.headless or args.no_prompt:
        return
    if input("Did you submit the application? [y/N] ").strip().lower().startswith("y"):
        store.set_status(ws, job, folder, "applied")
        print("Marked applied.")


def cmd_cover(ws, args):
    from jobseeker import coverletter
    from jobseeker.applications import resolve
    job, folder = resolve(ws, args.job)
    if not folder or not (folder / "form.toml").exists():
        raise JobSeekerError(f"Not prepared yet. Run `jobseeker prep {args.job}` first.")
    if args.action == "generate":
        coverletter.generate(ws, folder, args.notes, args.length)
    else:
        coverletter.to_pdf(ws, folder)


def cmd_mark(ws, args):
    from jobseeker import store
    from jobseeker.applications import resolve
    job, folder = resolve(ws, args.job)
    store.set_status(ws, job, folder, args.status)
    print(f"{job['company']} — {job['title']}: {args.status}")


def cmd_status(ws, args):
    from jobseeker import store
    apps = sorted(store.applications(ws).values(), key=lambda a: a["updated"] or "", reverse=True)
    if not apps:
        print("No applications yet. Start with `jobseeker list` and `prep`.")
    for a in apps:
        print(f"{a['updated']}  {a['status']:<12} {a['company'][:14]:<14} {a['title'][:60]:<60} {a['url']}")


# --------------------------------------------------------------------------- automation
def cmd_daily(ws, args):
    from jobseeker.daily import daily, notify
    try:
        daily(ws, notify_user=not args.no_notify)
    except Exception:
        if not args.no_notify:
            notify("Job Seeker", "Daily run failed. See logs/daily.log")
        raise


def cmd_web(ws, args):
    from jobseeker.web import serve
    serve(ws, args.port, not args.no_open)


def cmd_schedule(ws, args):
    from jobseeker import schedule
    if args.action == "remove":
        print(schedule.uninstall())
        return
    hour, minute = (int(x) for x in args.at.split(":"))
    print(schedule.install(ws, hour, minute, weekdays=not args.every_day))


# --------------------------------------------------------------------------- main
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobseeker", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--home", help="workspace folder with config.toml (default: $JOBSEEKER_HOME or current folder)")
    p.add_argument("--version", action="version", version=f"jobseeker {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    s = sub.add_parser("init", help="create config.toml and profile.toml here")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("demo", help="open the web UI on a sample workspace")
    s.add_argument("--dir", help="where to create the demo workspace (default: a temp folder)")
    s.add_argument("--port", type=int, default=8766)
    s.add_argument("--no-open", action="store_true")

    s = sub.add_parser("run", help="fetch boards and write the digest of new matches")
    s.add_argument("--all", action="store_true", help="digest includes previously seen matches")
    s.add_argument("--track", help="limit to one scoring track")
    s.add_argument("--min-score", type=float)
    s.add_argument("--mode", help="work modes to keep, e.g. remote,hybrid (overrides config)")
    sub.add_parser("verify", help="check every configured board")
    s = sub.add_parser("add", help="find a company's job board and add it to config.toml")
    s.add_argument("query", help="company name, careers page URL, or a job posting URL")
    s.add_argument("--name", help="company name to save (default: guessed)")
    s.add_argument("--track", default="none", help="scoring track for the company (default: none)")
    s.add_argument("--pick", type=int, default=1, help="which found board to add (default: 1, the largest)")
    s.add_argument("--dry-run", action="store_true", help="only show what was found")

    s = sub.add_parser("list", help="numbered matches from the latest run")
    s.add_argument("--top", type=int, default=30)
    s = sub.add_parser("check", help="knock-out warnings for a job (posting + application questions)")
    s.add_argument("job", help="number from `list`, a posting URL, or an application folder")
    s = sub.add_parser("prep", help="fetch a job's form and auto-answer it")
    s.add_argument("job", help="number from `list`, a posting URL, or an application folder")
    s.add_argument("--force", action="store_true", help="re-fetch and overwrite form.toml")
    s = sub.add_parser("fill", help="pre-fill the form in a browser (never submits)")
    s.add_argument("job")
    s.add_argument("--headless", action="store_true", help="fill invisibly and save a screenshot (testing)")
    s.add_argument("--screenshot")
    s.add_argument("--no-prompt", action="store_true", help="don't ask whether you submitted (used by the UI)")
    s = sub.add_parser("cover", help="draft a cover letter or render it to PDF")
    s.add_argument("action", choices=("generate", "pdf"))
    s.add_argument("job")
    s.add_argument("--notes", default="", help="what to emphasize")
    s.add_argument("--length", choices=("short", "standard", "long"), default="standard")
    s = sub.add_parser("mark", help="set an application's status")
    s.add_argument("job")
    s.add_argument("status", choices=("prepared", "filled", "applied", "interviewing", "rejected", "offer",
                                      "withdrawn"))
    sub.add_parser("status", help="every application and its status")

    s = sub.add_parser("daily", help="run + auto-prep + notification (what the schedule runs)")
    s.add_argument("--no-notify", action="store_true")
    s = sub.add_parser("web", help="open the local web UI")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-open", action="store_true")
    s = sub.add_parser("schedule", help="run `daily` automatically (launchd on macOS, cron line elsewhere)")
    s.add_argument("action", choices=("install", "remove"))
    s.add_argument("--at", default="08:00", help="time of day, HH:MM (default 08:00)")
    s.add_argument("--every-day", action="store_true", help="include weekends")
    return p


COMMANDS = {"run": cmd_run, "verify": cmd_verify, "add": cmd_add, "list": cmd_list, "check": cmd_check, "prep": cmd_prep,
            "fill": cmd_fill, "cover": cmd_cover, "mark": cmd_mark, "status": cmd_status, "daily": cmd_daily,
            "web": cmd_web, "schedule": cmd_schedule}


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "demo":
            return cmd_demo(args)
        COMMANDS[args.cmd](Workspace.load(args.home), args)
    except JobSeekerError as e:
        sys.exit(f"jobseeker: {e}")
    except KeyboardInterrupt:
        sys.exit(130)
