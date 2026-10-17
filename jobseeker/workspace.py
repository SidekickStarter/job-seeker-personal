"""Where your config and data live.

A workspace is a folder holding config.toml (companies, filters, scoring), profile.toml (your
details and answer rules) and everything Job Seeker writes: the SQLite database, digests,
application folders, notes and logs. Pick it with --home, the JOBSEEKER_HOME environment variable,
or by running commands from inside it.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "config.toml"
PROFILE_NAME = "profile.toml"


class JobSeekerError(Exception):
    """A problem the user can fix (missing config, unknown job, ...). Shown without a traceback."""


def find_home(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.environ.get("JOBSEEKER_HOME"):
        return Path(os.environ["JOBSEEKER_HOME"]).expanduser().resolve()
    return Path.cwd().resolve()


@dataclass
class Workspace:
    home: Path
    cfg: dict = field(default_factory=dict)

    @classmethod
    def load(cls, home: str | os.PathLike | None = None) -> Workspace:
        ws = cls(find_home(home))
        ws.reload()
        return ws

    def reload(self) -> dict:
        if not self.config_path.exists():
            raise JobSeekerError(f"No {CONFIG_NAME} in {self.home}. Run `jobseeker init` there "
                                f"(or pass --home / set JOBSEEKER_HOME).")
        with open(self.config_path, "rb") as f:
            self.cfg = tomllib.load(f)
        return self.cfg

    # ---- paths
    @property
    def settings(self) -> dict:
        return self.cfg["settings"]

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_NAME

    @property
    def profile_path(self) -> Path:
        return self.home / PROFILE_NAME

    @property
    def db_path(self) -> Path:
        return self.home / self.settings.get("db_path", "jobseeker.db")

    @property
    def digests_dir(self) -> Path:
        return self.home / self.settings.get("out_dir", "digests")

    @property
    def apps_dir(self) -> Path:
        return self.home / self.settings.get("apps_dir", "applications")

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def notes_dir(self) -> Path:
        return self.home / "referrals"

    @property
    def network_dir(self) -> Path:
        """Your LinkedIn export (Connections.csv): other people's data, kept local and gitignored."""
        return self.home / "linkedin"

    @property
    def contacts_path(self) -> Path:
        """How well you know each contact, plus people you add by hand."""
        return self.home / "contacts.toml"

    @property
    def matches_csv(self) -> Path:
        return self.digests_dir / "matches_latest.csv"

    def resolve(self, folder: str | os.PathLike | None) -> Path | None:
        """Folders are stored relative to the workspace (older rows may be absolute)."""
        return self.home / folder if folder else None

    def rel(self, path: Path) -> str:
        path = Path(path)
        return str(path.relative_to(self.home)) if path.is_relative_to(self.home) else str(path)
