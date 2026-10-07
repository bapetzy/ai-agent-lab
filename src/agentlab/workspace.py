"""A disposable per-run copy of a task's files, and the only place file tools may touch.

``resolve()`` turns a path chosen by the model into a real path, and refuses anything
that could point outside the workspace or that Windows might interpret in a surprising
way (drive letters, UNC paths, alternate data streams, device names, 8.3 aliases...).

This is application-level confinement in Python code. It stops a well-behaved or
mistaken agent from wandering off through *these tools*. It is NOT a sandbox: it does
nothing against code that runs with OS access, and its strength is exactly the
correctness of this file. See docs/SANDBOX.md.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path


class WorkspaceError(Exception):
    """A path or file operation the workspace refuses."""


_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_link(p: Path) -> bool:
    return p.is_symlink() or p.is_junction()


class Workspace:
    MAX_FILE_CHARS = 100_000
    MAX_LIST_ENTRIES = 500

    def __init__(self, root: Path):
        self.root = root.resolve()

    @classmethod
    def create(cls, fixture: Path, dest: Path) -> Workspace:
        """Copy ``fixture`` to a new directory ``dest``. Links in the fixture are refused."""
        fixture = Path(fixture)
        for p in [fixture, *fixture.rglob("*")]:
            if _is_link(p):
                raise WorkspaceError(f"fixture contains a link: {p}")
        if dest.exists():
            raise WorkspaceError(f"workspace already exists: {dest}")
        shutil.copytree(fixture, dest)
        return cls(dest)

    @classmethod
    def from_files(cls, files: dict[str, str], dest: Path) -> Workspace:
        """Build a workspace from {relative_path: text}. Used to rebuild a recorded fixture."""
        dest.mkdir(parents=True, exist_ok=False)
        ws = cls(dest)
        for rel, text in files.items():
            target = ws.resolve(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
        return ws

    # -- path confinement ---------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        if not isinstance(rel, str) or rel.strip() == "":
            raise WorkspaceError("path must be a non-empty string")
        if "\x00" in rel:
            raise WorkspaceError("path contains a NUL byte")
        if ":" in rel:
            raise WorkspaceError("':' is not allowed (drive letters, alternate data streams)")
        if rel.startswith(("/", "\\")):
            raise WorkspaceError("absolute paths are not allowed")
        parts = [p for p in re.split(r"[\\/]+", rel) if p not in ("", ".")]
        for part in parts:
            if part == "..":
                raise WorkspaceError("'..' is not allowed")
            if part.endswith((".", " ")):
                raise WorkspaceError("path parts may not end with '.' or ' ' (Windows strips them)")
            if _RESERVED.match(part):
                raise WorkspaceError(f"{part!r} is a reserved Windows device name")
            if "~" in part:
                raise WorkspaceError("'~' is not allowed (8.3 short-name aliases)")
        candidate = self.root.joinpath(*parts)
        # No link anywhere between the root and the target.
        probe = self.root
        for part in parts:
            probe = probe / part
            if _is_link(probe):
                raise WorkspaceError(f"path goes through a link: {part}")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError("path resolves outside the workspace")
        return resolved

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    # -- operations used by tools ---------------------------------------------------

    def list_files(self, rel: str = ".") -> list[str]:
        base = self.root if rel.strip() in (".", "") else self.resolve(rel)
        if not base.is_dir():
            raise WorkspaceError(f"not a directory: {rel}")
        found = sorted(self.relative(p) for p in base.rglob("*") if p.is_file())
        return found[: self.MAX_LIST_ENTRIES]

    def read_text(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise WorkspaceError(f"file not found: {rel}")
        data = path.read_bytes()
        if len(data) > self.MAX_FILE_CHARS * 4:
            raise WorkspaceError(f"file too large to read: {rel}")
        text = data.decode("utf-8", errors="replace")
        if len(text) > self.MAX_FILE_CHARS:
            raise WorkspaceError(f"file too large to read: {rel}")
        return text

    def write_text(self, rel: str, content: str) -> tuple[str | None, str]:
        """Write a file; return (old sha256 or None, new sha256)."""
        if len(content) > self.MAX_FILE_CHARS:
            raise WorkspaceError(f"content too large: {len(content)} characters")
        path = self.resolve(rel)
        if path.is_dir():
            raise WorkspaceError(f"is a directory: {rel}")
        old = _sha256(path.read_bytes()) if path.is_file() else None
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        path.write_bytes(data)
        return old, _sha256(data)

    def snapshot(self) -> dict[str, str]:
        """{relative path: sha256} for every file."""
        return {
            self.relative(p): _sha256(p.read_bytes())
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }

    def read_all(self) -> dict[str, str]:
        """{relative path: text} for every file (used to record the fixture)."""
        return {
            self.relative(p): p.read_bytes().decode("utf-8", errors="replace")
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }
