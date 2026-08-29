#!/usr/bin/env python3
"""Fail when local robot credentials appear in Git candidate files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_HEADER = ROOT / "hardware" / "firmware" / "deskbot_local_config.h"
LOCAL_ENV = ROOT / "service" / ".env"


def local_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if LOCAL_HEADER.exists():
        text = LOCAL_HEADER.read_text(encoding="utf-8")
        for name, value in re.findall(r'^\s*#define\s+([A-Z0-9_]+)\s+"([^"]+)"', text, re.M):
            if name in {
                "WIFI_DEFAULT_SSID",
                "WIFI_DEFAULT_PASSWORD",
                "DESKBOT_API_KEY",
            }:
                values[f"local-header:{name}"] = value
    if LOCAL_ENV.exists():
        for raw_line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"\'')
            if re.search(r"(?:KEY|TOKEN|SECRET|PASSWORD|APP_ID)$", name, re.I) and len(value) >= 6:
                values[f"service-env:{name}"] = value
    return values


def candidate_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode("utf-8") for item in proc.stdout.split(b"\0") if item]


def main() -> int:
    secrets = local_values()
    findings: list[tuple[str, str]] = []
    for path in candidate_files():
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for source, value in secrets.items():
            if value.encode("utf-8") in data:
                findings.append((source, path.relative_to(ROOT).as_posix()))

    for label, args in (
        ("<working-tree-diff>", ["git", "diff", "--no-ext-diff", "--binary"]),
        ("<staged-diff>", ["git", "diff", "--cached", "--no-ext-diff", "--binary"]),
    ):
        diff = subprocess.run(args, cwd=ROOT, check=True, capture_output=True).stdout
        for source, value in secrets.items():
            if value.encode("utf-8") in diff:
                findings.append((source, label))

    if findings:
        for source, path in findings:
            print(f"[secret-check] {source} appears in {path}")
        print("[secret-check] blocked: move these values to ignored local files before committing")
        return 1
    print(f"[secret-check] ok: checked {len(secrets)} local values; none appear in Git candidate files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
