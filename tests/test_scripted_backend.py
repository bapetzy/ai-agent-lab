"""Step 2: the provider-neutral scripted backend."""

import pytest

from agentlab.backends.scripted import ScriptedBackend, call, load_script, reply
from agentlab.model_types import Message, ModelError, ModelRequest


def request(text="hi", model="scripted-fake"):
    return ModelRequest(
        model=model,
        system="s",
        messages=(Message("user", text=text),),
        tools=(),
        max_output_tokens=10,
    )


def test_replies_in_order_and_records_every_request():
    backend = ScriptedBackend(
        [
            reply(tool_calls=[call("read_file", {"path": "a.md"}, "c1")]),
            reply(text="done"),
        ]
    )
    first = backend.complete(request("one"))
    second = backend.complete(request("two"))
    assert first.finish_reason == "tool_calls"
    assert first.tool_calls[0].arguments == {"path": "a.md"}
    assert second.finish_reason == "stop" and second.text == "done"
    assert [r["messages"][0]["text"] for r in backend.requests] == ["one", "two"]


def test_malformed_arguments_can_be_scripted():
    backend = ScriptedBackend([reply(tool_calls=[call("read_file", raw='{"path": ', id="c1")])])
    tc = backend.complete(request()).tool_calls[0]
    assert tc.arguments is None
    assert tc.raw_arguments == '{"path": '
    assert "not valid JSON" in tc.parse_error


def test_errors_can_be_scripted():
    backend = ScriptedBackend(
        [{"error": {"kind": "transient", "message": "busy", "status": 529}}, reply(text="ok")]
    )
    with pytest.raises(ModelError) as e:
        backend.complete(request())
    assert e.value.kind == "transient" and e.value.status == 529
    assert backend.complete(request()).text == "ok"


def test_running_out_of_script_is_a_permanent_model_error():
    backend = ScriptedBackend([])
    with pytest.raises(ModelError) as e:
        backend.complete(request())
    assert e.value.kind == "permanent"
    assert e.value.error_type == "ScriptExhausted"


def test_scripted_backend_is_free_and_describes_itself():
    backend = ScriptedBackend([reply(text="x")])
    assert backend.prices("scripted-fake") is None
    assert backend.describe()["name"] == "scripted"
    assert backend.wire_request(request()) == request().to_dict()


def test_shipped_demo_script_is_provider_neutral():
    from conftest import ROOT

    script = load_script(ROOT / "tasks" / "demo-permissions" / "scripted.json")
    assert all("content" not in item and "stop_reason" not in item for item in script)
    backend = ScriptedBackend(script)
    assert backend.complete(request()).tool_calls[0].name == "list_files"
