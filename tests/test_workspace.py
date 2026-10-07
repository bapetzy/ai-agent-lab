import os

import pytest
from conftest import make_dir_link

from agentlab.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path):
    fixture = tmp_path / "fixture"
    (fixture / "notes").mkdir(parents=True)
    (fixture / "notes" / "a.md").write_text("alpha", encoding="utf-8")
    (fixture / "readme.txt").write_text("hello", encoding="utf-8")
    secret = tmp_path / "outside.txt"
    secret.write_text("SECRET OUTSIDE WORKSPACE", encoding="utf-8")
    return Workspace.create(fixture, tmp_path / "runs" / "ws1")


def test_workspace_is_a_copy_of_the_fixture(ws, tmp_path):
    assert ws.read_text("notes/a.md") == "alpha"
    ws.write_text("notes/a.md", "changed")
    assert (tmp_path / "fixture" / "notes" / "a.md").read_text(encoding="utf-8") == "alpha"


def test_list_files_returns_relative_posix_paths(ws):
    assert ws.list_files(".") == ["notes/a.md", "readme.txt"]
    assert ws.list_files("notes") == ["notes/a.md"]


def test_write_creates_parents_and_reports_hashes(ws):
    old, new = ws.write_text("out/report.md", "x")
    assert old is None and len(new) == 64
    old2, new2 = ws.write_text("out/report.md", "y")
    assert old2 == new and new2 != new


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "../outside.txt",
        "notes/../../outside.txt",
        "..\\outside.txt",
        "/etc/passwd",
        "\\Windows\\win.ini",
        "C:\\Windows\\win.ini",
        "C:outside.txt",
        "\\\\server\\share\\x",
        "readme.txt:hidden",  # alternate data stream
        "NUL",
        "con.txt",
        "notes/COM1",
        "readme.txt.",  # trailing dot
        "readme.txt ",  # trailing space
        "PROGRA~1",  # 8.3 short-name style alias
        "notes\x00a.md",
    ],
)
def test_paths_outside_or_ambiguous_are_rejected(ws, bad):
    with pytest.raises(WorkspaceError):
        ws.resolve(bad)


def test_rejected_paths_never_read_outside(ws):
    with pytest.raises(WorkspaceError):
        ws.read_text("../outside.txt")


def test_a_path_through_a_link_is_refused(ws, monkeypatch):
    """Fast, always-on version: pretend notes/ is a junction (a Windows directory link)."""
    from pathlib import Path

    real = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "notes" or real(self))
    with pytest.raises(WorkspaceError, match="link"):
        ws.read_text("notes/a.md")
    with pytest.raises(WorkspaceError, match="link"):
        ws.write_text("notes/new.md", "x")


def test_fixture_containing_a_link_is_refused_fast(tmp_path, monkeypatch):
    from pathlib import Path

    fixture = tmp_path / "fx"
    (fixture / "escape").mkdir(parents=True)
    monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "escape")
    with pytest.raises(WorkspaceError, match="link"):
        Workspace.create(fixture, tmp_path / "ws")


real_links = pytest.mark.skipif(
    os.environ.get("AGENTLAB_LINK_TESTS") != "1",
    reason="creates real junctions/symlinks; set AGENTLAB_LINK_TESTS=1 (on this machine "
    "link creation from python.exe is blocked and stalls ~30 s)",
)


@real_links
def test_real_links_inside_workspace_are_refused(ws, tmp_path):
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
    make_dir_link(ws.root / "linkdir", outside)
    with pytest.raises(WorkspaceError, match="link"):
        ws.read_text("linkdir/secret.txt")


@real_links
def test_real_link_in_fixture_is_refused(tmp_path):
    fixture = tmp_path / "fx"
    fixture.mkdir()
    make_dir_link(fixture / "escape", tmp_path)
    with pytest.raises(WorkspaceError, match="link"):
        Workspace.create(fixture, tmp_path / "ws")


def test_size_limits(ws):
    with pytest.raises(WorkspaceError, match="too large"):
        ws.write_text("big.txt", "x" * (Workspace.MAX_FILE_CHARS + 1))


def test_reading_a_missing_file_is_an_error(ws):
    with pytest.raises(WorkspaceError, match="not found"):
        ws.read_text("nope.md")


def test_snapshot_hashes_every_file(ws):
    snap = ws.snapshot()
    assert set(snap) == {"notes/a.md", "readme.txt"}
