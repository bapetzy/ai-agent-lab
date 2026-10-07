"""End-to-end behaviour of one run, driven by a scripted (fake) model. No API key needed."""

from agentlab.kernel import approve_all, deny_all
from agentlab.run import start_run
from agentlab.scripted import ScriptedClient, response, text, tool_use

FAKE_PRICES = {"input": 0.0, "output": 0.0}


def run(task, journal, tmp_path, responses, approver=deny_all, **kw):
    client = ScriptedClient(responses)
    result = start_run(
        task,
        journal,
        client,
        approver=approver,
        runs_dir=tmp_path / "runs",
        prices=FAKE_PRICES,
        model_backend="scripted",
        **kw,
    )
    return result, client


def types(journal, run_id):
    return [e.type for e in journal.events(run_id)]


def test_minimal_loop_without_tools(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [response([text("Nothing to do.")], "end_turn")])
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
        response([tool_use("read_file", {"path": "notes/todo.md"}, "t1")], "tool_use"),
        response(
            [tool_use("write_file", {"path": "out/summary.md", "content": "Ana, Ben"}, "t2")],
            "tool_use",
        ),
        response([text("Done.")], "end_turn"),
    ]
    result, client = run(task, journal, tmp_path, script)
    assert result.status == "completed"
    assert result.success is True  # check: out/summary.md contains "Ana"
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
    # the tool result went back to the model in the next request
    second = client.requests[1]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "t1"
    assert "buy milk" in second[-1]["content"][0]["content"]


def test_denied_permission_is_explained_to_the_model(task, journal, tmp_path):
    script = [
        response(
            [tool_use("write_file", {"path": "readme.txt", "content": "x"}, "t1")], "tool_use"
        ),
        response([text("I am not allowed to write there.")], "end_turn"),
    ]
    result, client = run(task, journal, tmp_path, script)
    ev = {e.type: e for e in journal.events(result.run_id)}
    assert ev["permission_checked"].payload["decision"] == "deny"
    assert ev["permission_checked"].payload["rule"] == "default-deny"
    assert ev["permission_denied"].payload["by"] == "policy"
    assert "tool_started" not in ev  # the tool never ran
    tool_result = client.requests[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "permission_denied" in tool_result["content"]
    assert (result.workspace / "readme.txt").read_text() == "Summaries go in out/."


def test_ask_goes_to_a_human_and_approval_lets_the_tool_run(task, journal, tmp_path):
    script = [
        response(
            [tool_use("write_file", {"path": "notes/todo.md", "content": "new"}, "t1")], "tool_use"
        ),
        response([text("ok")], "end_turn"),
    ]
    result, _ = run(task, journal, tmp_path, script, approver=approve_all)
    seq = types(journal, result.run_id)
    assert seq[3:9] == [
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
        response(
            [tool_use("write_file", {"path": "notes/todo.md", "content": "new"}, "t1")], "tool_use"
        ),
        response([text("ok")], "end_turn"),
    ]
    result, _ = run(task, journal, tmp_path, script, approver=deny_all)
    denied = [e for e in journal.events(result.run_id) if e.type == "permission_denied"][0]
    assert denied.payload["by"] == "human"
    assert (result.workspace / "notes" / "todo.md").read_text().startswith("- buy milk")


def test_tool_failure_is_reported_to_the_model_and_the_run_continues(task, journal, tmp_path):
    script = [
        response([tool_use("read_file", {"path": "missing.md"}, "t1")], "tool_use"),
        response([text("That file does not exist.")], "end_turn"),
    ]
    result, client = run(task, journal, tmp_path, script)
    failed = [e for e in journal.events(result.run_id) if e.type == "tool_failed"][0]
    assert failed.payload["error_type"] == "WorkspaceError"
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert result.status == "completed"


def test_path_escape_fails_before_the_policy_is_even_asked(task, journal, tmp_path):
    script = [
        response([tool_use("read_file", {"path": "../../secret.txt"}, "t1")], "tool_use"),
        response([text("ok")], "end_turn"),
    ]
    result, _ = run(task, journal, tmp_path, script)
    seq = types(journal, result.run_id)
    assert "tool_failed" in seq and "permission_checked" not in seq


def test_invalid_tool_input_and_unknown_tools_fail_cleanly(task, journal, tmp_path):
    script = [
        response(
            [tool_use("read_file", {"wrong": 1}, "t1"), tool_use("rm_rf", {}, "t2")], "tool_use"
        ),
        response([text("ok")], "end_turn"),
    ]
    result, client = run(task, journal, tmp_path, script)
    reasons = [
        e.payload["error_type"] for e in journal.events(result.run_id) if e.type == "tool_failed"
    ]
    assert reasons == ["invalid_input", "unknown_tool"]
    # both results returned together in ONE user message
    assert len(client.requests[1]["messages"][-1]["content"]) == 2


def test_turn_limit_stops_a_looping_agent(task, journal, tmp_path):
    loop = [
        response([tool_use("list_files", {"path": "."}, f"t{i}")], "tool_use") for i in range(20)
    ]
    result, client = run(task, journal, tmp_path, loop)
    assert result.status == "failed"
    assert result.reason.startswith("limit")
    assert len(client.requests) == 6  # max_turns in the task
    assert types(journal, result.run_id)[-2:] == ["limit_reached", "run_failed"]


def test_refusal_and_max_tokens_end_the_run_as_failed(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [response([], "refusal")])
    assert result.status == "failed" and result.reason == "stop_reason:refusal"
    result2, _ = run(task, journal, tmp_path, [response([text("cut")], "max_tokens")])
    assert result2.reason == "stop_reason:max_tokens"


def test_every_event_carries_run_agent_and_task_ids(task, journal, tmp_path):
    script = [
        response([tool_use("list_files", {"path": "."}, "t1")], "tool_use"),
        response([text("ok")], "end_turn"),
    ]
    result, _ = run(task, journal, tmp_path, script)
    events = journal.events(result.run_id)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {e.agent_id for e in events} == {result.agent_id}
    assert {e.task_id for e in events} == {result.task_id}
    assert len({e.event_id for e in events}) == len(events)


def test_run_started_records_model_configuration(task, journal, tmp_path):
    result, _ = run(task, journal, tmp_path, [response([text("ok")], "end_turn")])
    started = journal.events(result.run_id)[0].payload
    assert started["agent"]["model"] == "scripted-fake"
    assert started["model_backend"] == "scripted"
    assert started["fallback"] == "disabled"
    assert started["limits"]["max_turns"] == 6
    assert started["policy"][0]["id"] == "read-all"
    assert started["tools"][0]["name"] == "list_files"
    assert "notes/todo.md" in started["fixture"]


def test_the_api_key_never_reaches_the_journal(task, tmp_path):
    from agentlab.journal import Journal

    secret = "sk-ant-api03-FAKE-KEY-FOR-TEST-0123456789"
    db = tmp_path / "lab.db"
    journal = Journal(db, secrets=[secret])
    script = [response([text(f"my key is {secret}")], "end_turn")]
    run(task, journal, tmp_path, script)
    journal.close()
    assert secret.encode() not in db.read_bytes()


def test_workspace_is_disposable_and_separate_per_run(task, journal, tmp_path, fixture_dir):
    script = [
        response(
            [tool_use("write_file", {"path": "out/summary.md", "content": "Ana"}, "t1")], "tool_use"
        ),
        response([text("ok")], "end_turn"),
    ]
    r1, _ = run(task, journal, tmp_path, script)
    r2, _ = run(task, journal, tmp_path, [response([text("ok")], "end_turn")])
    assert r1.workspace != r2.workspace
    assert not (r2.workspace / "out").exists()
    assert not (fixture_dir / "out").exists()  # fixture untouched


def test_spend_cap_stops_the_run_before_an_expensive_call(task, journal, tmp_path):
    import dataclasses

    # Price the fake model like Opus 5.5 and allow only $0.05 in total.
    task.limits = dataclasses.replace(task.limits, max_cost_usd=0.05, max_tokens_per_response=1500)
    script = [
        response(
            [tool_use("list_files", {"path": "."}, "t1")],
            "tool_use",
            usage={"input_tokens": 5000, "output_tokens": 500},  # $0.02 + $0.01 = $0.03
        ),
        response([text("never reached")], "end_turn"),
    ]
    client = ScriptedClient(script)
    result = start_run(
        task,
        journal,
        client,
        approver=deny_all,
        runs_dir=tmp_path / "runs",
        prices={"input": 4.0, "output": 20.0},
        model_backend="scripted",
    )
    assert result.status == "failed"
    assert "max_cost_usd" in result.reason
    assert len(client.requests) == 1  # 0.03 spent + up to 0.03 next output > 0.05 cap
    limit = [e for e in journal.events(result.run_id) if e.type == "limit_reached"][0]
    assert limit.payload["usage"]["cost_usd"] == 0.03
