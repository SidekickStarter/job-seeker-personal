"""Knock-out warnings: requirements in a posting or its form that would likely get you
auto-rejected (sponsorship, citizenship or clearance, degree, years of experience, pay below
your floor), checked against profile.toml before you spend time on the application.

Each Flag has a level:
  block  the posting states a hard requirement your profile doesn't meet
  warn   worth checking before you apply (a soft requirement, or one we can't be sure about)
and quotes the sentence it's based on, so you can judge it yourself. These are text heuristics,
not verdicts.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

LEVELS = ("block", "warn")


@dataclass
class Flag:
    level: str          # block | warn
    kind: str           # sponsorship | authorization | citizenship | clearance | degree | experience | salary | onsite
    message: str
    evidence: str = ""  # the sentence or question it's based on
    question: str = ""  # form question key, when it comes from the form


# --------------------------------------------------------------------------- your profile
@dataclass
class Candidate:
    needs_sponsorship: bool
    authorized: bool
    citizen: bool
    clearance: str
    years: float | None
    degree: int | None          # 1 bachelor's, 2 master's, 3 doctorate; None = don't check
    salary_min: int | None

    @classmethod
    def from_profile(cls, prof: dict) -> Candidate:
        w = prof.get("work", {})
        yes = lambda v: str(v).strip().lower().startswith(("y", "true"))  # noqa: E731
        cit = str(w.get("citizenship", "")).lower()
        clearance = str(w.get("clearance", "")).strip()
        try:
            years = float(w["years_experience"]) if str(w.get("years_experience", "")).strip() else None
        except ValueError:
            years = None
        try:
            salary = int(re.sub(r"[^\d]", "", str(w.get("salary_min", "")))) or None
        except ValueError:
            salary = None
        return cls(needs_sponsorship=yes(w.get("sponsorship", "No")),
                   authorized=yes(w.get("authorized", "Yes")),
                   citizen="citizen" in cit and "non" not in cit,
                   clearance="" if clearance.lower() in ("", "none", "no", "n/a") else clearance,
                   years=years, degree=degree_level(str(w.get("degree", ""))) or None, salary_min=salary)


# --------------------------------------------------------------------------- patterns
NOT_REQUIRED = re.compile(r"\b(?:prefer(?:red|ably)?|nice[- ]to[- ]have|a plus|is a plus|bonus|ideally|desired|"
                          r"advantage(?:ous)?|not required|a big plus)\b", re.I)
EQUIVALENT = re.compile(r"\bor (?:an? )?equivalent\b|\bequivalent (?:practical |work |professional |industry )?"
                        r"experience\b|\bor (?:related|relevant) (?:practical |work )?experience\b"
                        r"|\b(?:experience|background)\s*,?\s*or\s+(?:an?\s+)?(?:advanced\s+)?"
                        r"(?:degree|m\.|ms|msc|master|ph|bachelor|b\.)"          # "experience or an M.S."
                        r"|\b(?:degree|m\.?s\.?|ph\.?d\.?)\s*,?\s*or\s+(?:\w+\s+){0,3}experience\b", re.I)

NO_SPONSOR = re.compile(
    r"\b(?:unable|not able|cannot|can ?not|can't|will not|won't|do(?:es)? not|are not|is not)\s+(?:currently\s+|able\s+to\s+)?"
    r"(?:to\s+)?(?:offer|provide|support|consider)?\s*(?:visa\s+|immigration\s+|employment\s+|h-?1b\s+)?sponsor(?:ship)?\b"
    r"|\bno (?:visa |immigration |h-?1b )?sponsorship\b|\bsponsorship (?:is )?(?:not|un)available\b"
    r"|\bwithout (?:the need for |requiring |needing )?(?:current or future |visa |employer |company )*sponsorship\b", re.I)
CITIZEN_ONLY = re.compile(r"\b(?:must|required to|need to|needs to) be (?:a )?U\.?\s?S\.? citizen|"
                          r"\bU\.?\s?S\.? citizenship (?:is |will be )?(?:required|mandatory)|"
                          r"\b(?:requires?|requiring) (?:active )?U\.?\s?S\.? citizenship\b", re.I)
# Explicit only: generic "to conform to US export control regulations" boilerplate is too common to flag.
US_PERSON = re.compile(r"\bU\.?\s?S\.? persons?\b|\bITAR\b", re.I)
CLEARANCE_TYPES = r"(?:top[- ]secret(?:/sci)?|ts/sci|ts|secret|public trust|dod|security)"
ACTIVE_CLEARANCE = re.compile(
    rf"\b(?:active|current|existing|hold|holds|holding|possess|maintain(?:ing)? an? active)\b[^.;]{{0,40}}?\b{CLEARANCE_TYPES}\s+clearance\b"
    rf"|\b{CLEARANCE_TYPES}\s+clearance\s+(?:is\s+)?required\b|\bclearance (?:is )?required\b|\bpolygraph\b", re.I)
OBTAIN_CLEARANCE = re.compile(r"\b(?:able|ability|eligible|eligibility|willing(?:ness)?) to (?:obtain|acquire|get)\b"
                              r"[^.;]{0,50}\bclearance\b", re.I)

DEGREES = [   # (level, pattern): matched within a sentence that talks about a degree
    (3, re.compile(r"\bph\.?\s?d\b|\bdoctor(?:ate|al)\b", re.I)),
    (2, re.compile(r"\bmaster'?s\b|\bm\.\s?s\.?(?=[\s,/)]|$)|\bmsc\b|\bm\.?eng\b|\bmba\b|\bms(?=\s+(?:in|degree|or)\b)", re.I)),
    (1, re.compile(r"\bbachelor'?s?\b|\bb\.\s?s\.?(?=[\s,/)]|$)|\bb\.\s?a\.?(?=[\s,/)]|$)|\bbsc?(?=[\s,/)]|$)|\bba(?=\s+(?:in|degree|or)\b)"
                   r"|\bundergraduate degree\b|\bfour[- ]year degree\b", re.I)),
]
DEGREE_CONTEXT = re.compile(r"\bdegree\b|\bin (?:computer science|engineering|a (?:related|technical|quantitative) field)\b"
                            r"|\bph\.?\s?d\b|\bbachelor|\bmaster'?s\b", re.I)
EXPLICIT_REQUIRED = re.compile(r"\b(?:required|requirement|must have|minimum|at least)\b", re.I)

YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:or more\s+|-\s*\d{1,2}\s+|–\s*\d{1,2}\s+|to\s+\d{1,2}\s+)?"
                   r"years?\b(?:\s+of)?(?:\s+[\w/&+.#-]+){0,7}?\s+(?:experience|exp\b)", re.I)

MONEY = r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,3}(?:\.\d+)?\s?[kK])"
PAY_RANGE = re.compile(MONEY + r"\s*(?:-|–|—|to)\s*(?:USD\s*)?" + MONEY + r"(?!\s*(?:/\s*h|per hour|an hour|hourly))", re.I)

Q_SPONSOR = re.compile(r"sponsor|visa status|immigration", re.I)
Q_AUTHORIZED = re.compile(r"authori[sz]ed to work|legally (?:eligible|able|permitted|entitled) to work|right to work", re.I)
Q_CLEARANCE = re.compile(r"clearance", re.I)
Q_CITIZEN = re.compile(r"\bcitizen", re.I)
Q_ONSITE = re.compile(r"relocat|commut|on[- ]?site|in[- ]office|in[- ]person|days? (?:a|per) week", re.I)


def degree_level(text: str) -> int:
    """Highest degree named in text (0 if none)."""
    return max((lvl for lvl, pat in DEGREES if pat.search(text)), default=0)


def sentences(text: str) -> list[str]:
    # End a sentence only after a real word ("...systems. We"), not inside B.S. / Ph.D. / e.g.
    parts = re.split(r"(?<=[a-z0-9)]{3}[.!?])\s+|(?<=[;•])\s+|\n+|\s+[•·▪]\s+|\s{2,}", text or "")
    return [p.strip() for p in parts if len(p.strip()) > 3]


def clip(s: str, n: int = 220) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def around(s: str, m: re.Match | None, width: int = 220) -> str:
    """The part of a long sentence around the match, so the quote shows the words that triggered it."""
    if not m or len(s) <= width:
        return clip(s, width)
    start = max(0, m.start() - width // 3)
    end = min(len(s), start + width)
    return ("…" if start else "") + clip(s[start:end], width + 1).rstrip("…") + ("…" if end < len(s) else "")


def money(v: str) -> int:
    v = v.replace(",", "").strip()
    return int(float(v[:-1]) * 1000) if v[-1:] in "kK" else int(float(v))


# --------------------------------------------------------------------------- checks
def check_posting(text: str, prof: dict) -> list[Flag]:
    """Flags from the posting's description."""
    me, flags, seen = Candidate.from_profile(prof), [], set()

    def add(level, kind, message, sentence, match=None):
        if kind not in seen:            # one flag per kind is enough; the first quote explains it
            seen.add(kind)
            flags.append(Flag(level, kind, message, around(sentence, match)))

    years_needed = []
    for s in sentences(text):
        if me.needs_sponsorship and (m := NO_SPONSOR.search(s)):
            add("block", "sponsorship", "Posting says it won't sponsor visas; your profile needs sponsorship.", s, m)
        if not me.citizen and (m := CITIZEN_ONLY.search(s)):
            add("block", "citizenship", "Posting requires U.S. citizenship.", s, m)
        elif not me.citizen and (m := US_PERSON.search(s)):
            add("warn", "citizenship", "Export-controlled role (ITAR / U.S. person): citizenship or permanent "
                                       "residency is usually required.", s, m)
        if not me.clearance and (m := ACTIVE_CLEARANCE.search(s)) and not NOT_REQUIRED.search(s):
            add("block", "clearance", "Posting requires an active security clearance; your profile has none.", s, m)
        elif not me.clearance and not me.citizen and (m := OBTAIN_CLEARANCE.search(s)):
            add("warn", "clearance", "You must be able to get a security clearance, which usually requires "
                                     "U.S. citizenship.", s, m)
        if me.degree and DEGREE_CONTEXT.search(s) and not EQUIVALENT.search(s) and not NOT_REQUIRED.search(s):
            levels = [lvl for lvl, pat in DEGREES if pat.search(s)]
            if levels and min(levels) > me.degree:
                name = {2: "a master's", 3: "a PhD"}[min(levels)]
                hard = bool(EXPLICIT_REQUIRED.search(s))
                add("block" if hard else "warn", "degree",
                    f"Posting asks for {name} degree" + (" (required)" if hard else "") + "; yours is lower.", s)
        if me.years is not None and not NOT_REQUIRED.search(s):
            years_needed += [(int(m.group(1)), s, m) for m in YEARS.finditer(s) if 0 < int(m.group(1)) <= 30]
    if years_needed:
        n, s, m = max(years_needed, key=lambda x: x[0])
        if n > me.years:
            add("warn", "experience", f"Posting asks for {n}+ years; your profile says {me.years:g}.", s, m)

    if me.salary_min:
        tops = []
        for m in PAY_RANGE.finditer(text or ""):
            lo, hi = money(m.group(1)), money(m.group(2))
            if hi >= 20000 and hi >= lo:
                tops.append((hi, m.group(0)))
        if tops:
            hi, quote = max(tops)
            if hi < me.salary_min:
                add("warn", "salary", f"Posted pay tops out at ${hi:,}, below your minimum ${me.salary_min:,}.", quote)
    return flags


def check_form(questions: list[dict], prof: dict) -> list[Flag]:
    """Flags from application questions (and the answers prepared for them)."""
    me, flags = Candidate.from_profile(prof), []
    for q in questions:
        label, ans = q.get("label", ""), q.get("answer", "")
        ans_s = ", ".join(ans) if isinstance(ans, list) else str(ans or "")

        def add(level, kind, message, _q=q, _label=label):
            flags.append(Flag(level, kind, message, clip(_label), _q.get("key", "")))
        if Q_SPONSOR.search(label) and me.needs_sponsorship:
            add("block", "sponsorship", "Knock-out question: needing sponsorship is often an automatic rejection.")
        elif Q_AUTHORIZED.search(label) and not me.authorized:
            add("block", "authorization", "Knock-out question: answering No to work authorization is usually "
                                          "an automatic rejection.")
        elif Q_CLEARANCE.search(label) and not me.clearance and re.search(r"active|current|hold|have|possess", label, re.I):
            add("block", "clearance", "Asks whether you hold a security clearance; your profile has none.")
        elif Q_CITIZEN.search(label) and not me.citizen:
            add("warn", "citizenship", "Asks about U.S. citizenship; this role may require it.")
        elif (m := re.search(r"(\d{1,2})\s*\+?\s*(?:or more\s+)?years", label, re.I)) and me.years is not None \
                and int(m.group(1)) > me.years:
            add("warn", "experience", f"Asks for {m.group(1)}+ years; your profile says {me.years:g}.")
        elif re.search(r"degree|education", label, re.I) and me.degree and degree_level(label) > me.degree \
                and re.search(r"\b(?:do|have|hold)\b", label, re.I):
            add("warn", "degree", "Asks whether you hold a degree above yours.")
        elif Q_ONSITE.search(label) and ans_s.strip().lower().startswith("no"):
            add("warn", "onsite", f"You're answering \"{ans_s}\" to an on-site / relocation question.")
    return flags


def check(text: str, questions: list[dict], prof: dict) -> list[Flag]:
    """Posting + form flags, form first (it's what the screener actually sees), deduplicated by kind."""
    out, kinds = [], set()
    for f in check_form(questions, prof) + check_posting(text, prof):
        if f.kind not in kinds:
            kinds.add(f.kind)
            out.append(f)
    return sorted(out, key=lambda f: LEVELS.index(f.level))


# --------------------------------------------------------------------------- storage helpers
def dumps(flags: list[Flag]) -> str:
    return json.dumps([asdict(f) for f in flags], ensure_ascii=False) if flags else ""


def loads(s: str) -> list[Flag]:
    try:
        return [Flag(**d) for d in json.loads(s)] if s else []
    except (ValueError, TypeError):
        return []


def worst(flags: list[Flag]) -> str:
    """'block', 'warn' or ''."""
    return min((f.level for f in flags), key=LEVELS.index, default="")
