"""Greenhouse: public job board API (boards-api.greenhouse.io); forms pre-filled via the embed page."""
from __future__ import annotations

from jobradar import util
from jobradar.answers import DECLINE, pick_option
from jobradar.browser import NEAR_TEXT_JS, as_list, combo_pick, fill_file, location_pick
from jobradar.models import Job
from jobradar.sources import Source, register
from jobradar.util import strip_html


def fetch_jobs(c, keep):
    d = util.http_json(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    raw = d.get("jobs", [])
    jobs = [
        Job(c["name"], c["track"], "greenhouse", str(j["id"]), j.get("title", ""),
            (j.get("location") or {}).get("name", ""), j.get("absolute_url", ""),
            (j.get("updated_at") or "")[:10], strip_html(j.get("content")))
        for j in raw
    ]
    return jobs, len(raw)


GH_TYPES = {"input_text": "text", "textarea": "textarea", "input_file": "file",
            "multi_value_single_select": "select", "multi_value_multi_select": "multiselect"}


def fetch_form(job):
    d = util.http_json(f"https://boards-api.greenhouse.io/v1/boards/{job['slug']}/jobs/{job['job_id']}?questions=true")
    raw = list(d.get("questions") or []) + list(d.get("location_questions") or [])
    for comp in d.get("compliance") or []:
        raw += comp.get("questions") or []
    qs = []
    for q in raw:
        f = (q.get("fields") or [{}])[0]
        if f.get("type") not in GH_TYPES:
            continue
        qs.append({"key": f["name"], "label": strip_html(q.get("label") or f["name"]),
                   "type": "location" if f["name"] == "location" else GH_TYPES[f["type"]],
                   "required": bool(q.get("required")),
                   "options": [v["label"] for v in f.get("values") or []]})
    meta = {"title": d.get("title", ""), "location": (d.get("location") or {}).get("name", ""),
            "description": strip_html(d.get("content")),
            "apply_url": f"https://job-boards.greenhouse.io/embed/job_app?for={job['slug']}&token={job['job_id']}"}
    return qs, meta


def fill(page, qs, prof):
    report = []
    for q in qs:
        ans = q.get("answer")
        if ans in ("", [], None):
            report.append((q, "skip" if not q["required"] else "missing", ""))
            continue
        try:
            if q["type"] == "location" or q["key"] == "location":
                picked = location_pick(page, page.locator("#candidate-location").first, prof)
                report.append((q, "ok", f"picked {picked}"))
                continue
            el = page.locator(f'[id="{q["key"]}"]').first
            if not el.count():
                if not q["required"]:  # conditional field (e.g. Race only after Hispanic = No)
                    report.append((q, "skip", "not shown on this form"))
                    continue
                raise ValueError("field not on page")
            if q["type"] in ("text", "textarea"):
                el.fill(str(ans))
                ok = el.input_value() == str(ans)
            elif q["type"] == "file":
                fill_file(el, ans)
                page.wait_for_timeout(800)
                ok = True
            else:
                for v in as_list(ans):
                    combo_pick(page, el, v)
                ok = all(el.evaluate(NEAR_TEXT_JS, v) for v in as_list(ans))
            report.append((q, "ok" if ok else "check", ""))
        except Exception as e:
            report.append((q, "fail", str(e).splitlines()[0][:80]))
    if prof.get("eeo", {}).get("policy") == "decline":
        gh_decline_eeo(page, {q["key"] for q in qs})
    # Country picker that sits next to the phone field on newer Greenhouse forms
    country = page.locator("#country")
    if country.count() and not country.input_value():
        try:
            combo_pick(page, country, prof.get("me", {}).get("country", "United States"))
        except Exception:
            pass
    return report


def gh_decline_eeo(page, done):
    """EEO dropdowns rendered on the page but missing from the API (e.g. hispanic_ethnicity)."""
    for fid in ("gender", "hispanic_ethnicity", "race", "veteran_status", "disability_status"):
        el = page.locator(f'[id="{fid}"]')
        if fid in done or not el.count():
            continue
        try:
            el.click()
            page.wait_for_timeout(300)
            hit = pick_option(page.get_by_role("option").all_inner_texts(), DECLINE)
            if hit:
                page.get_by_role("option", name=hit, exact=True).first.click()
            else:
                el.press("Escape")
        except Exception:
            pass


register(Source("greenhouse", fetch_jobs, fetch_form, fill))
