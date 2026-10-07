"""The E000 script's loop logic, checked offline with a tiny fake client.

E000 is an optional, Anthropic-specific experiment (it needs the `anthropic` extra and a paid
key to run live). Its loop logic is tested here without either."""

import copy
import importlib.util

from conftest import ROOT


def load_bare_loop():
    path = ROOT / "experiments" / "000-bare-agent-loop" / "bare_loop.py"
    spec = importlib.util.spec_from_file_location("bare_loop", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMessage:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return self.data


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.messages = self

    def create(self, **params):
        self.requests.append(copy.deepcopy(params))  # the loop keeps mutating its list
        return FakeMessage(self.replies.pop(0))


def test_bare_loop_runs_one_tool_cycle_without_the_sdk(capsys):
    bare = load_bare_loop()
    client = FakeClient(
        [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "read_note", "input": {"name": "todo"}}
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Call Ana and send Ben the agenda."}],
            },
        ]
    )
    final = bare.bare_loop(client, "What is on my todo note?")
    assert final == "Call Ana and send Ben the agenda."
    second = client.requests[1]["messages"]
    assert second[-1]["content"][0]["content"].startswith("call Ana")
    assert "REQUEST" in capsys.readouterr().out
