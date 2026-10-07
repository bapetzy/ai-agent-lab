"""Replay semantics: what can and cannot be reproduced from the journal."""

from agentlab.kernel import approve_all, deny_all
from agentlab.replay import reconstruct_transcript, replay_run
from agentlab.run import start_run
from agentlab.scripted import ScriptedClient, response, text, tool_use

FAKE_PRICES = {"input": 0.0, "output": 0.0}

SCRIPT = [
    response(
        [tool_use("read_file", {"path": "notes/todo.md"}, "t1")],
        "tool_use",
        usage={"input_tokens": 100, "output_tokens": 20},
    ),
    response(
        [
            tool_use("write_file", {"path": "notes/todo.md", "content": "edited"}, "t2"),
            tool_use("write_file", {"path": "readme.txt", "content": "x"}, "t3"),
        ],
        "tool_use",
    ),
    response(
        [tool_use("write_file", {"path": "out/summary.md", "content": "Ana, Ben"}, "t4")],
        "tool_use",
    ),
    response([text("Summary written.")], "end_turn"),
]


def record(task, journal, tmp_path, approver=approve_all):
    return start_run(
        task,
        journal,
        ScriptedClient(SCRIPT),
        approver=approver,
        runs_dir=tmp_path / "runs",
        prices=FAKE_PRICES,
        model_backend="scripted",
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
    assert report.matched, report.differences  # a different approver would diverge


def test_replay_detects_a_changed_policy(task, journal, tmp_path, monkeypatch):
    original = record(task, journal, tmp_path)
    # Simulate a kernel change: the policy engine now allows everything.
    from agentlab.policy import Decision, Policy

    monkeypatch.setattr(Policy, "check", lambda self, cap, path: Decision("allow", "changed"))
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert any("permission_checked" in d for d in report.differences)


def test_replay_detects_a_changed_tool_description(task, journal, tmp_path, monkeypatch):
    original = record(task, journal, tmp_path)
    import dataclasses

    from agentlab import tools

    changed = dataclasses.replace(tools.TOOLS["read_file"], description="Different words.")
    monkeypatch.setitem(tools.TOOLS, "read_file", changed)
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert report.identical_requests == 0  # the very first request already differs
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


def test_transcript_can_be_reconstructed_from_the_journal(task, journal, tmp_path):
    original = record(task, journal, tmp_path)
    transcript = reconstruct_transcript(journal, original.run_id)
    roles = [m["role"] for m in transcript["messages"]]
    assert roles == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert transcript["system"] == task.agent["system"]
    assert transcript["messages"][-1]["content"][0]["text"] == "Summary written."


def test_transient_api_errors_are_not_replayed_but_do_not_break_replay(task, journal, tmp_path):
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError(
        "slow down", response=httpx2.Response(429, request=req), body=None
    )
    original = start_run(
        task,
        journal,
        ScriptedClient([rate, *SCRIPT]),
        approver=approve_all,
        runs_dir=tmp_path / "runs",
        prices=FAKE_PRICES,
        model_backend="scripted",
        sleep=lambda s: None,
    )
    assert "model_retry" in [e.type for e in journal.events(original.run_id)]
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert report.matched, report.differences


def test_a_run_that_ended_in_a_permanent_api_error_replays_only_up_to_the_error(
    task, journal, tmp_path
):
    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    bad = anthropic.BadRequestError("bad", response=httpx2.Response(400, request=req), body=None)
    original = start_run(
        task,
        journal,
        ScriptedClient([SCRIPT[0], bad]),
        approver=approve_all,
        runs_dir=tmp_path / "runs",
        prices=FAKE_PRICES,
        model_backend="scripted",
    )
    assert original.reason == "model:api_error"
    report = replay_run(journal, original.run_id, work_dir=tmp_path / "replay")
    assert not report.matched
    assert any("no response" in d for d in report.differences)
