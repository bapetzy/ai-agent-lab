import pytest
from conftest import ROOT

from agentlab.task import Task, evaluate_checks
from agentlab.workspace import Workspace


def test_checks_decide_success_independently_of_the_agent(tmp_path, fixture_dir):
    ws = Workspace.create(fixture_dir, tmp_path / "ws")
    ws.write_text("out/a.md", "Ana will buy milk")
    results = evaluate_checks(
        [
            {"type": "file_exists", "path": "out/a.md"},
            {"type": "file_contains", "path": "out/a.md", "text": "ana"},
            {"type": "file_not_contains", "path": "out/a.md", "text": "bike"},
            {"type": "final_text_contains", "text": "2"},
            {"type": "file_contains", "path": "missing.md", "text": "x"},
        ],
        ws,
        final_text="There are 2 open items.",
    )
    assert [r["passed"] for r in results] == [True, True, True, True, False]
    assert "not found" in results[-1]["detail"]


def test_unknown_check_type_is_rejected(fixture_dir):
    with pytest.raises(ValueError, match="check type"):
        Task.from_dict(
            {
                "task": {"name": "x", "prompt": "p"},
                "agent": {"model": "m", "system": "s", "tools": []},
                "check": [{"type": "vibes"}],
            },
            workspace_fixture=fixture_dir,
        )


@pytest.mark.parametrize("name", ["demo-permissions", "e001-meeting-actions"])
def test_shipped_tasks_load(name):
    task = Task.load(ROOT / "tasks" / name)
    assert task.workspace_fixture.is_dir()
    assert task.checks


def test_e001_checks_are_satisfiable_by_a_correct_answer(tmp_path, journal):
    """Guards the baseline task itself: a perfect agent must score success=True."""
    from agentlab.kernel import deny_all
    from agentlab.run import start_run
    from agentlab.scripted import ScriptedClient, response, text, tool_use

    task = Task.load(ROOT / "tasks" / "e001-meeting-actions")
    m = task.agent["model"]
    answer = (
        "- [Ana] book the venue for the workshop\n"
        "- [Citra] collect the budget numbers from finance\n"
        "- [Ben] fix the nightly backup job\n"
        "- [Dewi] prepare the workshop slides\n"
    )
    script = [
        response(
            [tool_use("write_file", {"path": "out/action-items.md", "content": answer}, "t1")],
            "tool_use",
            model=m,
        ),
        response([text("I found 4 open action items.")], "end_turn", model=m),
    ]
    result = start_run(
        task,
        journal,
        ScriptedClient(script),
        approver=deny_all,
        runs_dir=tmp_path / "runs",
        model_backend="scripted",
    )
    assert result.success is True
