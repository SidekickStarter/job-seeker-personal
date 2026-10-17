"""A sample workspace (fictional companies and candidate) for trying the UI without any setup.

`jobseeker demo` builds it in a temp folder and opens the web UI. "Run now" in the demo fetches
the real example companies from config.example.toml.
"""
from __future__ import annotations

import datetime as dt
import shutil
import tempfile
from pathlib import Path

from jobseeker import knockout
from jobseeker.answers import load_profile
from jobseeker.applications import write_form
from jobseeker.models import Job
from jobseeker.radar import write_csv
from jobseeker.store import open_db, set_status
from jobseeker.workspace import Workspace

TEMPLATES = Path(__file__).parent / "templates"

# (company, source, id, title, location, mode, score, track, why, days_ago_found)
POSTINGS = [
    ("Lumen Pay", "greenhouse", "4410021", "Staff Software Engineer, Ledger Platform", "Remote - US", "remote",
     14.6, "payments", "Payments / fintech | title: ledger | desc: payment, ledger, transaction, risk | stack: java, kafka", 0),
    ("Cobalt Data", "ashby", "c0ba17-0001", "Senior Software Engineer, Streaming Ingestion", "New York, NY", "hybrid",
     13.9, "data_platform", "Data platform / streaming | title: streaming, ingestion | desc: kafka, flink, iceberg | stack: python, kafka", 0),
    ("Northwind Freight", "greenhouse", "5520017", "Senior Backend Engineer, Shipment Events", "Remote - United States",
     "remote", 11.8, "backend", "Backend / distributed systems | desc: event-driven, microservice, latency | stack: java, aws", 0),
    ("Brightloop", "greenhouse", "7781002", "Principal Engineer, Data Lakehouse", "New York, NY; San Francisco, CA",
     "hybrid", 12.4, "data_platform", "Data platform / streaming | title: lakehouse | desc: spark, airflow, iceberg | stack: python", 1),
    ("Lumen Pay", "greenhouse", "4410388", "Senior Software Engineer, Fraud Signals", "Remote - US", "remote",
     10.9, "payments", "Payments / fintech | title: fraud | desc: risk, transaction, streaming | stack: python, kafka", 1),
    ("Harbor Health", "lever", "hh-2231", "Senior Software Engineer, Claims Platform", "Jersey City, NJ", "onsite",
     8.7, "backend", "Backend / distributed systems | desc: api, scalab, high availability | stack: java", 1),
    ("Quill Labs", "ashby", "q0111-0042", "Founding Backend Engineer", "Remote (US)", "remote",
     7.9, "backend", "Backend / distributed systems | desc: api, backend, distributed system | stack: python, aws", 3),
    ("Cobalt Data", "ashby", "c0ba17-0007", "Staff Engineer, Query Engine", "Remote - US", "remote",
     12.1, "data_platform", "Data platform / streaming | desc: warehouse, petabyte, distributed system | stack: java", 3),
    ("Northwind Freight", "greenhouse", "5520101", "Staff Software Engineer, Capacity Planning", "Hoboken, NJ", "hybrid",
     11.2, "backend", "Backend / distributed systems | desc: scalab, throughput, microservice | stack: java, aws", 3),
    ("Brightloop", "greenhouse", "7781066", "Senior Software Engineer, Platform APIs", "Remote - US", "remote",
     9.4, "backend", "Backend / distributed systems | title: api | desc: microservice, latency | stack: python", 6),
    ("Harbor Health", "lever", "hh-2260", "Senior Data Engineer, Interoperability", "Remote - US", "remote",
     8.1, "data_platform", "Data platform / streaming | desc: data pipeline, etl, batch | stack: python, aws", 6),
    ("Quill Labs", "ashby", "q0111-0050", "Senior Software Engineer, Billing", "New York, NY", "hybrid",
     9.8, "payments", "Payments / fintech | title: billing | desc: payment, transaction | stack: python", 6),
    ("Cobalt Data", "ashby", "c0ba17-0012", "Senior Software Engineer, Federal Cloud", "Remote - US", "remote",
     10.4, "data_platform", "Data platform / streaming | desc: streaming, data pipeline | stack: java, aws", 1),
]

# Snippets of posting text for a few jobs, run through the real knock-out checks (knockout.py).
DESCRIPTIONS = {
    "c0ba17-0012": "This role requires an active Secret clearance and U.S. citizenship.",
    "7781002": "You have 15+ years of experience building data platforms at scale.",
    "4410388": "The base salary range for this role is $140,000 - $165,000.",
}

DESCRIPTION = """Lumen Pay moves money for 40,000 small businesses. The Ledger Platform team owns the
double-entry ledger every payment, refund and payout is recorded in: about 30 million entries a day,
with correctness guarantees our finance and compliance partners depend on.

What you'll do: lead the design of the next-generation ledger (multi-currency, real-time balances),
own reliability for a tier-0 service, partner with Risk and Finance Engineering, and mentor engineers
across the payments org.

What we look for: 8+ years building distributed systems in Java, Kotlin or Python; experience with
event-driven architectures (Kafka or similar); strong data modeling and correctness instincts;
experience leading cross-team technical projects. Payments or fintech experience is a plus."""

QUESTIONS = [
    ("first_name", "First Name", "text", True, [], "Alex", "profile"),
    ("last_name", "Last Name", "text", True, [], "Rivera", "profile"),
    ("email", "Email", "text", True, [], "alex.rivera@example.com", "profile"),
    ("phone", "Phone", "text", True, [], "(555) 010-2030", "profile"),
    ("resume", "Resume/CV", "file", True, [], "resume.txt", "profile"),
    ("cover_letter", "Cover Letter", "file", False, [], "", "skip"),
    ("question_1", "LinkedIn Profile", "text", False, [], "https://www.linkedin.com/in/example", "profile"),
    ("question_2", "Are you legally authorized to work in the United States?", "select", True, ["Yes", "No"], "Yes", "profile"),
    ("question_3", "Will you now or in the future require visa sponsorship?", "select", True, ["Yes", "No"], "No", "profile"),
    ("question_4", "What are your salary expectations?", "text", False, [], "$190,000 - $230,000", "profile"),
    ("question_5", "Tell us about a system you designed where correctness mattered more than speed.", "textarea",
     True, [], "", "TODO"),
    ("question_6", "How did you hear about this job?", "select", False,
     ["LinkedIn", "Company Website", "Referral", "Other"], "LinkedIn", "profile"),
]

COVER = """Dear Lumen Pay Hiring Team,

A ledger that 40,000 businesses trust with every payout is exactly the kind of system I like to own:
one where correctness is the product. ...

(Sample letter: in your own workspace, "Generate with Claude" drafts one from the posting and your resume.)

Sincerely,
Alex Rivera"""

REFERRAL = """# Referral: Lumen Pay, Staff Software Engineer, Ledger Platform

- Req ID: R-1042 · Posting: 4410021

## Copy/paste paragraph

Alex Rivera is a senior backend engineer with 10 years of experience building payment and event-driven
systems. ... (Sample note: write yours here, or ask an assistant to draft one from the posting.)
"""

CONNECTIONS = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Priya,Shah,https://example.com/in/priya-shah,,Lumen Pay,"Senior Software Engineer, Ledger Platform",14 Mar 2022
Marcus,Lee,https://example.com/in/marcus-lee,,"Lumen Pay, Inc.","Engineering Manager, Ledger & Settlement",02 Sep 2024
Dana,Kim,https://example.com/in/dana-kim,,Lumen Pay,Technical Recruiter,19 Jan 2026
Tom,Alvarez,https://example.com/in/tom-alvarez,,Lumen Pay,Product Designer,30 Jun 2019
Wei,Zhang,https://example.com/in/wei-zhang,,Cobalt Data,Staff Engineer,11 Nov 2021
Sara,Okafor,https://example.com/in/sara-okafor,,Northwind Freight,VP of Engineering,05 May 2023
Leo,Brandt,https://example.com/in/leo-brandt,,Initech,Software Engineer,08 Aug 2018
"""

CONTACT_TAGS = """# How well you know people (used to rank referral likelihood). Private: gitignored.
[[contact]]
name = "Priya Shah"
url = "https://example.com/in/priya-shah"
closeness = "worked together"
note = "Globex 2019-2021, same team"

[[contact]]
name = "Wei Zhang"
closeness = "know a bit"
"""

RESUME = """Alex Rivera — Senior Software Engineer (sample resume for the Job Seeker demo)
Experience: 10 years building payment ledgers, event-driven services (Java, Kafka, AWS) and data pipelines.
"""


def build_demo(target: Path | None = None) -> Workspace:
    home = Path(target or tempfile.mkdtemp(prefix="jobseeker-demo-")).resolve()
    if home.exists() and (home / "config.toml").exists():
        shutil.rmtree(home)
    home.mkdir(parents=True, exist_ok=True)
    shutil.copy(TEMPLATES / "config.example.toml", home / "config.toml")
    (home / "resume.txt").write_text(RESUME)
    profile = (TEMPLATES / "profile.example.toml").read_text()
    for a, b in (('"First"', '"Alex"'), ('"Last"', '"Rivera"'), ("you@example.com", "alex.rivera@example.com"),
                 ("(555) 555-5555", "(555) 010-2030"), ('city = ""', 'city = "Brooklyn"'),
                 ('state = ""', 'state = "New York"'), ("/path/to/Resume.pdf", "resume.txt")):
        profile = profile.replace(a, b, 1)
    profile = profile.replace('years_experience = ""', 'years_experience = "10"', 1)
    profile = profile.replace('degree = ""', 'degree = "Bachelor\'s"', 1)
    profile = profile.replace('current_company = ""', 'current_company = "Globex"', 1)
    profile = profile.replace('current_title = ""', 'current_title = "Senior Software Engineer"', 1)
    profile = profile.replace('past_employers = []', 'past_employers = ["Initech"]', 1)
    (home / "profile.toml").write_text(profile)
    ws = Workspace.load(home)
    prof = load_profile(ws)

    today = dt.date.today()
    jobs, seen = [], []
    for company, source, jid, title, loc, mode, score, track, why, ago in POSTINGS:
        j = Job(company, track, source, jid, title, loc, f"https://example.com/{source}/{jid}",
                (today - dt.timedelta(days=ago + 1)).isoformat(), score=score, best_track=track, why=why,
                mode=mode, mode_src="listed", description=DESCRIPTIONS.get(jid, ""))
        j.flags = knockout.dumps(knockout.check_posting(j.description, prof))
        jobs.append(j)
        seen.append((j.key, (today - dt.timedelta(days=ago)).isoformat(), company, title, j.url, score, track))
    jobs.sort(key=lambda j: -j.score)
    ws.digests_dir.mkdir(exist_ok=True)
    new_today = {s[0] for s in seen if s[1] == today.isoformat()}
    write_csv(ws.matches_csv, jobs, {j.key for j in jobs} - new_today)
    db = open_db(ws)
    db.executemany("insert or replace into seen values (?,?,?,?,?,?,?)", seen)
    db.commit()
    (ws.digests_dir / f"digest_{today}.md").write_text(
        f"# Job Seeker — {today:%a %b %d, %Y}\n\nScanned 2,184 postings · {len(jobs)} matches · "
        f"**{len(new_today)} new**\n\n_(Sample digest. Click Run now to fetch real boards.)_\n")

    # One prepared application with a TODO, a cover letter and a referral note; two tracked ones.
    top = next(j for j in jobs if j.job_id == "4410021")
    job = {"key": top.key, "source": top.source, "company": top.company, "slug": "", "job_id": top.job_id,
           "title": top.title, "url": top.url, "location": top.location, "mode": top.mode, "score": str(top.score)}
    folder = ws.apps_dir / f"{today}-lumen-pay-staff-software-engineer-ledger-4410021"
    folder.mkdir(parents=True)
    (folder / "job.md").write_text(f"# {top.title} — {top.company}\n\n- Posting: {top.url}\n\n## Description\n\n{DESCRIPTION}\n")
    qs = [{"key": k, "label": lab, "type": t, "required": req, "options": opts,
           "answer": ans, "source": src}
          for k, lab, t, req, opts, ans, src in QUESTIONS]
    write_form(folder / "form.toml", job, {"title": top.title, "apply_url": top.url}, qs)
    (folder / "cover_letter.md").write_text(COVER + "\n")
    (folder / "referral.md").write_text(REFERRAL)
    set_status(ws, job, folder, "prepared")
    for jid, status in (("c0ba17-0007", "applied"), ("5520101", "interviewing")):
        j = next(x for x in jobs if x.job_id == jid)
        set_status(ws, {"key": j.key, "company": j.company, "title": j.title, "url": j.url}, None, status)

    ws.network_dir.mkdir(exist_ok=True)
    (ws.network_dir / "Connections.csv").write_text(CONNECTIONS)
    ws.contacts_path.write_text(CONTACT_TAGS)
    ws.notes_dir.mkdir(exist_ok=True)
    (ws.notes_dir / "intro.md").write_text("# Referral intro\n\nHi [Name], I'm a senior backend engineer ... "
                                           "(Sample note: keep reusable outreach templates here.)\n")
    ws.logs_dir.mkdir(exist_ok=True)
    (ws.logs_dir / "daily.log").write_text(
        f"=== {today} 08:00 daily run\nScanned 2184 postings from 7 companies; {len(jobs)} matches, "
        f"{len(new_today)} new. Errors: 0\nSummary: {len(new_today)} new matches\n")
    return ws
