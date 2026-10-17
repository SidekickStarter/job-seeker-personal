"""Ashby: public posting API for listings; the hosted page's GraphQL endpoint for form questions.

The form endpoint (jobs.ashbyhq.com/api/non-user-graphql) is what Ashby's own job pages call. It is
undocumented, so treat form prep for Ashby as best-effort."""
from __future__ import annotations

import re

from jobseeker import util
from jobseeker.browser import MARK_JS, as_list, click_labeled, combo_pick, fill_file, location_pick
from jobseeker.models import Job
from jobseeker.sources import Source, register
from jobseeker.util import join_locs, norm_mode, strip_html


def fetch_jobs(c, keep):
    d = util.http_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}")
    raw = d.get("jobs", [])
    jobs = []
    for j in raw:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        # Ashby sets isRemote on Hybrid postings too, so trust workplaceType first
        mode = norm_mode(j.get("workplaceType"))
        if not mode and j.get("isRemote"):
            mode = "remote"
        if mode == "remote":
            locs.append("Remote")
        jobs.append(Job(c["name"], c["track"], "ashby", j.get("id", ""), j.get("title", ""),
                        join_locs(locs), j.get("jobUrl", ""),
                        (j.get("publishedAt") or "")[:10],
                        j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                        mode=mode, mode_src="listed" if mode else ""))
    return jobs, len(raw)


ASHBY_TYPES = {"String": "text", "Email": "text", "Phone": "text", "Number": "text", "Date": "text",
               "SocialLink": "text", "LongText": "textarea", "File": "file", "ValueSelect": "select",
               "MultiValueSelect": "multiselect", "Boolean": "boolean", "Location": "location"}
ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title locationName applicationForm { sections { fieldEntries {
      ... on FormFieldEntry { id field isRequired } } } } } }"""


def fetch_form(job):
    org, jid = job["slug"], job["job_id"]
    r = util.http_json("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
                     {"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
                      "variables": {"organizationHostedJobsPageName": org, "jobPostingId": jid}})
    post = (r.get("data") or {}).get("jobPosting")
    if not post:
        raise ValueError(f"Ashby returned no posting: {str(r.get('errors'))[:200]}")
    qs = []
    for sec in post["applicationForm"]["sections"]:
        for e in sec["fieldEntries"]:
            f = e.get("field") or {}
            if f.get("type") not in ASHBY_TYPES:
                continue
            opts = [v.get("label", "") for v in f.get("selectableValues") or []]
            if f["type"] == "Boolean":
                opts = ["Yes", "No"]
            qs.append({"key": f["path"], "label": f.get("title", f["path"]), "type": ASHBY_TYPES[f["type"]],
                       "required": bool(e.get("isRequired")), "options": opts, "ashby_type": f["type"]})
    board = util.http_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}").get("jobs", [])
    b = next((j for j in board if j.get("id") == jid), {})
    meta = {"title": post.get("title", ""), "location": post.get("locationName", ""),
            "description": b.get("descriptionPlain") or strip_html(b.get("descriptionHtml")),
            "apply_url": f"https://jobs.ashbyhq.com/{org}/{jid}/application"}
    return qs, meta


def fill(page, qs, prof):
    report = []
    for i, q in enumerate(qs):
        ans = q.get("answer")
        if ans in ("", [], None):
            report.append((q, "skip" if not q["required"] else "missing", ""))
            continue
        key, tag = q["key"], f"q{i}"
        try:
            if q["type"] in ("text", "textarea"):
                el = page.locator(f'[name="{key}"], [id="{key}"]').first
                el.fill(str(ans))
                ok = el.input_value() == str(ans)
            elif q["type"] == "file":
                el = page.locator(f'input[type=file][id="{key}"]').first
                if not el.count() and page.evaluate(MARK_JS, [q["label"], "input[type=file]", tag]):
                    el = page.locator(f'[data-jr="{tag}"] input[type=file]').first
                fill_file(el, ans)
                page.wait_for_timeout(1500)
                ok = True
            elif q["type"] == "location":
                el = page.locator(f'[name="{key}"], [id="{key}"]').first
                if not el.count() and page.evaluate(MARK_JS, [q["label"], "input", tag]):
                    el = page.locator(f'[data-jr="{tag}"] input').first
                report.append((q, "ok", f"picked {location_pick(page, el, prof)}"))
                continue
            elif q["type"] == "boolean":
                if not page.evaluate(MARK_JS, [q["label"], "button", tag]):
                    raise ValueError("question not found on page")
                page.locator(f'[data-jr="{tag}"] button').filter(
                    has_text=re.compile(rf"^\s*{re.escape(str(ans))}\s*$", re.I)).first.click()
                ok = True
            elif q["type"] == "select":
                ok = ashby_choose(page, q, [str(ans)], tag)
            else:  # multiselect
                ok = ashby_choose(page, q, as_list(ans), tag)
            report.append((q, "ok" if ok else "check", ""))
        except Exception as e:
            report.append((q, "fail", str(e).splitlines()[0][:80]))
    if prof.get("eeo", {}).get("policy") == "decline":
        ashby_decline_eeo(page)
    return report


def ashby_choose(page, q, values, tag):
    """Ashby renders selects as radios, checkboxes, or a searchable combobox."""
    radios = page.locator(f'input[type=radio][name$="{q["key"]}"]')
    if radios.count():
        for v in values:
            if not click_labeled(radios, v):
                raise ValueError(f"option {v!r} not offered")
        return True
    if not page.evaluate(MARK_JS, [q["label"], "input[role=combobox], input[type=checkbox]", tag]):
        raise ValueError("question not found on page")
    box = page.locator(f'[data-jr="{tag}"]')
    combo = box.locator("input[role=combobox]")
    if combo.count():
        for v in values:
            combo_pick(page, combo.first, v)
        return all(box.evaluate("(e, v) => e.innerText.toLowerCase().includes(v.toLowerCase())", v) for v in values)
    for v in values:
        if not click_labeled(box.locator("input[type=checkbox]"), v):
            raise ValueError(f"option {v!r} not offered")
    return True


def ashby_decline_eeo(page):
    names = page.locator('input[type=radio][name*="_systemfield_eeoc_"]').evaluate_all(
        "els => [...new Set(els.map(e => e.name))]")
    for name in names:
        group = page.locator(f'input[type=radio][name="{name}"]')
        try:
            click_labeled(group, "decline") or click_labeled(group, "not wish")
        except Exception:
            pass


register(Source("ashby", fetch_jobs, fetch_form, fill))
