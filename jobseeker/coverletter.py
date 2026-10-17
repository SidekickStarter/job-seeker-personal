"""Cover letters for prepared applications.

generate: drafts <application>/cover_letter.md from the posting (job.md) and your resume. It runs
          the local `claude` CLI (Claude Code) with your own login, so no API key is needed; swap
          `draft_with_claude` for another model if you prefer.
to_pdf:   renders cover_letter.md to <Name>_Cover_Letter.pdf and sets it as the answer to the
          form's cover-letter upload, so `fill` attaches it.
"""
from __future__ import annotations

import datetime as dt
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from jobseeker.answers import load_profile
from jobseeker.applications import load_form, save_form
from jobseeker.util import safe
from jobseeker.workspace import JobSeekerError, Workspace

LENGTHS = {"short": "150-200 words, 2-3 short paragraphs",
           "standard": "250-350 words, 3-4 paragraphs",
           "long": "400-450 words, 4-5 paragraphs"}
COVER_RE = re.compile(r"cover\s*letter", re.I)


def resume_text(ws: Workspace, prof: dict) -> str:
    path = ws.home / Path(prof["me"]["resume"]).expanduser()      # relative paths: from the workspace
    if path.suffix.lower() != ".pdf":
        return path.read_text()
    import logging

    from pypdf import PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)   # harmless warnings about the PDF's structure
    return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)


def posting(folder):
    text = (folder / "job.md").read_text()
    m = re.search(r"## Description\s*\n(.*)", text, re.S)
    return (m.group(1) if m else text).strip()[:15000]


def build_prompt(job, description, resume, name, notes, length):
    return f"""Write a cover letter for {name}, applying for the role "{job['title'].strip()}" at {job['company']}.

<job_posting>
{description}
</job_posting>

<resume>
{resume}
</resume>

Requirements:
- Length: {LENGTHS[length]}.
- Use only facts that appear in the resume. Do not invent employers, projects, metrics, technologies, or
  experience. If the posting asks for a technology that isn't on the resume, don't claim it; lean on the
  closest real experience instead.
- Opening: why this specific role and team, using concrete details from the posting. No generic openers
  ("I am writing to express my interest", "I was thrilled to see"), no flattery.
- Middle: the two or three resume achievements that best match what the posting asks for, with their
  real numbers, each tied explicitly to a need in the posting.
- Close: one or two sentences, confident and brief.
- Tone: direct, specific, professional; first person; plain sentences, no buzzwords.
- Format: plain text, no markdown, no address block, no date, no subject line. Start with
  "Dear {job['company']} Hiring Team," and end with "Sincerely," then "{name}" on its own line.
{f'- Additional guidance from the candidate: {notes}' if notes.strip() else ''}

Output only the letter text, nothing before or after it."""


def draft_with_claude(prompt: str) -> str:
    """Run the prompt through the local `claude` CLI: no tools, no saved session, and outside any
    project folder so the draft isn't influenced by local files or memory."""
    if not shutil.which("claude"):
        raise JobSeekerError("Drafting uses the `claude` CLI (Claude Code), which isn't on PATH. Install it, "
                            "or write the letter yourself in cover_letter.md.")
    r = subprocess.run(["claude", "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"],
                       input=prompt, capture_output=True, text=True, timeout=600, cwd=tempfile.gettempdir())
    letter = r.stdout.strip()
    if r.returncode or not letter:
        raise JobSeekerError(f"Claude didn't return a letter: {(r.stderr or r.stdout).strip()[:300]}")
    return letter


def generate(ws: Workspace, folder: Path, notes: str = "", length: str = "standard", draft=draft_with_claude) -> str:
    form = load_form(folder)
    prof = load_profile(ws)
    me = prof["me"]
    name = f"{me['first_name']} {me['last_name']}"
    prompt = build_prompt(form["job"], posting(folder), resume_text(ws, prof), name, notes, length)
    print(f"Drafting a {length} cover letter for {form['job']['company']} — {form['job']['title'].strip()} …",
          flush=True)
    letter = draft(prompt)
    (folder / "cover_letter.md").write_text(letter + "\n")
    print(f"Saved {ws.rel(folder / 'cover_letter.md')} ({len(letter.split())} words)")
    return letter


PDF_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: Letter; margin: 0.9in 1in; }}
body {{ font: 11pt/1.5 Georgia, 'Times New Roman', serif; color: #111; }}
.head {{ border-bottom: 1px solid #999; padding-bottom: 8px; margin-bottom: 22px; }}
.name {{ font-size: 18pt; letter-spacing: .5px; }}
.contact {{ font-size: 9.5pt; color: #444; margin-top: 2px; }}
.date {{ margin-bottom: 18px; }}
p {{ margin: 0 0 11pt; }}
</style></head><body>
<div class="head"><div class="name">{name}</div><div class="contact">{contact}</div></div>
<div class="date">{date}</div>
{body}
</body></html>"""


def to_pdf(ws: Workspace, folder: Path) -> Path:
    letter = (folder / "cover_letter.md").read_text().strip()
    me = load_profile(ws)["me"]
    name = f"{me['first_name']} {me['last_name']}"
    contact = " · ".join(x for x in (f"{me.get('city', '')}, {me.get('state', '')}".strip(", "), me.get("phone"),
                                     me.get("email"), (me.get("linkedin") or "").replace("https://www.", "")) if x)
    paras = [p.strip() for p in re.split(r"\n\s*\n", letter) if p.strip()]
    body = "\n".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paras)
    page_html = PDF_HTML.format(name=html.escape(name), contact=html.escape(contact), body=body,
                                date=f"{dt.date.today():%B} {dt.date.today().day}, {dt.date.today():%Y}")
    out = folder / f"{safe(name).replace('-', '_').title()}_Cover_Letter.pdf"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(page_html)
        page.pdf(path=str(out), format="Letter")
        browser.close()
    attached = attach(folder, out, letter)
    print(f"Saved {ws.rel(out)}" + (f"; set as the answer to: {', '.join(attached)}" if attached else
                                         "; this form has no cover letter field (upload it yourself if needed)"))
    return out


def attach(folder, pdf, letter):
    """Point the form's cover-letter question(s) at the letter: the PDF for uploads, text for text boxes."""
    form = load_form(folder)
    qs, hit = form.get("q", []), []
    for q in qs:
        if COVER_RE.search(q["label"]) or q["key"] == "cover_letter":
            if q["type"] == "file":
                q["answer"] = str(pdf)
            elif q["type"] in ("textarea", "text"):
                q["answer"] = letter
            else:
                continue
            q["source"] = "edited"
            hit.append(q["label"])
    if hit:
        save_form(folder, form, qs)
    return hit
