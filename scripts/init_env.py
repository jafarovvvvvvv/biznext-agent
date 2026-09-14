"""Create .env from .env.example with random URL-safe database passwords.

Usage: python scripts/init_env.py [--force]
Existing .env is never overwritten without --force. Secrets are not printed.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"
GENERATED_KEYS = ("POSTGRES_PASSWORD", "APP_DB_PASSWORD", "AGENT_DB_PASSWORD")


def main() -> int:
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
