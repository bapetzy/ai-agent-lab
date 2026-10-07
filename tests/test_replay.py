"""Step 6: replay under schema v2 (provider-neutral recordings)."""

import dataclasses

from agentlab.backends.scripted import ScriptedBackend, call, reply
from agentlab.kernel import approve_all, deny_all
from agentlab.model_types import ModelError
from agentlab.replay import reconstruct_transcript, replay_run
from agentlab.run import start_run

SCRIPT = [
    reply(
        tool_calls=[call("read_file", {"path": "notes/todo.md"}, "t1")],
        usage={"input_tokens": 100, "output_tokens": 20},
    ),
    reply(
        tool_calls=[
            call("write_file", {"path": "notes/todo.md", "content": "edited"}, "t2"),
            call("write_file", {"path": "readme.txt", "content": "x"}, "t3"),
            call("write_file", raw="{broken", id="t4"),
        ]
    ),
    reply(tool_calls=[call("write_file", {"path": "out/summary.md", "content": "Ana, Ben"}, "t5")]),
    reply("Summary written."),
]


def record(task, journal, tmp_path, approver=approve_all, script=SCRIPT):
    return start_run(
        task,
        journal,
        ScriptedBackend(script),
        approver=approver,
        runs_dir=tmp_path / "runs",
        sleep=lambda s: None,
    )


def test_replay_of_an_unchanged_kernel_matches_exactly(task, journal, tmp_path):
    original = record(task, journal, tmp_path)
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences
    assert report.model_calls == 4
    assert report.identical_requests == 4
    assert report.events_compared == len(journal.events(original.run_id))
    assert "timestamps" in " ".join(report.not_reproduced)


def test_replay_reuses_recorded_human_decisions(task, journal, tmp_path):
    original = record(task, journal, tmp_path, approver=deny_all)
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences


def test_replay_detects_a_changed_policy(task, journal, tmp_path, monkeypatch):
    original = record(task, journal, tmp_path)
    from agentlab.policy import Decision, Policy

    monkeypatch.setattr(Policy, "check", lambda self, cap, path: Decision("allow", "changed"))
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert any("permission_checked" in d for d in report.differences)


def test_replay_detects_a_changed_tool_description(task, journal, tmp_path, monkeypatch):
    original = record(task, journal, tmp_path)
    from agentlab import tools

    changed = dataclasses.replace(tools.TOOLS["read_file"], description="Different words.")
    monkeypatch.setitem(tools.TOOLS, "read_file", changed)
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert report.identical_requests == 0
    assert any("request 1" in d for d in report.differences)


def test_replay_uses_the_recorded_fixture_not_the_current_files(
    task, journal, tmp_path, fixture_dir
):
    original = record(task, journal, tmp_path)
    (fixture_dir / "notes" / "todo.md").write_text("CHANGED AFTER THE RUN", encoding="utf-8")
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences


def test_replay_does_not_write_to_the_journal(task, journal, tmp_path):
    original = record(task, journal, tmp_path)
    before = len(journal.events(original.run_id))
    replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert len(journal.events(original.run_id)) == before
    assert len(journal.runs()) == 1


def test_malformed_calls_replay_identically(task, journal, tmp_path):
    original = record(task, journal, tmp_path)
    failed = [e for e in journal.events(original.run_id) if e.type == "tool_failed"]
    assert any(e.payload["error_type"] == "invalid_input" for e in failed)
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences


def test_transient_errors_are_not_replayed_but_do_not_break_replay(task, journal, tmp_path):
    original = record(
        task, journal, tmp_path, script=[ModelError("transient", "busy", status=529), *SCRIPT]
    )
    assert "model_retry" in [e.type for e in journal.events(original.run_id)]
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences


def test_a_run_that_ended_in_a_permanent_error_replays_only_up_to_the_error(
    task, journal, tmp_path
):
    original = record(
        task, journal, tmp_path, script=[SCRIPT[0], ModelError("permanent", "bad", status=400)]
    )
    assert original.reason == "model:model_error"
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert any("no response" in d for d in report.differences)


def test_schema_v1_runs_are_refused_with_a_clear_message(journal, tmp_path):
    journal.append(
        "old-run",
        "run_started",
        {"task": {"name": "x"}, "model_backend": "anthropic"},
        agent_id="a",
        task_id="t",
    )
    report = replay_run(journal, "old-run", work_dir=tmp_path / "replay")
    assert not report.matched
    assert "schema_version" in report.differences[0]


def test_transcript_can_be_reconstructed_from_the_journal(task, journal, tmp_path):
    original = record(task, journal, tmp_path)
    transcript = reconstruct_transcript(journal, original.run_id)
    roles = [m["role"] for m in transcript["messages"]]
    assert roles == [
        "user",
        "assistant",
        "tool_results",
        "assistant",
        "tool_results",
        "assistant",
        "tool_results",
        "assistant",
    ]
    assert transcript["system"] == task.agent["system"]
    assert transcript["messages"][-1]["text"] == "Summary written."
