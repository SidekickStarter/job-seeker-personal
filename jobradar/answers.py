"""Your profile (profile.toml) and the rules that answer common application questions."""
from __future__ import annotations

import re
import tomllib

from jobradar.workspace import JobRadarError, Workspace

EEO_RE = re.compile(r"gender|race|ethnic|hispanic|latino|veteran|disabilit|sexual orientation|transgender", re.I)
DECLINE = ["Decline", "I don't wish", "I do not wish", "Prefer not", "not to answer", "not to disclose",
           "I don't want", "Choose not", "do not want to answer"]


def load_profile(ws: Workspace) -> dict:
    if not ws.profile_path.exists():
        raise JobRadarError(f"{ws.profile_path} not found. Run `jobradar init`, then fill in profile.toml.")
    with open(ws.profile_path, "rb") as f:
        return tomllib.load(f)


def profile_vars(prof):
    return {k: str(v) for sec in ("me", "work") for k, v in prof.get(sec, {}).items()}


def pick_option(options, candidates):
    """First candidate (in preference order) that matches an option: exact, prefix, then substring."""
    low = [(o, o.lower().strip()) for o in options]
    for cand in candidates:
        c = cand.lower().strip()
        if not c:
            continue
        for strength in range(3):          # exact, then prefix, then substring
            for orig, o in low:
                if (o == c, o.startswith(c), c in o)[strength]:
                    return orig
    return None


def auto_answer(q, prof):
    """Returns (answer, source). source: profile | TODO | skip."""
    v = profile_vars(prof)
    by_key = {"first_name": v.get("first_name"), "last_name": v.get("last_name"),
              "preferred_name": v.get("preferred_name") or v.get("first_name"),
              "email": v.get("email"), "phone": v.get("phone"), "resume": v.get("resume"),
              "_systemfield_name": f"{v.get('first_name', '')} {v.get('last_name', '')}".strip(),
              "_systemfield_email": v.get("email"), "_systemfield_resume": v.get("resume")}
    label = q["label"].strip()
    if q["key"] in by_key and by_key[q["key"]]:
        return by_key[q["key"]], "profile"
    if q.get("ashby_type") == "Phone":
        return v.get("phone", ""), "profile"
    if q["type"] == "file":  # cover letter etc.: attach yourself if you want one
        return "", ("TODO" if q["required"] else "skip")

    candidates = None
    if EEO_RE.search(label):
        if prof.get("eeo", {}).get("policy") == "decline":
            candidates = DECLINE
    else:
        for rule in prof.get("rules", []):
            if re.search(rule["match"], label, re.I):
                candidates = [fmt(a, v) for a in rule["answer"]]
                break
    if candidates is None or not any(candidates):
        return "", ("TODO" if q["required"] or q["type"] in ("textarea",) else "skip")

    if q["options"]:
        hit = pick_option(q["options"], candidates)
        if not hit:
            return "", "TODO"
        return ([hit] if q["type"] == "multiselect" else hit), "profile"
    return candidates[0], "profile"


def fmt(template, v):
    s = template.format_map({k: v.get(k, "") for k in re.findall(r"{(\w+)}", template)})
    return "" if not s.strip(" ,") else s
