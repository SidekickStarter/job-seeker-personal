"""Safe edits to config.toml (used by the web Settings tab and `jobseeker add`).

Edited with tomlkit so comments and layout survive; only values that actually change are
rewritten. Every edit is re-parsed and every regex compiled before writing, so a bad entry
can't break a scheduled run.
"""
from __future__ import annotations

import threading
import tomllib

import tomlkit

from jobseeker.matching import Matcher, kw_regex
from jobseeker.radar import MODES
from jobseeker.sources import SOURCES
from jobseeker.workspace import JobSeekerError, Workspace

NO_TRACK = "none"   # a company that gets no scoring-group bonus
_LOCK = threading.Lock()


def tstr(s):
    """Literal TOML strings ('...') so regexes like \\b need no escaping."""
    s = str(s)
    return tomlkit.string(s, literal=True) if "'" not in s and "\n" not in s else tomlkit.string(s)


def tarray(items):
    """TOML array packed several items per line (like the hand-written config), not one per line."""
    parts = [tstr(x).as_string() for x in items]
    if sum(len(p) + 2 for p in parts) < 90:
        return tomlkit.parse(f"x = [{', '.join(parts)}]")["x"]
    rows, row = [], ""
    for p in parts:
        if row and len(row) + len(p) > 96:
            rows.append(row.rstrip())
            row = ""
        row += p + ", "
    rows.append(row.rstrip())
    return tomlkit.parse("x = [\n  " + "\n  ".join(rows) + "\n]")["x"]


def validate(cfg: dict) -> None:
    Matcher(cfg)                                  # compiles filter/location regexes
    for t in cfg["tracks"].values():
        for k in t["keywords"]:
            kw_regex(k)


def edit_config(ws: Workspace, change) -> None:
    """Apply `change(doc)` to config.toml; write only if the result parses and validates."""
    with _LOCK:
        doc = tomlkit.parse(ws.config_path.read_text())
        change(doc)
        text = tomlkit.dumps(doc)
        validate(tomllib.loads(text))
        ws.config_path.write_text(text)
        ws.reload()


def lines(v) -> list[str]:
    items = v if isinstance(v, list) else str(v).splitlines()
    return [x.strip() for x in items if str(x).strip()]


def num(v, integer=False):
    f = float(v)
    return int(f) if integer or f.is_integer() else f


def board_of(c: dict) -> str:
    if c["ats"] == "workday":
        return f"{c.get('host', '')}/{c.get('site', '')}"
    return c.get("slug", "")


# --------------------------------------------------------------------------- search filters
def settings_view(ws: Workspace, match_counts: dict[str, int] | None = None) -> dict:
    c, counts = ws.cfg, match_counts or {}
    s, f, L = c["settings"], c["filters"], c["location"]
    return dict(
        settings={k: s.get(k) for k in ("min_score", "auto_prep_min_score", "auto_prep_max",
                                          "include_managers", "allow_unknown_location")},
        filters={k: f.get(k) for k in ("title_exclude", "title_must_match", "seniority",
                                         "allow_unlabeled_seniority")},
        location={"modes": L.get("modes", list(MODES)), "metro": L["metro"],
                  "metro_exclude": L.get("metro_exclude", [])},
        tracks={k: {"label": v.get("label", k), "weight": v.get("weight", 1.0), "keywords": v["keywords"]}
                for k, v in c["tracks"].items()},
        stack=c["stack"],
        companies=[{"name": x["name"], "ats": x["ats"], "board": board_of(x), "track": x["track"],
                    "enabled": x.get("enabled", True), "matches": counts.get(x["name"], 0)}
                   for x in c["companies"]])


def apply_settings(ws: Workspace, body: dict) -> None:
    """Save a (partial) settings_view-shaped payload. Raises ValueError/re.error on bad input."""
    def setv(table, key, value):
        if table.get(key) != value:      # untouched values keep their formatting and comments
            table[key] = tarray(value) if isinstance(value, list) else value

    def change(doc):
        s, f, L = doc["settings"], doc["filters"], doc["location"]
        b = body.get("settings", {})
        for k in ("min_score", "auto_prep_min_score"):
            if k in b:
                setv(s, k, num(b[k]))
        if "auto_prep_max" in b:
            setv(s, "auto_prep_max", num(b["auto_prep_max"], integer=True))
        for k in ("include_managers", "allow_unknown_location"):
            if k in b:
                setv(s, k, bool(b[k]))
        b = body.get("filters", {})
        if "title_exclude" in b:
            setv(f, "title_exclude", lines(b["title_exclude"]))
        for k in ("title_must_match", "seniority"):
            v = (b.get(k) or "").strip()
            if v and f.get(k) != v:
                f[k] = tstr(v)
        if "allow_unlabeled_seniority" in b:
            setv(f, "allow_unlabeled_seniority", bool(b["allow_unlabeled_seniority"]))
        b = body.get("location", {})
        if "modes" in b:
            modes = [m for m in MODES if m in b["modes"]]
            if not modes:
                raise ValueError("Pick at least one work mode")
            setv(L, "modes", modes)
        for k in ("metro", "metro_exclude"):
            if k in b:
                setv(L, k, lines(b[k]))
        for name, t in body.get("tracks", {}).items():
            if name not in doc["tracks"]:
                continue
            track = doc["tracks"][name]
            if "weight" in t:
                setv(track, "weight", float(t["weight"]))
            if "keywords" in t:
                setv(track, "keywords", lines(t["keywords"]))
        for k in ("bonus_keywords", "penalty_keywords"):
            if k in body.get("stack", {}):
                setv(doc["stack"], k, lines(body["stack"][k]))
    edit_config(ws, change)


# --------------------------------------------------------------------------- companies
def add_company(ws: Workspace, name: str, ats: str, track: str = NO_TRACK, slug: str | None = None,
                host: str | None = None, tenant: str | None = None, site: str | None = None) -> None:
    name, track = (name or "").strip(), track or NO_TRACK
    if not name or ats not in SOURCES:
        raise JobSeekerError("Name and board are required")
    if any(x["name"].lower() == name.lower() for x in ws.cfg["companies"]):
        raise JobSeekerError(f"{name} is already in your list")
    if track != NO_TRACK and track not in ws.cfg["tracks"]:
        raise JobSeekerError("Unknown scoring group")
    t = tomlkit.table()
    t["name"], t["track"], t["ats"] = name, track, ats
    if ats == "workday":
        for k, v in (("host", host), ("tenant", tenant), ("site", site)):
            if not v:
                raise JobSeekerError(f"Workday needs {k}")
            t[k] = v
        t["search"] = tarray(["senior software engineer", "staff software engineer"])
        t["max_results"] = 100
    else:
        if not slug:
            raise JobSeekerError("Board name is required")
        t["slug"] = slug
    t["enabled"] = True
    edit_config(ws, lambda doc: doc["companies"].append(t))


def update_company(ws: Workspace, name: str, enabled: bool | None = None, track: str | None = None,
                   remove: bool = False) -> None:
    def change(doc):
        for x in doc["companies"]:
            if x["name"] == name:
                if enabled is not None:
                    x["enabled"] = bool(enabled)
                if track:
                    x["track"] = track
                if remove:
                    doc["companies"].remove(x)
                return
        raise JobSeekerError(f"Company {name!r} not found")
    edit_config(ws, change)

