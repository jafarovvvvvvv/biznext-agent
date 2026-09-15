"""Create .env from .env.example with random URL-safe passwords and tokens.

Usage: python scripts/init_env.py [--force]
       python scripts/init_env.py --add-missing   # upgrade: append keys that appeared in .env.example
Existing .env is never overwritten without --force. Secrets are not printed.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"
GENERATED_KEYS = ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "AGENT_DB_PASSWORD", "INGEST_DB_PASSWORD", "CONNECTOR_TOKEN",
                  "ONEC_DEMO_PASSWORD")


def add_missing() -> int:
    """Append keys from .env.example that are absent in .env (generated secrets for GENERATED_KEYS)."""
    existing = {line.split("=", 1)[0].strip() for line in TARGET.read_text(encoding="utf-8").splitlines() if "=" in line}
    added = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key in existing:
            continue
        added.append(f"{key}={secrets.token_hex(24)}" if key in GENERATED_KEYS else line)
    if added:
        with TARGET.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n# added by init_env.py --add-missing\n" + "\n".join(added) + "\n")
    print(f"added {len(added)} keys: {', '.join(a.split('=', 1)[0] for a in added) or 'none'}")
    return 0


def main() -> int:
    if "--add-missing" in sys.argv[1:] and TARGET.exists():
        return add_missing()
    force = "--force" in sys.argv[1:]
    if TARGET.exists() and not force:
        print(".env already exists - left untouched (use --force to regenerate)")
        return 0

    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        if key in GENERATED_KEYS:
            line = f"{key}={secrets.token_hex(24)}"
        lines.append(line)

    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        TARGET.chmod(0o600)
    except OSError:
        pass
    print(f"created {TARGET.name} with generated database passwords; fill LLM_API_KEY manually")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
