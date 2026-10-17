"""Workday: the career site's search API (<tenant>.wdN.myworkdayjobs.com/wday/cxs/...).

Undocumented and large boards are slow, so each company lists `search` terms and `max_results`.
Listings only; forms aren't pre-filled."""
from __future__ import annotations

from jobradar import util
from jobradar.models import Job
from jobradar.sources import Source, register
from jobradar.util import join_locs, strip_html


def fetch_jobs(c, keep):
    host, tenant, site = c["host"], c["tenant"], c["site"]
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    cap = int(c.get("max_results", 200))
    jobs, seen, raw_count = [], set(), 0
    for query in c.get("search", ["software engineer"]):
        offset, total = 0, None
        while True:
            d = util.http_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20,
                                            "offset": offset, "searchText": query})
            if total is None:
                total = d.get("total", 0)  # only reported on the first page
                raw_count += total
            posts = d.get("jobPostings", [])
            for p in posts:
                path = p.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                job = Job(c["name"], c["track"], "workday", path.rsplit("_", 1)[-1],
                          p.get("title", ""), p.get("locationsText", ""),
                          f"https://{host}/en-US/{site}{path}", p.get("postedOn", ""))
                if not keep(job, True):
                    continue
                try:  # detail call only for survivors of the cheap filter
                    info = util.http_json(f"{base}{path}").get("jobPostingInfo", {})
                    job.description = strip_html(info.get("jobDescription"))
                    locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                    job.location = join_locs(locs) or job.location
                    job.posted = info.get("startDate") or job.posted
                except Exception:
                    pass
                jobs.append(job)
            offset += 20
            if not posts or offset >= min(total, cap):
                break
    return jobs, raw_count


register(Source("workday", fetch_jobs))
