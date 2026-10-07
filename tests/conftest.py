import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agentlab.journal import Journal
from agentlab.task import Task

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path():
    """Replaces pytest's tmp_path. On this Windows machine pytest's version tries to create
    a 'current' symlink, and each unprivileged symlink attempt stalls ~30 s before failing."""
    d = Path(tempfile.mkdtemp(prefix="agentlab-test-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def make_dir_link(link: Path, target: Path) -> None:
    """A directory link: an NTFS junction on Windows (no admin needed), a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
        )
    else:
        os.symlink(target, link)


@pytest.fixture
def journal():
    return Journal(":memory:")


@pytest.fixture
def fixture_dir(tmp_path):
    """A tiny disposable workspace fixture."""
    fx = tmp_path / "fixture"
    (fx / "notes").mkdir(parents=True)
    (fx / "notes" / "todo.md").write_text("- buy milk (Ana)\n- fix bike (Ben)\n", encoding="utf-8")
    (fx / "readme.txt").write_text("Summaries go in out/.", encoding="utf-8")
    return fx


@pytest.fixture
def task(fixture_dir):
    return Task.from_dict(
        {
            "task": {"name": "test-task", "prompt": "Summarize notes/todo.md into out/summary.md"},
            "agent": {
                "model": "scripted-fake",
                "system": "You are a careful assistant.",
                "tools": ["list_files", "read_file", "write_file"],
            },
            "limits": {"max_turns": 6, "max_tool_calls": 10},
            "policy": [
                {"id": "read-all", "capability": "filesystem.read", "decision": "allow"},
                {
                    "id": "write-out",
                    "capability": "filesystem.write",
                    "paths": ["out/**"],
                    "decision": "allow",
                },
                {
                    "id": "ask-notes",
                    "capability": "filesystem.write",
                    "paths": ["notes/**"],
                    "decision": "ask",
                },
            ],
            "check": [
                {"type": "file_contains", "path": "out/summary.md", "text": "Ana"},
            ],
        },
        workspace_fixture=fixture_dir,
    )
