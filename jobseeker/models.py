from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Job:
    """One posting from a job board. Sources fill the first block; the matcher fills the rest."""
    company: str
    company_track: str
    source: str
    job_id: str
    title: str
    location: str
    url: str
    posted: str = ""
    description: str = ""
    score: float = 0.0
    best_track: str = ""
    why: str = ""
    mode: str = ""         # remote | hybrid | onsite | office (in office, hybrid vs onsite not stated)
    mode_src: str = ""     # listed (ATS field) | inferred (location/description text)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.company}:{self.job_id}"
