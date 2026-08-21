"""Print a fresh PII encryption key.

    python -m scripts.generate_pii_key

Copy the line it prints into your .env. Keep it out of git, out of chat, and out
of screenshots: it is the only thing standing between a stolen backup and your
customers' phone numbers.

Losing it does not corrupt anything - guides, money and metrics are untouched -
but every stored name and phone becomes permanently unreadable, and the only way
back is re-uploading the source files.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.crypto import ENV_KEY, generate_key

if __name__ == "__main__":
    print(f"{ENV_KEY}={generate_key()}")
