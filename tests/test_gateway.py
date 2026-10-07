"""Step 4: the gateway over any backend."""

import pytest

from agentlab.backends.scripted import ScriptedBackend, call, reply
from agentlab.gateway import ModelCallFailed, ModelGateway
from agentlab.journal import Journal, Recorder
from agentlab.model_types import Message, ModelError, ModelRequest


def req(*texts, model="scripted-fake", system="You are a test."):
    msgs = tuple(Message("user", text=t) for t in texts)
    return ModelRequest(
        model=model,
        system=system,
        messages=msgs,
        tools=(),
        max_output_tokens=1000,
        options={"effort": "medium"},
    )


@pytest.fixture
def rec():
    return Recorder(Journal(":memory:"), run_id="run-1", agent_id="ag-1", task_id="t-1")


def test_request_and_response_are_recorded_in_neutral_form(rec):
    backend = ScriptedBackend([reply("hi", usage={"input_tokens": 12, "output_tokens": 3})])
    out = ModelGateway(backend, rec).call(req("hello"))
    assert out.finish_reason == "stop"

    req_ev, resp_ev = rec.journal.events("run-1")
    assert req_ev.type == "model_request"
    assert req_ev.payload["backend"] == "scripted"
    assert req_ev.payload["model"] == "scripted-fake"
    assert req_ev.payload["options"] == {"effort": "medium"}
    body = rec.journal.get_blob(req_ev.payload["request_blob"])
    assert body["messages"][0]["text"] == "hello"
    assert rec.journal.get_blob(req_ev.payload["wire_blob"]) == body  # scripted wire = neutral

    p = resp_ev.payload
    assert resp_ev.type == "model_response"
    assert p["requested_model"] == p["actual_model"] == "scripted-fake"
    assert p["finish_reason"] == "stop"
    assert p["usage"]["input_tokens"] == 12
    assert rec.journal.get_blob(p["response_blob"])["text"] == "hi"


def test_tool_calls_are_recorded_with_raw_arguments_and_parse_errors(rec):
    backend = ScriptedBackend(
        [
            reply(
                tool_calls=[
                    call("read_file", {"path": "a"}, "c1"),
                    call("read_file", raw="{oops", id="c2"),
                ]
            )
        ]
    )
    ModelGateway(backend, rec).call(req("x"))
    calls = rec.journal.events("run-1")[-1].payload["tool_calls"]
    assert calls[0]["arguments"] == {"path": "a"} and calls[0]["parse_error"] is None
    assert calls[1]["arguments"] is None and calls[1]["raw_arguments"] == "{oops"
    assert "not valid JSON" in calls[1]["parse_error"]


def test_a_different_actual_model_fails_instead_of_silently_switching(rec):
    backend = ScriptedBackend([reply("hi", model="some-other-model")])
    with pytest.raises(ModelCallFailed) as err:
        ModelGateway(backend, rec).call(req("hello"))
    assert err.value.reason == "model_mismatch"
    assert rec.journal.events("run-1")[-1].payload["actual_model"] == "some-other-model"


def test_history_must_only_grow_by_appending(rec):
    backend = ScriptedBackend([reply("a"), reply("b")])
    gw = ModelGateway(backend, rec)
    gw.call(req("first"))
    with pytest.raises(ModelCallFailed) as err:
        gw.call(req("EDITED"))
    assert err.value.reason == "prefix_violation"
    assert len(backend.requests) == 1  # the bad request never reached the backend


def test_changing_the_system_prompt_mid_run_is_a_prefix_violation(rec):
    gw = ModelGateway(ScriptedBackend([reply("a"), reply("b")]), rec)
    gw.call(req("first"))
    with pytest.raises(ModelCallFailed, match="system"):
        gw.call(req("first", "second", system="changed"))


def test_transient_errors_are_retried_and_each_attempt_is_journaled(rec):
    backend = ScriptedBackend([ModelError("transient", "busy", status=529), reply("ok")])
    ModelGateway(backend, rec, sleep=lambda s: None).call(req("x"))
    assert [e.type for e in rec.journal.events("run-1")] == [
        "model_request",
        "model_retry",
        "model_response",
    ]


def test_permanent_errors_are_recorded_and_raised(rec):
    backend = ScriptedBackend(
        [ModelError("permanent", "bad", status=400, error_type="BadRequestError")]
    )
    with pytest.raises(ModelCallFailed) as err:
        ModelGateway(backend, rec, sleep=lambda s: None).call(req("x"))
    assert err.value.reason == "model_error"
    last = rec.journal.events("run-1")[-1]
    assert last.type == "model_error"
    assert last.payload["status_code"] == 400
    assert last.payload["error_type"] == "BadRequestError"
    assert last.payload["kind"] == "permanent"


def test_unavailable_backend_fails_fast_without_retrying(rec):
    backend = ScriptedBackend([ModelError("unavailable", "server not running"), reply("never")])
    with pytest.raises(ModelCallFailed):
        ModelGateway(backend, rec, sleep=lambda s: None).call(req("x"))
    assert [e.type for e in rec.journal.events("run-1")] == ["model_request", "model_error"]


def test_retries_give_up_after_max_attempts(rec):
    errs = [ModelError("transient", "busy")] * 3
    with pytest.raises(ModelCallFailed):
        ModelGateway(ScriptedBackend(errs), rec, max_attempts=3, sleep=lambda s: None).call(
            req("x")
        )
    assert [e.type for e in rec.journal.events("run-1")] == [
        "model_request",
        "model_retry",
        "model_retry",
        "model_error",
    ]
