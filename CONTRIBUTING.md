# Contributing

Thanks for helping! Issues and pull requests are welcome.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest && ruff check .
```

Tests run offline: `tests/conftest.py` replaces `jobradar.util.http_json` with fixtures from
`tests/fixtures/`. Please add a test with any behavior change.

To try the UI without touching your own data: `jobradar demo`.

## Project layout

```
jobradar/
  cli.py            the `jobradar` command
  workspace.py      where config and data live (Workspace, JobRadarError)
  sources/          one module per job board (see below)
  matching.py       title / location / work-mode filters and scoring
  radar.py          a run: fetch, filter, score, record, write digest
  applications.py   prep: resolve a job, fetch its form, auto-answer, form.toml I/O
  answers.py        profile.toml and the answer rules
  browser.py        Playwright helpers and `fill`
  coverletter.py    drafting and PDF rendering
  discover.py       find a company's board from a name or URL
  config_edit.py    validated, comment-preserving edits to config.toml
  daily.py, schedule.py   scheduled runs
  demo.py           the sample workspace
  web/              Flask app + single-page UI (static/index.html, no build step)
```

## Adding a job board

Create `jobradar/sources/<board>.py`:

```python
from jobradar import util
from jobradar.models import Job
from jobradar.sources import Source, register


def fetch_jobs(company, keep):
    """company: the [[companies]] table from config.toml. Return (jobs, raw_count)."""
    data = util.http_json(f"https://api.example-board.com/{company['slug']}/jobs")
    jobs = [Job(company["name"], company["track"], "<board>", str(p["id"]), p["title"],
                p["location"], p["url"], description=p.get("description", ""))
            for p in data["jobs"]]
    return jobs, len(data["jobs"])


register(Source("<board>", fetch_jobs))
```

Then import it at the bottom of `jobradar/sources/__init__.py`, add a fixture and a test in
`tests/test_sources.py`, and (optionally) a URL pattern in `discover.BOARD_PATTERNS` so
`jobradar add` can recognize the board.

- Set `Job.mode` (`remote`/`hybrid`/`onsite`) when the board says so; otherwise the matcher infers it.
- `keep(job, prelim=True)` lets slow boards skip detail requests for postings that will be filtered anyway (see `workday.py`).
- To support `prep`, add `fetch_form(job) -> (questions, meta)`. Questions are dicts with `key`, `label`, `type` (`text`, `textarea`, `select`, `multiselect`, `file`, `boolean`, `location`), `required` and `options`. `meta` holds `title`, `location`, `description` and `apply_url`.
- To support `fill`, add `fill(page, questions, profile) -> report` using the helpers in `browser.py`. **Never click Submit.**

## Ground rules

- No auto-submission, ever. Pull requests that add it won't be merged.
- Only use endpoints that the board's public job pages already use, and keep request volume low.
- Never commit personal data. `config.toml`, `profile.toml` and workspace data are gitignored; use `jobradar demo` data in screenshots.
