"""Find a company's job board from its name, careers page, or any posting link."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from jobseeker import util
from jobseeker.sources import SOURCES

BOARD_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("workday", r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)"),
]
SKIP_SLUGS = {"embed", "api", "v1", "jobs", "job_app", "boards"}
SLUG_BOARDS = ("greenhouse", "lever", "ashby")


def check_board(ats, slug=None, host=None, tenant=None, site=None):
    """Number of open postings on a board, or None if it doesn't exist."""
    try:
        if ats == "workday":
            d = util.http_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                               {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
            return d.get("total", 0)
        _, n = SOURCES[ats].fetch_jobs({"name": slug, "track": "", "slug": slug}, lambda *_: False)
        return n
    except Exception:
        return None


def boards_in(text: str) -> list[dict]:
    """Every job-board reference in a URL or an HTML page."""
    found = []
    for ats, pat in BOARD_PATTERNS:
        for m in re.finditer(pat, text):
            if ats == "workday":
                tenant, wd, site = m.groups()
                b = {"ats": ats, "host": f"{tenant}.{wd}.myworkdayjobs.com", "tenant": tenant, "site": site}
            elif m.group(1).lower() in SKIP_SLUGS:
                continue
            else:
                b = {"ats": ats, "slug": urllib.parse.unquote(m.group(1))}
            if b not in found:
                found.append(b)
    return found


def name_guesses(name: str) -> list[dict]:
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    dashed = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slugs = dict.fromkeys([base, dashed, base + "inc", base + "hq", base + "jobs", base + "careers"])
    return [{"ats": a, "slug": s} for s in slugs if s for a in SLUG_BOARDS]


def probe_all(candidates: list[dict]) -> list[dict]:
    """Candidates that exist, with their open-job counts, biggest first."""
    with ThreadPoolExecutor(max_workers=12) as ex:
        counts = list(ex.map(lambda b: check_board(**b), candidates))
    return sorted(({**b, "count": n} for b, n in zip(candidates, counts) if n is not None),
                  key=lambda b: -b["count"])


def detect(query: str) -> tuple[str, list[dict], str]:
    """(suggested company name, boards found, note) for a name, careers page, or posting link."""
    q = query.strip()
    is_url = bool(re.match(r"https?://", q))
    candidates = boards_in(q)
    if candidates:          # a job-board or posting link: name it after the board
        b = candidates[0]
        name = (b.get("slug") or b.get("tenant") or "").replace("-", " ").title()
    elif is_url:            # a company careers page: look for the board it embeds or links to
        host = re.sub(r"^(www|careers|jobs)\.", "", q.split("/")[2])
        name = host.split(".")[0].capitalize()
        try:
            req = urllib.request.Request(q, headers={"User-Agent": util.UA})
            page = urllib.request.urlopen(req, timeout=15).read(3_000_000).decode("utf-8", "replace")
            candidates = boards_in(page)
        except Exception:
            pass
    else:
        name = q
    found = probe_all(candidates) if candidates else []
    if not found:           # fall back to guessing board names from the company name
        found = probe_all(name_guesses(name))
    note = "" if found else ("Couldn't find a Greenhouse, Lever, Ashby or Workday board. Paste a link to one "
                             "of the company's job postings (the Apply page) and try again.")
    return name, found, note
