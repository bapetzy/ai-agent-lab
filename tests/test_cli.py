"""The CLI end to end, offline (scripted backend)."""

from conftest import ROOT

from agentlab.cli import main

DEMO = ROOT / "tasks" / "demo-permissions"


def run_cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr().out
    return code, out


def test_scripted_demo_run_trace_show_and_replay(tmp_path, capsys):
    common = ["--db", str(tmp_path / "lab.db")]
    code, out = run_cli(
        capsys,
        "run",
        str(DEMO),
        "--scripted",
        str(DEMO / "scripted.json"),
        "--approve",
        "yes",
        "--runs-dir",
        str(tmp_path / "runs"),
        *common,
    )
    assert code == 0, out
    assert "SCRIPTED" in out
    run_id = next(line.split()[-1] for line in out.splitlines() if line.startswith("run id"))

    code, out = run_cli(capsys, "runs", *common)
    assert run_id[-8:] in out

    code, out = run_cli(capsys, "trace", run_id[-8:], *common)
    assert code == 0
    for expected in [
        "MODEL",
        "read_file",
        "DENIED",
        "approved",
        "FAILED",
        "invalid_input",
        "CHECK",
        "COMPLETED",
    ]:
        assert expected in out, expected

    code, out = run_cli(capsys, "show", run_id[-8:], "2", *common)
    assert code == 0
    assert '"messages"' in out

    code, out = run_cli(capsys, "replay", run_id[-8:], *common)
    assert code == 0
    assert "MATCH" in out


def test_scripted_task_without_a_script_explains_itself(tmp_path, capsys):
    code, out = run_cli(capsys, "run", str(DEMO), "--db", str(tmp_path / "lab.db"))
    assert code == 2
    assert "--scripted" in out


def test_unsupported_tool_strategy_is_refused(tmp_path, capsys):
    import shutil

    task_dir = tmp_path / "task"
    shutil.copytree(ROOT / "tasks" / "e001-meeting-actions", task_dir)
    toml = task_dir / "task.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            'tool_strategy = "native"', 'tool_strategy = "constrained_json"'
        ),
        encoding="utf-8",
    )
    code, out = run_cli(capsys, "run", str(task_dir), "--db", str(tmp_path / "lab.db"))
    assert code == 2
    assert "constrained_json" in out and "not implemented" in out


def test_anthropic_backend_is_off_unless_explicitly_enabled(tmp_path, capsys, monkeypatch):
    from agentlab.backends import BackendNotAvailable, make_backend
    from agentlab.task import Task

    monkeypatch.delenv("AGENTLAB_ENABLE_EXTERNAL_API", raising=False)
    task = Task.load(DEMO)
    task.model = {**task.model, "backend": "anthropic", "name": "claude-opus-5-5"}
    try:
        make_backend(task)
        raise AssertionError("anthropic backend should be refused")
    except BackendNotAvailable as e:
        assert "AGENTLAB_ENABLE_EXTERNAL_API" in str(e)
