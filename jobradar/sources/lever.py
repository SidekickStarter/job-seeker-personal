"""Lever: public postings API (api.lever.co). Listings only; forms aren't pre-filled yet."""
from __future__ import annotations

import datetime as dt

from jobradar import util
from jobradar.models import Job
from jobradar.sources import Source, register
from jobradar.util import join_locs, norm_mode, strip_html


def fetch_jobs(c, keep):
    raw = util.http_json(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json")
    if not isinstance(raw, list):
        raise ValueError(f"unexpected Lever response: {str(raw)[:120]}")
    jobs = []
    for j in raw:
        cat = j.get("categories") or {}
        locs = list(cat.get("allLocations") or [cat.get("location") or ""])
        mode = norm_mode(j.get("workplaceType"))
        if mode == "remote":
            locs.append("Remote")
        desc = " ".join([
            j.get("descriptionPlain") or "",
            " ".join(strip_html(item.get("content")) for item in j.get("lists") or []),
            j.get("additionalPlain") or "",
        ])
        posted = ""
        if j.get("createdAt"):
            posted = dt.datetime.fromtimestamp(j["createdAt"] / 1000, dt.UTC).date().isoformat()
        jobs.append(Job(c["name"], c["track"], "lever", j.get("id", ""), j.get("text", ""),
                        join_locs(locs), j.get("hostedUrl", ""), posted, desc,
                        mode=mode, mode_src="listed" if mode else ""))
    return jobs, len(raw)


register(Source("lever", fetch_jobs))
