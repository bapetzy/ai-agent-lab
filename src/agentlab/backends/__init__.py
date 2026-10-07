"""Model backends. Each one translates between the kernel's types (model_types.py) and one
way of producing model output. Only modules in this package may know a provider's format.

- scripted.py   pre-written responses; default for tests and offline demos (IMPLEMENTED)
- anthropic.py  optional external API adapter; off unless explicitly enabled (IMPLEMENTED;
                needs the optional `anthropic` extra, a flag and a key)
- openai_compat (local llama.cpp `llama-server`): PROPOSED, docs/MODEL-GATEWAY.md step 7
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from agentlab.backends.scripted import ScriptedBackend, load_script

ENABLE_FLAG = "AGENTLAB_ENABLE_EXTERNAL_API"


class BackendNotAvailable(Exception):
    """The task's backend cannot be used right now; the message says why and what to do."""


def make_backend(task, *, scripted: str | Path | None = None):
    """Build the backend a run should use. A scripted file always wins (offline)."""
    if scripted is not None:
        return ScriptedBackend(load_script(scripted))
    kind = task.model["backend"]
    if kind == "scripted":
        raise BackendNotAvailable(
            "this task uses the scripted backend: pass --scripted FILE with the responses"
        )
    if kind == "openai_compat":
        raise BackendNotAvailable(
            "backend 'openai_compat' (local llama.cpp server) is not implemented yet "
            "(docs/MODEL-GATEWAY.md step 7). Run offline with --scripted FILE."
        )
    if kind == "anthropic":
        if os.environ.get(ENABLE_FLAG) != "1":
            raise BackendNotAvailable(
                f"the Anthropic backend is optional and off by default (free-first). To use it "
                f"you must set {ENABLE_FLAG}=1, install the extra (uv sync --extra anthropic) "
                "and provide ANTHROPIC_API_KEY. It is never required."
            )
        if importlib.util.find_spec("anthropic") is None:
            raise BackendNotAvailable(
                "the anthropic package is not installed: uv sync --extra anthropic"
            )
        from agentlab import config
        from agentlab.backends.anthropic import AnthropicBackend

        key = config.api_key()
        if not key:
            raise BackendNotAvailable("ANTHROPIC_API_KEY is not set (environment or .env)")
        return AnthropicBackend(api_key=key, timeout=task.limits.request_timeout_s)
    raise BackendNotAvailable(f"unknown backend {kind!r}")


def secrets_of(backend) -> list[str]:
    """Strings the journal must never store (e.g. an API key)."""
    return [s for s in [getattr(backend, "api_key", None)] if s]
