"""The kernel: every tool call the model asks for passes through ``Kernel.handle``.

    tool_requested
      -> unknown tool?        tool_failed (unknown_tool)
      -> bad input?           tool_failed (invalid_input)
      -> path outside?        tool_failed (invalid_path)
    permission_checked        policy says allow / deny / ask
      -> ask:                 approval_requested, approval_resolved (a human decides)
    permission_approved | permission_denied
    tool_started
    tool_completed | tool_failed

Whatever happens, the model gets a ``tool_result`` back. Errors and denials are results
with ``is_error: true`` and an explanation, so the model can adapt.

This is application-level mediation. It controls what agents can do through these tools;
it is not an OS-level security boundary (see docs/SANDBOX.md).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from agentlab.journal import Recorder
from agentlab.policy import Policy
from agentlab.tools import Tool, validate_input
from agentlab.workspace import Workspace, WorkspaceError

Approver = Callable[[dict], dict]  # request -> {"approved": bool, "by": str, "note": str}


def deny_all(request: dict) -> dict:
    return {"approved": False, "by": "auto-deny", "note": "no human approver available"}


def approve_all(request: dict) -> dict:
    return {"approved": True, "by": "auto-approve", "note": ""}


def _error_result(tool_use_id: str, error: dict) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(error),
        "is_error": True,
    }


class Kernel:
    def __init__(
        self,
        rec: Recorder,
        workspace: Workspace,
        policy: Policy,
        approver: Approver,
        tools: dict[str, Tool],
    ):
        self.rec = rec
        self.workspace = workspace
        self.policy = policy
        self.approver = approver
        self.tools = tools

    def _fail(self, call_id: str, tool: str, error_type: str, message: str, **extra) -> dict:
        self.rec.emit(
            "tool_failed",
            {
                "tool_use_id": call_id,
                "tool": tool,
                "error_type": error_type,
                "message": message,
                **extra,
            },
        )
        return _error_result(call_id, {"error": error_type, "message": message})

    def handle(self, call: dict) -> dict:
        call_id, name, args = call["id"], call["name"], call["input"]
        self.rec.emit("tool_requested", {"tool_use_id": call_id, "tool": name, "input": args})

        # 1. Is it a real tool, with valid input, pointing inside the workspace?
        tool = self.tools.get(name)
        if tool is None:
            return self._fail(call_id, name, "unknown_tool", f"there is no tool named {name!r}")
        if (problem := validate_input(tool, args)) is not None:
            return self._fail(call_id, name, "invalid_input", problem)
        try:
            # canonical workspace-relative path; "." is the workspace root
            path = self.workspace.relative(self.workspace.resolve(args["path"]))
        except WorkspaceError as e:
            return self._fail(call_id, name, "invalid_path", str(e))

        # 2. May this agent do it? Policy first, then a human if the policy says "ask".
        verdict = self.policy.check(tool.capability, path)
        self.rec.emit(
            "permission_checked",
            {
                "tool_use_id": call_id,
                "tool": name,
                "capability": tool.capability,
                "path": path,
                "decision": verdict.decision,
                "rule": verdict.rule_id,
            },
        )
        decided_by, note = "policy", ""
        allowed = verdict.decision == "allow"
        if verdict.decision == "ask":
            request = {
                "tool": name,
                "input": args,
                "capability": tool.capability,
                "path": path,
                "rule": verdict.rule_id,
            }
            self.rec.emit("approval_requested", {"tool_use_id": call_id, **request})
            answer = self.approver(request)
            self.rec.emit("approval_resolved", {"tool_use_id": call_id, **answer})
            decided_by, note, allowed = "human", answer.get("note", ""), bool(answer["approved"])

        facts = {
            "tool_use_id": call_id,
            "capability": tool.capability,
            "path": path,
            "rule": verdict.rule_id,
            "by": decided_by,
        }
        if not allowed:
            self.rec.emit("permission_denied", {**facts, "note": note})
            return _error_result(
                call_id,
                {
                    "error": "permission_denied",
                    "capability": tool.capability,
                    "path": path,
                    "rule": verdict.rule_id,
                    "decided_by": decided_by,
                    "message": note or "Your permissions do not allow this. Work within them.",
                },
            )
        self.rec.emit("permission_approved", facts)

        # 3. Run it.
        self.rec.emit("tool_started", {"tool_use_id": call_id, "tool": name})
        started = time.perf_counter()
        try:
            out = tool.run(self.workspace, args)
        except WorkspaceError as e:
            return self._fail(
                call_id,
                name,
                "WorkspaceError",
                str(e),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except Exception as e:  # a bug in a tool: record it, tell the model, keep going
            return self._fail(
                call_id,
                name,
                type(e).__name__,
                str(e),
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        self.rec.emit(
            "tool_completed",
            {
                "tool_use_id": call_id,
                "tool": name,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "output_chars": len(out.text),
                "output_preview": out.text[:200],
                "output_blob": self.rec.blob({"text": out.text}),
                "details": out.details,
            },
        )
        return {"type": "tool_result", "tool_use_id": call_id, "content": out.text}
