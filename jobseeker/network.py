"""Your network at each company: who you already know there, how likely they are to help with a
referral, and search links for finding the hiring manager and the team.

Sources (no scraping; LinkedIn's terms forbid automated access):
  linkedin/Connections.csv   LinkedIn's own export of your 1st-degree connections
                             (Settings & Privacy > Data privacy > Get a copy of your data > Connections)
  contacts.toml              how well you know someone, plus people you add by hand:
                               [[contact]]
                               name = "Jane Doe"            # matches the export by name or url
                               company = "Reddit"           # only needed for people not in the export
                               position = "Senior Software Engineer, Data Platform"
                               url = "https://www.linkedin.com/in/janedoe"
                               closeness = "worked together"   # see CLOSENESS
                               note = "Amazon 2019-2021"

The likelihood is a transparent heuristic, shown with its reasons, not a prediction.
"""
from __future__ import annotations

import csv
import io
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field

from jobseeker.workspace import Workspace

CONNECTIONS = "Connections.csv"

# How well you know them: the strongest signal for a referral, and only you know it.
CLOSENESS = {               # label -> points
    "close": 35,
    "worked together": 30,
    "know well": 25,
    "know a bit": 12,
    "met once": 4,
    "never talked": -10,
    "": 0,                  # not tagged yet
}

ROLES = [   # (role, pattern on the position title), first match wins
    ("executive", re.compile(r"\b(?:chief|cto|ceo|cfo|coo|founder|co-founder|president|svp|evp|vp|vice president)\b", re.I)),
    ("recruiter", re.compile(r"recruit|talent|sourc(?:er|ing)|people partner|people operations|\bhr\b|human resources", re.I)),
    ("hiring manager", re.compile(r"\b(?:engineering manager|manager,? (?:software|engineering|platform|data)|"
                                  r"(?:software|engineering|platform|data) manager|head of|director|"
                                  r"manager)\b", re.I)),
    ("engineer", re.compile(r"engineer|developer|\bsde\b|\bswe\b|architect|scientist|programmer|\bdevops\b|\bsre\b", re.I)),
]
ROLE_POINTS = {"engineer": 40, "recruiter": 30, "hiring manager": 30, "executive": 12, "other": 18}
ROLE_WHY = {
    "engineer": "engineers are the most common referrers, and usually earn a referral bonus",
    "recruiter": "can route your application to the right team",
    "hiring manager": "could be the hiring manager or know who is; better for a direct note than a referral ask",
    "executive": "senior leaders rarely refer directly, but an intro carries weight",
    "other": "can submit a referral, but may not know the engineering teams",
}
SUFFIXES = re.compile(r"\b(?:inc|incorporated|llc|ltd|limited|corp|corporation|co|company|plc|gmbh|the|"
                      r"technologies|technology|holdings|group)\b\.?", re.I)
STOP = {"senior", "staff", "principal", "lead", "software", "engineer", "engineering", "sr", "ii", "iii", "iv",
        "and", "the", "of", "for", "team", "remote", "us", "usa", "new", "york", "hybrid", "machine", "learning",
        "full", "stack", "backend", "frontend", "manager", "developer", "member", "technical", "i"}


@dataclass
class Contact:
    name: str
    company: str = ""
    position: str = ""
    url: str = ""
    email: str = ""
    connected_on: str = ""
    closeness: str = ""
    note: str = ""
    source: str = "export"      # export | manual

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name else ""


@dataclass
class Lead:
    contact: Contact
    role: str
    score: int
    likelihood: str             # High | Medium | Low
    reasons: list[str] = field(default_factory=list)
    team_match: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- loading
def norm_company(name: str) -> str:
    s = re.sub(r"\(.*?\)", " ", name or "")
    s = SUFFIXES.sub(" ", s.lower().replace("&", " and "))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def same_company(contact_company: str, company: str, aliases=()) -> bool:
    a = norm_company(contact_company)
    if not a:
        return False
    for b in [norm_company(company), *(norm_company(x) for x in aliases)]:
        if b and (a == b or a.startswith(b + " ") or b.startswith(a + " ")):
            return True
    return False


def parse_connections(text: str) -> list[Contact]:
    """LinkedIn's Connections.csv starts with a few 'Notes:' lines before the header row."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lower().startswith("first name,")), None)
    if start is None:
        raise ValueError("This doesn't look like LinkedIn's Connections.csv (no 'First Name,...' header).")
    out = []
    for r in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        name = f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip()
        if name:
            out.append(Contact(name=name, company=(r.get("Company") or "").strip(),
                               position=(r.get("Position") or "").strip(), url=(r.get("URL") or "").strip(),
                               email=(r.get("Email Address") or "").strip(),
                               connected_on=(r.get("Connected On") or "").strip()))
    return out


def load_contacts(ws: Workspace) -> list[Contact]:
    """Your export merged with contacts.toml (tags apply by url or name; extra people are added)."""
    path = ws.network_dir / CONNECTIONS
    contacts = parse_connections(path.read_text(encoding="utf-8-sig")) if path.exists() else []
    manual = []
    if ws.contacts_path.exists():
        with open(ws.contacts_path, "rb") as f:
            manual = tomllib.load(f).get("contact", [])
    by_url = {c.url.rstrip("/").lower(): c for c in contacts if c.url}
    by_name = {c.name.lower(): c for c in contacts}
    for m in manual:
        hit = by_url.get(str(m.get("url", "")).rstrip("/").lower()) or by_name.get(str(m.get("name", "")).lower())
        if hit:
            hit.closeness = m.get("closeness", hit.closeness)
            hit.note = m.get("note", hit.note)
            for k in ("company", "position"):
                if m.get(k):
                    setattr(hit, k, m[k])
        elif m.get("name"):
            contacts.append(Contact(name=m["name"], company=m.get("company", ""), position=m.get("position", ""),
                                    url=m.get("url", ""), closeness=m.get("closeness", ""), note=m.get("note", ""),
                                    source="manual"))
    return contacts


def save_import(ws: Workspace, text: str) -> int:
    """Validate and store an uploaded Connections.csv. Returns the number of connections."""
    n = len(parse_connections(text))
    ws.network_dir.mkdir(parents=True, exist_ok=True)
    (ws.network_dir / CONNECTIONS).write_text(text, encoding="utf-8")
    return n


def set_closeness(ws: Workspace, contact: Contact, closeness: str, note: str | None = None) -> None:
    """Record how well you know someone in contacts.toml (created if needed; comments kept)."""
    import tomlkit
    if closeness not in CLOSENESS:
        raise ValueError(f"closeness must be one of {', '.join(repr(k) for k in CLOSENESS if k)}")
    doc = tomlkit.parse(ws.contacts_path.read_text()) if ws.contacts_path.exists() else tomlkit.document()
    if "contact" not in doc:
        if not ws.contacts_path.exists():
            doc.add(tomlkit.comment("How well you know people (used to rank referral likelihood). Private: gitignored."))
        doc["contact"] = tomlkit.aot()
    for t in doc["contact"]:
        if (contact.url and t.get("url") == contact.url) or t.get("name", "").lower() == contact.name.lower():
            break
    else:
        t = tomlkit.table()
        t["name"] = contact.name
        if contact.url:
            t["url"] = contact.url
        if contact.source == "manual":
            t["company"], t["position"] = contact.company, contact.position
        doc["contact"].append(t)
    t["closeness"] = closeness
    if note is not None:
        t["note"] = note
    ws.contacts_path.write_text(tomlkit.dumps(doc))


# --------------------------------------------------------------------------- ranking
def role_of(position: str) -> str:
    return next((role for role, pat in ROLES if pat.search(position or "")), "other")


def team_words(job_title: str) -> list[str]:
    """Distinctive words from the job title, e.g. 'Staff Engineer, Ingestion Platform' -> ingestion, platform."""
    words = re.findall(r"[a-z][a-z+#]{2,}", (job_title or "").lower())
    return [w for w in dict.fromkeys(words) if w not in STOP]


def rank(contacts: list[Contact], company: str, job_title: str = "", aliases=()) -> list[Lead]:
    team = team_words(job_title)
    leads = []
    for c in contacts:
        if not same_company(c.company, company, aliases):
            continue
        role = role_of(c.position)
        score, reasons = ROLE_POINTS[role], [f"{role.capitalize()} — {ROLE_WHY[role]}"]
        overlap = [w for w in team if re.search(rf"\b{re.escape(w)}", c.position.lower())]
        if overlap:
            score += 15 if role in ("engineer", "hiring manager") else 5
            reasons.append(f"Title mentions {', '.join(overlap)}, like this job — likely the same or a nearby team")
        pts = CLOSENESS.get(c.closeness, 0)
        if c.closeness:
            score += pts
            reasons.append(f"You've tagged them: {c.closeness}")
        else:
            reasons.append("Tag how well you know them to sharpen this")
        score = max(0, min(100, score))
        likelihood = "High" if score >= 60 else "Medium" if score >= 40 else "Low"
        leads.append(Lead(c, role, score, likelihood, reasons, overlap))
    return sorted(leads, key=lambda lead: (-lead.score, lead.contact.name))


def counts_by_company(contacts: list[Contact], companies: list[dict]) -> dict[str, int]:
    """Number of your connections at each configured company (name -> count)."""
    out = {}
    for co in companies:
        n = sum(same_company(c.company, co["name"], co.get("aliases", ())) for c in contacts)
        if n:
            out[co["name"]] = n
    return out


# --------------------------------------------------------------------------- finding people you don't know yet
def li_search(keywords: str) -> str:
    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode({"keywords": keywords})


def google_xray(company: str, *terms: str) -> str:
    q = f'site:linkedin.com/in "{company}" ' + " ".join(f'"{t}"' for t in terms if t)
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})


def search_links(company: str, job_title: str, past_employers=()) -> list[dict]:
    """Links you open yourself to find the hiring manager, the team, recruiters and former colleagues."""
    team = " ".join(team_words(job_title)[:3])
    links = [
        {"label": "Likely hiring manager", "why": "engineering managers whose title mentions this team",
         "url": li_search(f"{company} engineering manager {team}".strip())},
        {"label": "Engineers on the team", "why": "potential referrers and future teammates",
         "url": li_search(f"{company} engineer {team}".strip())},
        {"label": "Technical recruiters", "why": "can route you to the right team",
         "url": li_search(f"{company} technical recruiter")},
        {"label": "Hiring manager via Google", "why": "public profiles, works without LinkedIn search limits",
         "url": google_xray(company, "engineering manager", *team_words(job_title)[:2])},
    ]
    for emp in past_employers:
        links.append({"label": f"Ex-{emp} at {company}", "why": "former colleagues are the warmest cold contacts",
                      "url": li_search(f"{company} {emp}")})
    return links


# --------------------------------------------------------------------------- outreach
def draft_note(lead: Lead, job: dict, me: dict, req: str = "") -> str:
    """A short LinkedIn note (aim: under 300 characters for a connection request)."""
    first, company = lead.contact.first_name, job["company"]
    title = re.sub(r"\s+", " ", job["title"]).strip()
    ref = f" ({req})" if req else ""
    who = f"{me.get('current_title', 'engineer')} at {me['current_company']}" if me.get("current_company") else \
        me.get("current_title", "an engineer")
    if lead.role == "recruiter":
        return (f"Hi {first}, I'm a {who} applying for {company}'s {title} role{ref}. "
                f"Could you point me to the right recruiter or hiring manager? Happy to send my resume.")
    if lead.role in ("hiring manager", "executive"):
        return (f"Hi {first}, I'm a {who} and just applied for the {title} role{ref} on your team at {company}. "
                f"I'd love 15 minutes to hear what you're looking for.")
    if lead.contact.closeness in ("close", "worked together", "know well"):
        return (f"Hi {first}! I'm applying for {title}{ref} at {company}. Would you be comfortable referring me? "
                f"I can send my resume and a short blurb you can paste.")
    return (f"Hi {first}, I'm a {who} interested in {company}'s {title} role{ref}. "
            f"Would you be open to a quick chat about the team, and possibly a referral?")
