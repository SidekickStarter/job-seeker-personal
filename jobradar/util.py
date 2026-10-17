"""Small shared helpers: HTTP, HTML cleanup, location and work-mode normalization."""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (job-radar; +https://github.com/SidekickStarter/job-radar)"
TIMEOUT = 25
TAG_RE = re.compile(r"<[^>]+>")


def http_json(url: str, payload: dict | None = None):
    """GET (or POST `payload` as JSON) and decode the JSON response."""
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def describe_error(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code} (slug/tenant probably wrong)" if e.code == 404 else f"HTTP {e.code}"
    return f"{type(e).__name__}: {str(e)[:120]}"


def join_locs(locs) -> str:
    out = []
    for loc in locs:
        if loc and loc not in out:
            out.append(loc)
    return " / ".join(out)


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(html.unescape(s))  # Greenhouse double-escapes content
    s = TAG_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_mode(v: str | None) -> str:
    """ATS workplaceType ('Remote', 'OnSite', 'on-site', 'Hybrid', ...) -> remote|hybrid|onsite|''."""
    v = re.sub(r"[\s_-]", "", (v or "").lower())
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "inoffice": "onsite"}.get(v, "")


def safe(s: str) -> str:
    """Filesystem-friendly slug."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]
