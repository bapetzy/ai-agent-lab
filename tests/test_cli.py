"""The CLI end to end, offline (scripted model)."""

import json

from conftest import ROOT

from agentlab.cli import main

DEMO = ROOT / "tasks" / "demo-permissions"


def run_cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr().out
    return code, out


def test_scripted_demo_run_trace_show_and_replay(tmp_path, capsys):
    db = str(tmp_path / "lab.db")
    common = ["--db", db]
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
    assert "SCRIPTED" in out  # never mistaken for a real model run
    run_id = next(line.split()[-1] for line in out.splitlines() if line.startswith("run id"))

    code, out = run_cli(capsys, "runs", *common)
    assert run_id[-8:] in out

    code, out = run_cli(capsys, "trace", run_id[-8:], *common)
    assert code == 0
    for expected in ["MODEL", "read_file", "DENIED", "approved", "FAILED", "CHECK", "COMPLETED"]:
        assert expected in out, expected

    code, out = run_cli(capsys, "show", run_id[-8:], "2", *common)
    assert code == 0
    assert '"messages"' in out  # the full request body of the first model call

    code, out = run_cli(capsys, "replay", run_id[-8:], *common)
    assert code == 0
    assert "MATCH" in out


def test_live_run_without_api_key_fails_clearly(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("agentlab.config.DOTENV", tmp_path / "missing.env")
    code, out = run_cli(
        capsys,
        "run",
        str(ROOT / "tasks" / "e001-meeting-actions"),
        "--db",
        str(tmp_path / "lab.db"),
    )
    assert code == 2
    assert "ANTHROPIC_API_KEY" in out


def test_scripted_demo_files_are_valid_json():
    script = json.loads((DEMO / "scripted.json").read_text(encoding="utf-8"))
    assert script[-1]["stop_reason"] == "end_turn"
