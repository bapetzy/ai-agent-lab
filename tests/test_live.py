"""Live API smoke tests. Deselected by default; they cost money.

uv run pytest -m live
"""

import pytest

from agentlab import config

pytestmark = pytest.mark.live


@pytest.fixture
def api_key():
    key = config.api_key()
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set")
    return key


def test_one_tiny_live_call_records_requested_and_actual_model(api_key):
    import anthropic

    from agentlab.gateway import ModelGateway
    from agentlab.journal import Journal, Recorder

    journal = Journal(":memory:", secrets=[api_key])
    rec = Recorder(journal, "live-run", "live-agent", "live-task")
    gw = ModelGateway(anthropic.Anthropic(api_key=api_key, max_retries=0), rec)
    out = gw.call(
        {
            "model": "claude-opus-5-5",
            "max_tokens": 1000,
            "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": "Reply with the single word OK."}],
        }
    )
    resp = journal.events("live-run")[-1].payload
    assert resp["actual_model"] == resp["requested_model"] == "claude-opus-5-5"
    assert resp["request_id"]
    assert out["usage"]["output_tokens"] > 0
