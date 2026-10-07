"""Optional live test of the Anthropic adapter. Deselected by default; costs money; needs the
`anthropic` extra, AGENTLAB_ENABLE_EXTERNAL_API=1 and a key. Never required (free-first).

    uv run --extra anthropic pytest -m live
"""

import importlib.util
import os

import pytest

from agentlab import config

pytestmark = pytest.mark.live


def test_one_tiny_live_call_records_requested_and_actual_model():
    if importlib.util.find_spec("anthropic") is None:
        pytest.skip("anthropic extra not installed")
    if os.environ.get("AGENTLAB_ENABLE_EXTERNAL_API") != "1" or not config.api_key():
        pytest.skip("external API not enabled")

    from agentlab.backends.anthropic import AnthropicBackend
    from agentlab.gateway import ModelGateway
    from agentlab.journal import Journal, Recorder
    from agentlab.model_types import Message, ModelRequest

    key = config.api_key()
    journal = Journal(":memory:", secrets=[key])
    rec = Recorder(journal, "live-run", "live-agent", "live-task")
    gw = ModelGateway(AnthropicBackend(api_key=key), rec)
    out = gw.call(
        ModelRequest(
            model="claude-opus-5-5",
            system="Be brief.",
            messages=(Message("user", text="Reply with the single word OK."),),
            tools=(),
            max_output_tokens=1000,
            options={"effort": "low"},
        )
    )
    resp = journal.events("live-run")[-1].payload
    assert resp["actual_model"] == resp["requested_model"] == "claude-opus-5-5"
    assert out.usage.output_tokens > 0
