"""Playwright helpers shared by the board-specific form fillers, and the `fill` entry point.

Nothing here clicks Submit: the browser is left open for you to review and submit yourself."""
from __future__ import annotations

from pathlib import Path

from jobseeker.answers import pick_option

BROWSER_DIR = Path.home() / ".jobseeker" / "browser"   # keeps your logins/cookies between runs

# Finds the smallest element whose text starts with the question label, walks up to the
# nearest ancestor containing `sel`, and tags it data-jr=<tag> so Playwright can target it.
MARK_JS = """([label, sel, tag]) => {
  const norm = s => (s || '').replace(/\\*/g, '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const want = norm(label).slice(0, 60);
  if (!want) return false;
  const cands = [...document.querySelectorAll('label, legend, p, span, div, h3, h4')]
    .filter(e => { const t = norm(e.innerText); return t && t.length < 800 && t.startsWith(want); })
    .sort((a, b) => a.innerText.length - b.innerText.length);
  for (const el of cands) {
    let n = el;
    for (let i = 0; i < 7 && n; i++, n = n.parentElement) {
      if (n.querySelector(sel)) { n.setAttribute('data-jr', tag); return true; }
    }
  }
  return false;
}"""

NEAR_TEXT_JS = """(e, v) => { let n = e; v = v.toLowerCase();
  for (let i = 0; i < 4 && n; i++, n = n.parentElement)
    if ((n.innerText || '').toLowerCase().includes(v)) return true;
  return false; }"""


def as_list(ans):
    return ans if isinstance(ans, list) else [ans]


def combo_pick(page, el, value):
    el.click()
    el.press_sequentially(value[:40], delay=15)
    page.wait_for_timeout(400)
    opts = page.get_by_role("option")
    exact = page.get_by_role("option", name=value, exact=True)
    target = exact.first if exact.count() else opts.filter(has_text=value).first
    if not target.count():
        el.press("Escape")
        raise ValueError(f"option {value!r} not offered")
    target.click()
    page.wait_for_timeout(200)


def location_pick(page, el, prof):
    """Location autocompletes word towns differently ("Springfield Township" vs "Springfield"), so try each
    search in [me].location_search and accept only a suggestion naming your town and state."""
    me = prof.get("me", {})
    city, state = me.get("city", ""), me.get("state", "")
    searches = me.get("location_search") or [f"{city}, {state}", city]
    for text in searches:
        el.click()
        el.fill("")
        el.press_sequentially(text, delay=20)
        page.wait_for_timeout(1800)
        town = text.split(",")[0].strip().lower()
        for opt in page.get_by_role("option").all_inner_texts():
            o = opt.lower()
            if o.startswith(town) and (not state or state.lower() in o):
                page.get_by_role("option", name=opt, exact=True).first.click()
                page.wait_for_timeout(300)
                return opt
    el.fill("")
    raise ValueError(f"no suggestion matched {city}, {state}; tried {searches}")


def fill_file(loc, path):
    path = Path(path).expanduser()
    if not path.exists():
        raise ValueError(f"file not found: {path}")
    loc.set_input_files(str(path))


def click_labeled(inputs, value):
    """Click the <label> of the input whose label text matches value."""
    labels = inputs.evaluate_all("els => els.map(e => (e.labels && e.labels[0]) ? e.labels[0].innerText.trim() : '')")
    hit = pick_option(labels, [value])
    if hit is None:
        return False
    i = labels.index(hit)
    inp = inputs.nth(i)
    lab_for = inp.get_attribute("id")
    if lab_for:
        inputs.page.locator(f'label[for="{lab_for}"]').first.click()
    else:
        inp.check(force=True)
    return True


def print_report(report):
    marks = {"ok": "✓", "check": "?", "fail": "✗", "missing": "!", "skip": "-"}
    print()
    for q, status, note in report:
        ans = q.get("answer")
        shown = ", ".join(as_list(ans)) if isinstance(ans, list) else str(ans or "")
        shown = Path(shown).name if q["type"] == "file" else shown.replace("\n", " ")
        print(f"  {marks[status]} {q['label'][:58]:<58} {shown[:40]}{'  — ' + note if note else ''}")
    n = {s: sum(r[1] == s for r in report) for s in marks}
    print(f"\nFilled {n['ok']}, check {n['check']}, failed {n['fail']}, "
          f"required but unanswered {n['missing']}, skipped {n['skip']}.")


def mark_unfilled(page, report):
    for i, (q, status, _) in enumerate(report):
        if status in ("fail", "missing", "check"):
            try:
                if page.evaluate(MARK_JS, [q["label"], "input, textarea, button", f"x{i}"]):
                    page.locator(f'[data-jr="x{i}"]').first.evaluate(
                        "e => { e.style.outline = '3px solid #e5484d'; e.style.outlineOffset = '4px'; }")
            except Exception:
                pass


def fill_job(ws, job: dict, folder: Path, prof: dict, headless: bool = False, screenshot: str | None = None):
    """Open the application form and pre-fill it from form.toml. Headed: waits until you close the
    window (you review and submit). Headless: saves a screenshot instead (for testing)."""
    from jobseeker.applications import load_form
    from jobseeker.sources import SOURCES
    from jobseeker.store import set_status
    from jobseeker.workspace import JobSeekerError

    source = SOURCES.get(job["source"])
    if not source or not source.fill:
        raise JobSeekerError(f"{job['source']} forms can't be pre-filled; apply at {job['url']}")
    form = load_form(folder)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise JobSeekerError("`fill` needs Playwright: pip install 'job-seeker[fill]' && playwright install chromium") from None

    with sync_playwright() as p:
        if headless:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
        else:
            old = Path.home() / ".job_radar" / "browser"     # pre-rename location: keep your logins
            if old.exists() and not BROWSER_DIR.exists():
                BROWSER_DIR.parent.mkdir(parents=True, exist_ok=True)
                old.rename(BROWSER_DIR)
            BROWSER_DIR.mkdir(parents=True, exist_ok=True)
            try:   # your installed Chrome if there is one, else Playwright's Chromium
                ctx = p.chromium.launch_persistent_context(BROWSER_DIR, headless=False, channel="chrome",
                                                           no_viewport=True)
            except Exception:
                ctx = p.chromium.launch_persistent_context(BROWSER_DIR, headless=False, no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(form["apply_url"], wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        qs = form.get("q", [])
        for q in qs:   # file answers may be relative to the workspace (e.g. resume = "resume.pdf")
            if q["type"] == "file" and isinstance(q.get("answer"), str) and q["answer"]:
                q["answer"] = str(ws.home / Path(q["answer"]).expanduser())
        report = source.fill(page, qs, prof)
        print_report(report)
        mark_unfilled(page, report)

        if headless:
            shot = screenshot or str(folder / "filled.png")
            page.screenshot(path=shot, full_page=True)
            print(f"Screenshot: {shot}")
            ctx.close()
            return report
        set_status(ws, job, folder, "filled")
        print("\nReview the form in the browser (unfilled required fields are outlined in red),"
              "\nthen click Submit yourself. Close the browser window when you're done.", flush=True)
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass
    return report
