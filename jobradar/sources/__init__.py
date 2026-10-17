"""Job board adapters. Each board registers a Source; add a new board by writing a module that
calls `register(Source(...))` and importing it at the bottom of this file.

    fetch_jobs(company, keep) -> (jobs, raw_count)
        company: the [[companies]] table from config (name, track, slug or tenant fields...).
        keep(job, prelim): lets slow sources skip detail calls for postings that will be filtered.
    fetch_form(job) -> (questions, meta)        optional: enables `prep`
    fill(page, questions, profile) -> report    optional: enables `fill` (Playwright page)
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class Source:
    name: str
    fetch_jobs: Callable
    fetch_form: Callable | None = None
    fill: Callable | None = None


SOURCES: dict[str, Source] = {}


def register(source: Source) -> Source:
    SOURCES[source.name] = source
    return source


from jobradar.sources import ashby, greenhouse, lever, workday  # noqa: E402,F401  (registers them)
