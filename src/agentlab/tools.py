"""The three Phase 1 tools. Each one is: a name, a description the model reads, a JSON
Schema for its input, the capability it needs, and a small function that does the work.

The functions only receive the run's Workspace. They have no other way to reach the
filesystem, and there is no shell, subprocess, or network tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentlab.model_types import ToolSpec
from agentlab.workspace import Workspace


@dataclass(frozen=True)
class ToolOutput:
    text: str  # what the model gets back
    details: dict  # extra facts for the journal (hashes, counts)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema of the arguments object
    capability: str  # e.g. "filesystem.read"
    run: Callable[[Workspace, dict], ToolOutput]

    def spec(self) -> ToolSpec:
        """What the model is told about this tool (backends translate it to their format)."""
        return ToolSpec(self.name, self.description, self.parameters)


def _schema(**props: str) -> dict:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "description": d} for name, d in props.items()},
        "required": list(props),
        "additionalProperties": False,
    }


def _list_files(ws: Workspace, args: dict) -> ToolOutput:
    files = ws.list_files(args["path"])
    return ToolOutput("\n".join(files) if files else "(no files)", {"count": len(files)})


def _read_file(ws: Workspace, args: dict) -> ToolOutput:
    content = ws.read_text(args["path"])
    return ToolOutput(content, {"chars": len(content)})


def _write_file(ws: Workspace, args: dict) -> ToolOutput:
    old, new = ws.write_text(args["path"], args["content"])
    action = "Created" if old is None else "Overwrote"
    return ToolOutput(
        f"{action} {args['path']} ({len(args['content'])} characters).",
        {"old_sha256": old, "new_sha256": new, "chars": len(args["content"])},
    )


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool(
            "list_files",
            "List all files under a directory of the task workspace, recursively. "
            "Call this first to discover which files exist. Use '.' for the whole workspace.",
            _schema(path="Workspace-relative directory, e.g. '.' or 'notes'"),
            "filesystem.read",
            _list_files,
        ),
        Tool(
            "read_file",
            "Read a UTF-8 text file from the task workspace. Call this when you need the "
            "exact contents of a file whose path you know.",
            _schema(path="Workspace-relative file path, e.g. 'notes/meeting.md'"),
            "filesystem.read",
            _read_file,
        ),
        Tool(
            "write_file",
            "Create or overwrite a UTF-8 text file in the task workspace. Call this to save "
            "your result. Parent directories are created automatically. Writes may be denied "
            "by the permission policy; if so, the error explains why.",
            _schema(
                path="Workspace-relative file path, e.g. 'out/summary.md'",
                content="The complete new file content",
            ),
            "filesystem.write",
            _write_file,
        ),
    ]
}


def validate_input(tool: Tool, args: object) -> str | None:
    """Check the model's input against the tool's schema. Returns a problem or None.

    Some backends constrain arguments to the schema; we check anyway, because the kernel
    must not trust model output whatever produced it.
    """
    if not isinstance(args, dict):
        return "input must be a JSON object"
    props = tool.parameters["properties"]
    missing = [k for k in tool.parameters["required"] if k not in args]
    extra = [k for k in args if k not in props]
    if missing:
        return f"missing field(s): {', '.join(missing)}"
    if extra:
        return f"unexpected field(s): {', '.join(extra)}"
    wrong = [k for k, v in args.items() if not isinstance(v, str)]
    if wrong:
        return f"field(s) must be strings: {', '.join(wrong)}"
    return None
