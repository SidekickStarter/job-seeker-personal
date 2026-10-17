"""SQLite storage: postings already seen (to flag new ones) and application status."""
from __future__ import annotations

import datetime as dt
import sqlite3

from jobseeker.workspace import Workspace

STATUSES = ("prepared", "filled", "applied", "interviewing", "rejected", "offer", "withdrawn")


def open_db(ws: Workspace) -> sqlite3.Connection:
    db = sqlite3.connect(ws.db_path)
    db.execute("""create table if not exists seen(
        key text primary key, first_seen text, company text, title text,
        url text, score real, track text)""")
    db.execute("""create table if not exists applications(
        key text primary key, folder text, company text, title text, url text,
        status text, created text, updated text)""")
    return db


def set_status(ws: Workspace, job: dict, folder, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    now = dt.date.today().isoformat()
    db = open_db(ws)
    db.execute("""insert into applications values (?,?,?,?,?,?,?,?)
                  on conflict(key) do update set status=excluded.status, updated=excluded.updated,
                  folder=coalesce(excluded.folder, folder)""",
               (job["key"], ws.rel(folder) if folder else None, job["company"], job["title"],
                job["url"], status, now, now))
    db.commit()


def applications(ws: Workspace) -> dict[str, dict]:
    cols = ("key", "folder", "company", "title", "url", "status", "created", "updated")
    rows = open_db(ws).execute(f"select {', '.join(cols)} from applications").fetchall()
    return {r[0]: dict(zip(cols, r)) for r in rows}


def first_seen(ws: Workspace) -> dict[str, str]:
    return dict(open_db(ws).execute("select key, first_seen from seen"))
