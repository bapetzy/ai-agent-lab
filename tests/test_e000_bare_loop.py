"""The E000 script's loop logic, checked offline with a scripted model."""

import importlib.util

from conftest import ROOT

from agentlab.scripted import ScriptedClient, response, text, tool_use


def load_bare_loop():
    path = ROOT / "experiments" / "000-bare-agent-loop" / "bare_loop.py"
    spec = importlib.util.spec_from_file_location("bare_loop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bare_loop_runs_one_tool_cycle(capsys):
    bare = load_bare_loop()
    client = ScriptedClient(
        [
            response([tool_use("read_note", {"name": "todo"}, "t1")], "tool_use", model=bare.MODEL),
            response([text("Call Ana and send Ben the agenda.")], "end_turn", model=bare.MODEL),
        ]
    )
    final = bare.bare_loop(client, "What is on my todo note?")
    assert final == "Call Ana and send Ben the agenda."
    second_request = client.requests[1]["messages"]
    assert second_request[-1]["content"][0]["content"].startswith("call Ana")
    assert "REQUEST" in capsys.readouterr().out
