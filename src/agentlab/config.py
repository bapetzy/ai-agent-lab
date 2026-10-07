"""Where the API key comes from: the ANTHROPIC_API_KEY environment variable, or a ``.env``
file in the project root (git-ignored). The key is only ever handed to the SDK client and
to the journal's redaction list. It is never stored, printed, or put in a request body.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV = PROJECT_ROOT / ".env"


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or _read_dotenv(DOTENV).get("ANTHROPIC_API_KEY")
