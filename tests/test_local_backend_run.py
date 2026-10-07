"""Step 7 end to end: task -> make_backend("openai_compat") -> kernel -> stub llama-server.
Still no llama.cpp, no model, no internet: the 'server' is an in-process stub."""

import pytest
from conftest import ROOT
from test_openai_compat import MODEL, Stub, completion, dead_port

from agentlab.backends import BackendNotAvailable, make_backend
from agentlab.backends.openai_compat import OpenAICompatBackend
from agentlab.cli import main
from agentlab.kernel import deny_all
from agentlab.run import start_run
from agentlab.task import Task


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def local_task(fixture_dir, base_url):
    return Task.from_dict(
        {
            "task": {"name": "local-test", "prompt": "Summarize notes/todo.md into out/summary.md"},
            "model": {
                "backend": "openai_compat",
                "name": MODEL,
                "base_url": base_url,
                "temperature": 0.0,
                "seed": 1,
                "options": {"enable_thinking": False},
            },
            "agent": {"system": "You are careful.", "tools": ["read_file", "write_file"]},
            "limits": {"max_turns": 5},
            "policy": [
                {"id": "read-all", "capability": "filesystem.read", "decision": "allow"},
                {
                    "id": "write-out",
                    "capability": "filesystem.write",
                    "paths": ["out/**"],
                    "decision": "allow",
                },
            ],
            "check": [{"type": "file_contains", "path": "out/summary.md", "text": "Ana"}],
        },
        workspace_fixture=fixture_dir,
    )


def call(id, name, arguments: str) -> dict:
    return {"id": id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_make_backend_builds_the_local_backend(fixture_dir, stub):
    backend = make_backend(local_task(fixture_dir, stub.url))
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.base_url == stub.url


def test_make_backend_refuses_non_local_addresses(fixture_dir):
    with pytest.raises(BackendNotAvailable, match="refusing non-local"):
        make_backend(local_task(fixture_dir, "http://192.168.1.50:8080"))


def test_base_url_is_kept_in_the_model_section(fixture_dir):
    assert local_task(fixture_dir, "http://127.0.0.1:9999").model["base_url"] == (
        "http://127.0.0.1:9999"
    )


def test_full_run_through_the_kernel_against_a_stub_server(fixture_dir, stub, journal, tmp_path):
    stub.replies += [
        (
            200,
            completion(
                {
                    "content": None,
                    "tool_calls": [call("c1", "read_file", '{"path": "notes/todo.md"}')],
                },
                finish="tool_calls",
                timings={"prompt_n": 120, "cache_n": 0, "predicted_n": 9},
            ),
        ),
        (
            200,
            completion(
                {
                    "content": None,
                    "tool_calls": [
                        call("c2", "write_file", '{"path": "readme.txt", "content": "x"}'),
                        call(
                            "c3", "write_file", '{"path": "out/summary.md", "content": "Ana'
                        ),  # malformed
                        call(
                            "c4", "write_file", '{"path": "out/summary.md", "content": "Ana, Ben"}'
                        ),
                    ],
                },
                finish="tool_calls",
                timings={"prompt_n": 40, "cache_n": 120, "predicted_n": 30},
            ),
        ),
        (200, completion({"content": "Wrote the summary."}, finish="stop")),
    ]
    task = local_task(fixture_dir, stub.url)
    result = start_run(
        task, journal, make_backend(task), approver=deny_all, runs_dir=tmp_path / "runs"
    )
    assert result.status == "completed" and result.success is True

    events = journal.events(result.run_id)
    started = events[0].payload
    assert started["backend"]["name"] == "openai_compat"
    assert started["backend"]["server"]["build_info"] == "b11461"
    assert started["prices_per_mtok"] is None
    failed = {
        e.payload["tool_call_id"]: e.payload["error_type"]
        for e in events
        if e.type == "tool_failed"
    }
    assert failed == {"c3": "invalid_input"}  # malformed: recorded and rejected
    denied = [e.payload["tool_call_id"] for e in events if e.type == "permission_denied"]
    assert denied == ["c2"]
    responses = [e.payload for e in events if e.type == "model_response"]
    assert responses[1]["usage"]["cached_input_tokens"] == 120
    assert responses[1]["timings"]["cache_n"] == 120

    # what the "model" saw on its third turn: every result, in the OpenAI tool format
    third = stub.requests[2]["body"]["messages"]
    tool_msgs = [m for m in third if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["c1", "c2", "c3", "c4"]
    assert tool_msgs[1]["content"].startswith("ERROR: ")  # permission denied
    assert tool_msgs[2]["content"].startswith("ERROR: ")  # malformed call
    assert not tool_msgs[3]["content"].startswith("ERROR: ")
    assert stub.requests[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_run_with_no_local_server_fails_clearly(fixture_dir, journal, tmp_path):
    task = local_task(fixture_dir, f"http://127.0.0.1:{dead_port()}")
    result = start_run(
        task, journal, make_backend(task), approver=deny_all, runs_dir=tmp_path / "runs"
    )
    assert result.status == "failed" and result.reason == "model:model_error"
    err = [e.payload for e in journal.events(result.run_id) if e.type == "model_error"][0]
    assert err["kind"] == "unavailable" and "llama-server" in err["message"]
    assert [e.type for e in journal.events(result.run_id)].count("model_retry") == 0


def test_cli_reports_a_missing_local_server(tmp_path, capsys):
    import shutil

    task_dir = tmp_path / "task"
    shutil.copytree(ROOT / "tasks" / "e001-meeting-actions", task_dir)
    toml = task_dir / "task.toml"
    text = toml.read_text(encoding="utf-8").replace(
        'base_url = "http://127.0.0.1:8080"', f'base_url = "http://127.0.0.1:{dead_port()}"'
    )
    toml.write_text(text, encoding="utf-8")
    code = main(
        [
            "run",
            str(task_dir),
            "--db",
            str(tmp_path / "lab.db"),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--approve",
            "no",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "OPENAI_COMPAT" in out and "cannot reach the local model server" in out
