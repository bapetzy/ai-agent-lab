"""Step 3: end-to-end behaviour of one run on neutral types, driven by the scripted backend.
No API key, no network, no SDK, no local model."""

import dataclasses

from agentlab.backends.scripted import ScriptedBackend, call, reply
from agentlab.kernel import approve_all, deny_all
from agentlab.model_types import ModelError
from agentlab.run import start_run


def run(task, journal, tmp_path, script, approver=deny_all, **kw):
    backend = ScriptedBackend(script)
    result = start_run(task, journal, backend, approver=approver, runs_dir=tmp_path / "runs", **kw)
    return result, backend


def types(journal, run_id):
    return [e.type for e in journal.events(run_id)]


def last_results(backend, request_index):
    """The tool results the model saw at the start of request #request_index."""
    return backend.requests[request_index]["messages"][-1]["tool_results"]


def test_minimal_loop_without_tools(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [reply("Nothing to do.")])
    assert result.status == "completed"
    assert result.final_text == "Nothing to do."
    assert types(journal, result.run_id) == [
        "run_started",
        "model_request",
        "model_response",
        "check_result",
        "run_completed",
    ]


def test_successful_tool_call_full_cycle(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("read_file", {"path": "notes/todo.md"}, "t1")]),
        reply(
            tool_calls=[call("write_file", {"path": "out/summary.md", "content": "Ana, Ben"}, "t2")]
        ),
        reply("Done."),
    ]
    result, backend = run(task, journal, tmp_path, script)
    assert result.status == "completed"
    assert result.success is True
    assert types(journal, result.run_id) == [
        "run_started",
        "model_request",
        "model_response",
        "tool_requested",
        "permission_checked",
        "permission_approved",
        "tool_started",
        "tool_completed",
        "model_request",
        "model_response",
        "tool_requested",
        "permission_checked",
        "permission_approved",
        "tool_started",
        "tool_completed",
        "model_request",
        "model_response",
        "check_result",
        "run_completed",
    ]
    first_result = last_results(backend, 1)[0]
    assert first_result["call_id"] == "t1"
    assert "buy milk" in first_result["content"]
    assert first_result["is_error"] is False


def test_denied_permission_is_explained_to_the_model(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("write_file", {"path": "readme.txt", "content": "x"}, "t1")]),
        reply("I am not allowed to write there."),
    ]
    result, backend = run(task, journal, tmp_path, script)
    ev = {e.type: e for e in journal.events(result.run_id)}
    assert ev["permission_checked"].payload["decision"] == "deny"
    assert ev["permission_checked"].payload["rule"] == "default-deny"
    assert ev["permission_denied"].payload["by"] == "policy"
    assert "tool_started" not in ev
    tool_result = last_results(backend, 1)[0]
    assert tool_result["is_error"] is True
    assert "permission_denied" in tool_result["content"]
    assert (result.workspace / "readme.txt").read_text() == "Summaries go in out/."


def test_ask_goes_to_a_human_and_approval_lets_the_tool_run(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("write_file", {"path": "notes/todo.md", "content": "new"}, "t1")]),
        reply("ok"),
    ]
    result, _ = run(task, journal, tmp_path, script, approver=approve_all)
    assert types(journal, result.run_id)[3:9] == [
        "tool_requested",
        "permission_checked",
        "approval_requested",
        "approval_resolved",
        "permission_approved",
        "tool_started",
    ]
    assert (result.workspace / "notes" / "todo.md").read_text() == "new"


def test_ask_rejected_by_the_human_denies_the_tool(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("write_file", {"path": "notes/todo.md", "content": "new"}, "t1")]),
        reply("ok"),
    ]
    result, _ = run(task, journal, tmp_path, script, approver=deny_all)
    denied = [e for e in journal.events(result.run_id) if e.type == "permission_denied"][0]
    assert denied.payload["by"] == "human"
    assert (result.workspace / "notes" / "todo.md").read_text().startswith("- buy milk")


def test_tool_failure_is_reported_to_the_model_and_the_run_continues(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("read_file", {"path": "missing.md"}, "t1")]),
        reply("That file does not exist."),
    ]
    result, backend = run(task, journal, tmp_path, script)
    failed = [e for e in journal.events(result.run_id) if e.type == "tool_failed"][0]
    assert failed.payload["error_type"] == "WorkspaceError"
    assert last_results(backend, 1)[0]["is_error"] is True
    assert result.status == "completed"


def test_malformed_tool_call_is_preserved_and_rejected_safely(task, journal, tmp_path):
    script = [
        reply(
            tool_calls=[
                call("write_file", raw='{"path": "out/summary.md", "content": "Ana', id="t1")
            ]
        ),
        reply("My call was broken."),
    ]
    result, backend = run(task, journal, tmp_path, script)
    events = journal.events(result.run_id)
    requested = next(e for e in events if e.type == "tool_requested").payload
    assert requested["arguments"] is None
    assert requested["raw_arguments"] == '{"path": "out/summary.md", "content": "Ana'
    assert "not valid JSON" in requested["parse_error"]
    failed = next(e for e in events if e.type == "tool_failed").payload
    assert failed["error_type"] == "invalid_input"
    assert not any(e.type in ("permission_checked", "tool_started") for e in events)
    assert not (result.workspace / "out" / "summary.md").exists()  # nothing executed
    assert last_results(backend, 1)[0]["is_error"] is True
    # the malformed call is also kept, verbatim, in the history the model sees next
    assistant = backend.requests[1]["messages"][1]
    assert assistant["tool_calls"][0]["raw_arguments"].endswith('"Ana')


def test_path_escape_fails_before_the_policy_is_even_asked(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("read_file", {"path": "../../secret.txt"}, "t1")]),
        reply("ok"),
    ]
    result, _ = run(task, journal, tmp_path, script)
    seq = types(journal, result.run_id)
    assert "tool_failed" in seq and "permission_checked" not in seq


def test_invalid_tool_input_and_unknown_tools_fail_cleanly(task, journal, tmp_path):
    script = [
        reply(tool_calls=[call("read_file", {"wrong": 1}, "t1"), call("rm_rf", {}, "t2")]),
        reply("ok"),
    ]
    result, backend = run(task, journal, tmp_path, script)
    reasons = [
        e.payload["error_type"] for e in journal.events(result.run_id) if e.type == "tool_failed"
    ]
    assert reasons == ["invalid_input", "unknown_tool"]
    assert len(last_results(backend, 1)) == 2  # both answered together in one message


def test_tool_calls_are_not_executed_when_the_response_was_cut_off(task, journal, tmp_path):
    script = [
        reply(
            tool_calls=[call("write_file", {"path": "out/x.md", "content": "y"}, "t1")],
            finish_reason="length",
        )
    ]
    result, _ = run(task, journal, tmp_path, script)
    assert result.status == "failed" and result.reason == "finish_reason:length"
    assert "tool_requested" not in types(journal, result.run_id)


def test_turn_limit_stops_a_looping_agent(task, journal, tmp_path):
    loop = [reply(tool_calls=[call("list_files", {"path": "."}, f"t{i}")]) for i in range(20)]
    result, backend = run(task, journal, tmp_path, loop)
    assert result.status == "failed"
    assert result.reason.startswith("limit")
    assert len(backend.requests) == 6
    assert types(journal, result.run_id)[-2:] == ["limit_reached", "run_failed"]


def test_wall_clock_limit_stops_a_slow_run(task, journal, tmp_path):
    task.limits = dataclasses.replace(task.limits, max_wall_seconds=10)
    now = {"t": 0.0}

    def clock():
        now["t"] += 1.0  # every look at the clock costs one simulated second
        return now["t"]

    script = [reply(tool_calls=[call("list_files", {"path": "."}, f"t{i}")]) for i in range(6)]
    result, _ = run(task, journal, tmp_path, script, clock=clock)
    assert result.status == "failed" and "max_wall_seconds" in result.reason


def test_refusal_and_length_end_the_run_as_failed(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [reply("", finish_reason="refusal")])
    assert result.status == "failed" and result.reason == "finish_reason:refusal"
    result2, _ = run(task, journal, tmp_path, [reply("cut", finish_reason="length")])
    assert result2.reason == "finish_reason:length"


def test_backend_errors_end_the_run_with_a_model_reason(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [ModelError("unavailable", "server not running")])
    assert result.status == "failed" and result.reason == "model:model_error"
    assert types(journal, result.run_id)[-2:] == ["model_error", "run_failed"]


def test_every_event_carries_run_agent_and_task_ids(task, journal, tmp_path):
    script = [reply(tool_calls=[call("list_files", {"path": "."}, "t1")]), reply("ok")]
    result, _ = run(task, journal, tmp_path, script)
    events = journal.events(result.run_id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {e.agent_id for e in events} == {result.agent_id}
    assert {e.task_id for e in events} == {result.task_id}
    assert len({e.event_id for e in events}) == len(events)


def test_run_started_records_model_configuration(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [reply("ok")])
    started = journal.events(result.run_id)[0].payload
    assert started["schema_version"] == 2
    assert started["model"] == {
        "backend": "scripted",
        "name": "scripted-fake",
        "options": {},
        "temperature": None,
        "seed": None,
    }
    assert started["model_requested"] == "scripted-fake"
    assert started["backend"]["name"] == "scripted"
    assert started["fallback"] == "disabled"
    assert started["prices_per_mtok"] is None  # free backend
    assert started["limits"]["max_turns"] == 6
    assert started["policy"][0]["id"] == "read-all"
    assert set(started["tools"][0]) == {"name", "description", "parameters"}
    assert "notes/todo.md" in started["fixture"]


def test_secrets_registered_with_the_journal_never_reach_disk(task, tmp_path):
    from agentlab.journal import Journal

    secret = "sk-ant-api03-FAKE-KEY-FOR-TEST-0123456789"
    db = tmp_path / "lab.db"
    journal = Journal(db, secrets=[secret])
    run(task, journal, tmp_path, [reply(f"my key is {secret}")])
    journal.close()
    assert secret.encode() not in db.read_bytes()


def test_workspace_is_disposable_and_separate_per_run(task, journal, tmp_path, fixture_dir):
    script = [
        reply(tool_calls=[call("write_file", {"path": "out/summary.md", "content": "Ana"}, "t1")]),
        reply("ok"),
    ]
    r1, _ = run(task, journal, tmp_path, script)
    r2, _ = run(task, journal, tmp_path, [reply("ok")])
    assert r1.workspace != r2.workspace
    assert not (r2.workspace / "out").exists()
    assert not (fixture_dir / "out").exists()


def test_spend_cap_still_works_for_priced_backends(task, journal, tmp_path):
    class PricedScripted(ScriptedBackend):
        def prices(self, model):
            return {"input": 4.0, "output": 20.0}

    task.limits = dataclasses.replace(task.limits, max_cost_usd=0.05, max_tokens_per_response=1500)
    script = [
        reply(
            tool_calls=[call("list_files", {"path": "."}, "t1")],
            usage={"input_tokens": 5000, "output_tokens": 500},
        ),  # $0.03
        reply("never reached"),
    ]
    backend = PricedScripted(script)
    result = start_run(task, journal, backend, approver=deny_all, runs_dir=tmp_path / "runs")
    assert result.status == "failed" and "max_cost_usd" in result.reason
    assert len(backend.requests) == 1  # 0.03 spent + up to 0.03 more output > 0.05
