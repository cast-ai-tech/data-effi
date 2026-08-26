"""Print a fresh credential vault key.

    python -m scripts.generate_vault_key

Copy the line it prints into your .env. Keep it out of git, out of chat, and out
of screenshots: it is the only thing standing between a stolen backup and every
merchant's password for their fulfillment platform.

Losing it is recoverable but visible. Nothing in the database is corrupted, no
guide or peso is lost, but every stored credential becomes unreadable and each
merchant has to re-enter their username and password once. The connections page
tells them so instead of failing silently.

This key is SEPARATE from PII_ENCRYPTION_KEY on purpose. Rotating one must not
break the other - see pipeline/vault.py for the reasoning.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.vault import ENV_KEY, generate_key

if __name__ == "__main__":
    print(f"{ENV_KEY}={generate_key()}")
