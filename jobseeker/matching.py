"""Title, location and work-mode filters, plus keyword scoring against your tracks."""
from __future__ import annotations

import re
from functools import cache


@cache
def kw_regex(k: str) -> re.Pattern:
    """Keywords match as word-prefixes ('forecast' hits 'forecasting').
    A trailing '$' forces a whole-word match ('java$' won't hit 'javascript')."""
    whole = k.endswith("$")
    k = k.rstrip("$")
    return re.compile(r"(?<![a-z0-9])" + re.escape(k.lower()) + (r"(?![a-z0-9])" if whole else ""))


def hits(keywords, text):
    return [k.rstrip("$") for k in keywords if kw_regex(k).search(text)]


# Work-mode inference for boards with no workplace field (Greenhouse, Workday).
LOC_SPLIT_RE = re.compile(r"\s*(?:;|\||/|\bor\b)\s*", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b(?!\s*(?:cloud|search|retrieval|architecture|infrastructure|"
                       r"deployment|database|model|approach|apps?|ml|ai)\b)", re.I)
HYBRID_DAYS_RE = re.compile(r"\b(?:[1-4]|one|two|three|four)\s*(?:\+|or more)?\s*days?\s*(?:a|per|each|/)\s*"
                            r"week\b.{0,40}?\b(?:office|on[- ]?site|in[- ]person)", re.I)
REMOTE_DESC_RE = re.compile(r"\bfully remote\b|\b100% remote\b|\bremote[- ]first\b|\bwork from anywhere\b|"
                            r"\b(?:this|the) (?:role|position|job) (?:is|can be) (?:fully )?remote\b|"
                            r"\bremote (?:role|position|opportunity)\b", re.I)
ONSITE_LOC_RE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b", re.I)
ONSITE_DESC_RE = re.compile(r"\bfully (?:on[- ]?site|in[- ]office)\b|\b100% on[- ]?site\b|"
                            r"\b(?:five|5) days (?:a|per) week (?:in|at) (?:the|our) office\b|"
                            r"\b(?:this|the) (?:role|position) is (?:an )?(?:on[- ]?site|in[- ]office)\b", re.I)


class Matcher:
    def __init__(self, cfg):
        self.cfg = cfg
        f = cfg["filters"]
        self.eng = re.compile(f["title_must_match"], re.I)
        self.senior = re.compile(f["seniority"], re.I)
        self.excl = [re.compile(p, re.I) for p in f["title_exclude"]]
        self.mgr = re.compile(r"\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b", re.I)
        L = cfg["location"]
        self.metro = [re.compile(p, re.I) for p in L["metro"]]
        self.remote = re.compile(r"\bremote\b|\banywhere\b", re.I)
        self.us = [re.compile(p, re.I) for p in L["us_markers"]]
        self.non_us = [re.compile(p, re.I) for p in L["non_us_markers"]]
        self.metro_excl = [re.compile(p, re.I) for p in L.get("metro_exclude", [])]
        self.modes = set(L.get("modes", ["remote", "hybrid", "onsite"]))

    # -- filters
    def title_ok(self, title):
        if not self.eng.search(title):
            return False
        if any(p.search(title) for p in self.excl):
            return False
        if not self.cfg["settings"]["include_managers"] and self.mgr.search(title):
            return False
        return bool(self.senior.search(title)) or self.cfg["filters"]["allow_unlabeled_seniority"]

    def in_metro(self, loc):
        """True if any one of the listed locations is in the commute metro."""
        for seg in LOC_SPLIT_RE.split(loc):
            if any(p.search(seg) for p in self.metro) and not any(p.search(seg) for p in self.metro_excl):
                return True
        return False

    def us_remote(self, loc):
        if any(p.search(loc) for p in self.us) or self.in_metro(loc):
            return True
        return not any(p.search(loc) for p in self.non_us)

    def work_mode(self, job):
        """(mode, source). Uses the ATS field when there is one, else reads the text."""
        if job.mode:
            return job.mode, job.mode_src or "listed"
        loc = job.location
        if HYBRID_RE.search(loc):
            return "hybrid", "inferred"
        if self.remote.search(loc):
            return "remote", "inferred"
        if ONSITE_LOC_RE.search(loc):
            return "onsite", "inferred"
        desc = job.description
        if HYBRID_RE.search(desc) or HYBRID_DAYS_RE.search(desc):
            return "hybrid", "inferred"
        if REMOTE_DESC_RE.search(desc):
            return "remote", "inferred"
        if ONSITE_DESC_RE.search(desc):
            return "onsite", "inferred"
        return "office", "inferred"

    def mode_ok(self, mode):
        if mode == "office":  # in office, unstated whether hybrid or onsite
            return bool({"hybrid", "onsite"} & self.modes)
        return mode in self.modes

    def location_ok(self, job, prelim=False):
        """Remote roles: anywhere in the US. Hybrid/onsite roles: must be in the NY/NJ metro."""
        loc = job.location
        if not loc.strip() or (prelim and re.search(r"\d+\s+locations", loc, re.I)):
            return prelim or self.cfg["settings"]["allow_unknown_location"]
        if prelim:  # Workday list page: no description yet, so stay permissive
            return self.in_metro(loc) or (bool(self.remote.search(loc)) and self.us_remote(loc))
        if not self.mode_ok(job.mode):
            return False
        if job.mode == "remote":
            return self.us_remote(loc)
        return self.in_metro(loc)

    def keep(self, job, prelim=False):
        if not self.title_ok(job.title):
            return False
        if not prelim:
            job.mode, job.mode_src = self.work_mode(job)
        return self.location_ok(job, prelim)

    # -- scoring
    def score(self, job):
        title, desc = job.title.lower(), job.description.lower()
        tracks = self.cfg["tracks"]
        per = {}
        for name, t in tracks.items():
            th, dh = hits(t["keywords"], title), hits(t["keywords"], desc)
            s = 3 * len(th) + min(len(dh), 8)
            if job.company_track == name:
                s += self.cfg["settings"]["company_domain_bonus"]
            per[name] = (s * t.get("weight", 1.0), th, dh)

        best = max(per, key=lambda n: per[n][0])
        others = sum(v[0] for n, v in per.items() if n != best)
        st = self.cfg["stack"]
        bonus = hits(st["bonus_keywords"], desc + " " + title)
        pen_t = hits(st["penalty_keywords"], title)
        pen_d = hits(st["penalty_keywords"], desc)

        s = per[best][0] + 0.35 * others + 0.75 * len(bonus) - 4 * len(pen_t) - 1 * len(pen_d)
        if not self.senior.search(job.title):
            s -= 2  # unlabeled seniority: keep, but rank lower
        if re.search(r"\bstaff\b|\bprincipal\b", job.title, re.I):
            s += 1

        _, th, dh = per[best]
        why = [tracks[best].get("label", best)]
        if th:
            why.append("title: " + ", ".join(th))
        if dh:
            why.append("desc: " + ", ".join(dh[:6]))
        if bonus:
            why.append("stack: " + ", ".join(bonus))
        if pen_t or pen_d:
            why.append("gap: " + ", ".join(sorted(set(pen_t + pen_d))))
        job.score, job.best_track, job.why = round(s, 1), best, " | ".join(why)
        return job
