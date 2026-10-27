from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.paper-radar.daily"


def build_plist(
    *,
    python: str,
    config_path: Path,
    working_dir: Path,
    log_dir: Path,
    hour: int,
    minute: int,
) -> str:
    log_dir.mkdir(parents=True, exist_ok=True)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{escape(LABEL)}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{escape(python)}</string>
    <string>-m</string>
    <string>paper_radar</string>
    <string>run</string>
    <string>--config</string>
    <string>{escape(str(config_path))}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{escape(str(working_dir))}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>{hour}</integer>
    <key>Minute</key>
    <integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{escape(str(log_dir / "launchd.out.log"))}</string>
  <key>StandardErrorPath</key>
  <string>{escape(str(log_dir / "launchd.err.log"))}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def install(plist_text: str) -> tuple[bool, str]:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(plist_text, encoding="utf-8")

    uid = os.getuid()
    domain = f"gui/{uid}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["launchctl", "enable", f"{domain}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        fallback = subprocess.run(
            ["launchctl", "load", "-w", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if fallback.returncode != 0:
            detail = (result.stderr or fallback.stderr or "unknown launchctl error").strip()
            return False, f"写入成功但加载失败：{detail}\nplist: {path}"
    return True, f"已安装：{path}"


def uninstall() -> tuple[bool, str]:
    path = plist_path()
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if path.exists():
        path.unlink()
        return True, f"已卸载并删除：{path}"
    return True, f"未找到 plist，无需卸载：{path}"


def default_python() -> str:
    return sys.executable
