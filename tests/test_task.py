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


def minimal(**override):
    data = {
        "task": {"name": "x", "prompt": "p"},
        "model": {"backend": "scripted", "name": "m"},
        "agent": {"system": "s", "tools": []},
    }
    data.update(override)
    return data


def test_unknown_check_type_is_rejected(fixture_dir):
    with pytest.raises(ValueError, match="check type"):
        Task.from_dict(minimal(check=[{"type": "vibes"}]), workspace_fixture=fixture_dir)


def test_unknown_backend_is_rejected(fixture_dir):
    with pytest.raises(ValueError, match="backend"):
        Task.from_dict(
            minimal(model={"backend": "magic", "name": "m"}), workspace_fixture=fixture_dir
        )


def test_model_section_defaults(fixture_dir):
    t = Task.from_dict(minimal(), workspace_fixture=fixture_dir)
    assert t.model == {
        "backend": "scripted",
        "name": "m",
        "options": {},
        "temperature": None,
        "seed": None,
    }


@pytest.mark.parametrize("name", ["demo-permissions", "e001-meeting-actions"])
def test_shipped_tasks_load(name):
    task = Task.load(ROOT / "tasks" / name)
    assert task.workspace_fixture.is_dir()
    assert task.checks


def test_e001_targets_the_local_backend_not_a_paid_api():
    task = Task.load(ROOT / "tasks" / "e001-meeting-actions")
    assert task.model["backend"] == "openai_compat"


def test_e001_checks_are_satisfiable_by_a_correct_answer(tmp_path, journal):
    """Guards the baseline task itself: a perfect agent must score success=True."""
    from agentlab.backends.scripted import ScriptedBackend, call, reply
    from agentlab.kernel import deny_all
    from agentlab.run import start_run

    task = Task.load(ROOT / "tasks" / "e001-meeting-actions")
    m = task.model["name"]
    answer = (
        "- [Ana] book the venue for the workshop\n"
        "- [Citra] collect the budget numbers from finance\n"
        "- [Ben] fix the nightly backup job\n"
        "- [Dewi] prepare the workshop slides\n"
    )
    script = [
        reply(
            tool_calls=[
                call("write_file", {"path": "out/action-items.md", "content": answer}, "t1")
            ],
            model=m,
        ),
        reply("I found 4 open action items.", model=m),
    ]
    result = start_run(
        task, journal, ScriptedBackend(script), approver=deny_all, runs_dir=tmp_path / "runs"
    )
    assert result.success is True
