"""The free-first guarantees, tested directly: the core runs with no Anthropic SDK, no API key,
and no network."""

import socket
import subprocess
import sys

import pytest
from conftest import ROOT

from agentlab.backends.scripted import ScriptedBackend, call, reply
from agentlab.kernel import deny_all
from agentlab.run import start_run

CORE_MODULES = [
    "agentlab.model_types",
    "agentlab.backends.scripted",
    "agentlab.gateway",
    "agentlab.kernel",
    "agentlab.agent",
    "agentlab.run",
    "agentlab.task",
    "agentlab.tools",
    "agentlab.policy",
    "agentlab.workspace",
    "agentlab.journal",
    "agentlab.budget",
    "agentlab.replay",
    "agentlab.trace",
    "agentlab.cli",
    "agentlab.backends",
]

BLOCK_ANTHROPIC = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "anthropic" or name.startswith("anthropic."):
            raise ImportError("anthropic is blocked in this test")
sys.meta_path.insert(0, Block())
"""


def test_core_imports_and_a_full_run_work_with_the_anthropic_sdk_blocked(tmp_path):
    code = (
        BLOCK_ANTHROPIC
        + f"""
import importlib
for m in {CORE_MODULES!r}:
    importlib.import_module(m)
from agentlab.cli import main
rc = main(["run", r"{ROOT / "tasks" / "demo-permissions"}",
           "--scripted", r"{ROOT / "tasks" / "demo-permissions" / "scripted.json"}",
           "--approve", "yes", "--db", r"{tmp_path / "lab.db"}",
           "--runs-dir", r"{tmp_path / "runs"}"])
assert "anthropic" not in sys.modules
print("RC", rc)
"""
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PATH": "", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
        cwd=ROOT,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "RC 0" in out.stdout


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def test_a_full_scripted_run_makes_no_network_access(
    task, journal, tmp_path, no_network, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    script = [
        reply(tool_calls=[call("read_file", {"path": "notes/todo.md"}, "t1")]),
        reply(tool_calls=[call("write_file", {"path": "out/summary.md", "content": "Ana"}, "t2")]),
        reply("done"),
    ]
    result = start_run(
        task, journal, ScriptedBackend(script), approver=deny_all, runs_dir=tmp_path / "runs"
    )
    assert result.status == "completed" and result.success is True


def test_the_anthropic_sdk_is_not_a_core_dependency():
    import tomllib

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert not any(d.startswith("anthropic") for d in project["dependencies"])
    assert any(d.startswith("anthropic") for d in project["optional-dependencies"]["anthropic"])
