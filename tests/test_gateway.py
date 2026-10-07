import anthropic
import httpx2
import pytest

from agentlab.gateway import ModelCallFailed, ModelGateway
from agentlab.journal import Journal, Recorder
from agentlab.scripted import ScriptedClient, response, text

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def params(messages, model="claude-opus-5-5"):
    return {
        "model": model,
        "max_tokens": 1000,
        "system": "You are a test.",
        "tools": [],
        "output_config": {"effort": "medium"},
        "messages": messages,
    }


@pytest.fixture
def rec():
    return Recorder(Journal(":memory:"), run_id="run-1", agent_id="ag-1", task_id="t-1")


def test_request_and_response_are_recorded_with_metadata(rec):
    client = ScriptedClient(
        [
            response(
                [text("hi")],
                "end_turn",
                model="claude-opus-5-5",
                usage={"input_tokens": 12, "output_tokens": 3},
            )
        ]
    )
    gw = ModelGateway(client, rec)
    out = gw.call(params([{"role": "user", "content": "hello"}]))
    assert out["stop_reason"] == "end_turn"

    req_ev, resp_ev = rec.journal.events("run-1")
    assert req_ev.type == "model_request"
    assert req_ev.payload["model"] == "claude-opus-5-5"
    assert req_ev.payload["output_config"] == {"effort": "medium"}
    body = rec.journal.get_blob(req_ev.payload["request_blob"])
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["system"] == "You are a test."

    assert resp_ev.type == "model_response"
    assert resp_ev.payload["requested_model"] == "claude-opus-5-5"
    assert resp_ev.payload["actual_model"] == "claude-opus-5-5"
    assert resp_ev.payload["usage"]["input_tokens"] == 12
    assert resp_ev.payload["stop_reason"] == "end_turn"
    assert "request_id" in resp_ev.payload
    assert rec.journal.get_blob(resp_ev.payload["response_blob"])["content"][0]["text"] == "hi"


def test_a_different_actual_model_fails_instead_of_silently_switching(rec):
    client = ScriptedClient([response([text("hi")], "end_turn", model="claude-opus-5")])
    gw = ModelGateway(client, rec)
    with pytest.raises(ModelCallFailed) as err:
        gw.call(params([{"role": "user", "content": "hello"}]))
    assert err.value.reason == "model_mismatch"
    resp_ev = rec.journal.events("run-1")[-1]
    assert resp_ev.payload["actual_model"] == "claude-opus-5"  # still recorded


def test_history_must_only_grow_by_appending(rec):
    client = ScriptedClient([response([text("a")], "end_turn", model="claude-opus-5-5")] * 2)
    gw = ModelGateway(client, rec)
    gw.call(params([{"role": "user", "content": "first"}]))
    with pytest.raises(ModelCallFailed) as err:
        gw.call(params([{"role": "user", "content": "EDITED"}]))
    assert err.value.reason == "prefix_violation"
    assert len(client.requests) == 1  # the bad request never left the kernel


def test_transient_errors_are_retried_and_each_attempt_is_journaled(rec):
    rate = anthropic.RateLimitError(
        "slow down", response=httpx2.Response(429, request=REQ), body=None
    )
    client = ScriptedClient([rate, response([text("ok")], "end_turn", model="claude-opus-5-5")])
    gw = ModelGateway(client, rec, sleep=lambda s: None)
    gw.call(params([{"role": "user", "content": "x"}]))
    types = [e.type for e in rec.journal.events("run-1")]
    assert types == ["model_request", "model_retry", "model_response"]


def test_permanent_errors_are_recorded_and_raised(rec):
    bad = anthropic.BadRequestError(
        "invalid", response=httpx2.Response(400, request=REQ), body=None
    )
    gw = ModelGateway(ScriptedClient([bad]), rec, sleep=lambda s: None)
    with pytest.raises(ModelCallFailed) as err:
        gw.call(params([{"role": "user", "content": "x"}]))
    assert err.value.reason == "api_error"
    last = rec.journal.events("run-1")[-1]
    assert last.type == "model_error"
    assert last.payload["status_code"] == 400
    assert last.payload["error_type"] == "BadRequestError"


def test_retries_give_up_after_max_attempts(rec):
    conn = anthropic.APIConnectionError(request=REQ)
    gw = ModelGateway(ScriptedClient([conn, conn, conn]), rec, max_attempts=3, sleep=lambda s: None)
    with pytest.raises(ModelCallFailed):
        gw.call(params([{"role": "user", "content": "x"}]))
    types = [e.type for e in rec.journal.events("run-1")]
    assert types == ["model_request", "model_retry", "model_retry", "model_error"]
