"""Run `jobseeker daily` on a schedule: a launchd agent on macOS, a cron line elsewhere."""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from jobseeker.workspace import Workspace

LABEL = "com.jobseeker.daily"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def command(ws: Workspace) -> list[str]:
    return [sys.executable, "-m", "jobseeker", "--home", str(ws.home), "daily"]


def plist(ws: Workspace, hour: int, minute: int, weekdays: bool) -> str:
    days = range(1, 6) if weekdays else range(0, 7)
    cal = "\n".join(f"    <dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{hour}</integer>"
                    f"<key>Minute</key><integer>{minute}</integer></dict>" for d in days)
    args = "\n".join(f"    <string>{escape(a)}</string>" for a in command(ws))
    log = escape(str(ws.logs_dir / "daily.log"))
    pkg_root = escape(str(Path(__file__).resolve().parent.parent))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}
  </array>
  <key>WorkingDirectory</key><string>{escape(str(ws.home))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key><string>{pkg_root}</string>
    <key>PATH</key><string>{escape(os.environ.get("PATH", "/usr/bin:/bin"))}</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
{cal}
  </array>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def cron_line(ws: Workspace, hour: int, minute: int, weekdays: bool) -> str:
    cmd = " ".join(f'"{a}"' if " " in a else a for a in command(ws))
    return f"{minute} {hour} * * {'1-5' if weekdays else '*'} cd \"{ws.home}\" && {cmd} >> \"{ws.logs_dir / 'daily.log'}\" 2>&1"


def install(ws: Workspace, hour: int = 8, minute: int = 0, weekdays: bool = True) -> str:
    """Install (or replace) the schedule. Returns a human-readable description."""
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Darwin":
        return ("Add this line to your crontab (`crontab -e`):\n\n" + cron_line(ws, hour, minute, weekdays))
    domain = f"gui/{os.getuid()}"
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    PLIST.write_text(plist(ws, hour, minute, weekdays))
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    when = f"{'weekdays' if weekdays else 'every day'} at {hour:02d}:{minute:02d}"
    return (f"Scheduled `jobseeker daily` {when} ({PLIST}).\nLog: {ws.logs_dir / 'daily.log'}\n"
            f"Run now: launchctl kickstart {domain}/{LABEL}")


def uninstall() -> str:
    if platform.system() != "Darwin":
        return "Remove the jobseeker line from your crontab (`crontab -e`)."
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    return "Schedule removed."
