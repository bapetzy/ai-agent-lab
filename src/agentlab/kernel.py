"""The kernel: every tool call the model proposes passes through ``Kernel.handle``.

    tool_requested            (arguments, raw arguments, parse error if any)
      -> malformed arguments? tool_failed (invalid_input)   nothing runs
      -> unknown tool?        tool_failed (unknown_tool)
      -> bad input?           tool_failed (invalid_input)
      -> path outside?        tool_failed (invalid_path)
    permission_checked        policy says allow / deny / ask
      -> ask:                 approval_requested, approval_resolved (a human decides)
    permission_approved | permission_denied
    tool_started
    tool_completed | tool_failed

The kernel only understands ``ToolCall`` in and ``ToolResult`` out (model_types.py). It does
not know which model or provider produced the call. Whatever happens, the model gets a result
back; errors and denials are results with ``is_error=True`` and a JSON explanation.

This is application-level mediation. It controls what agents can do through these tools;
it is not an OS-level security boundary (see docs/SANDBOX.md).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

from agentlab.journal import Recorder
from agentlab.model_types import ToolCall, ToolResult
from agentlab.policy import Policy
from agentlab.tools import Tool, validate_input
from agentlab.workspace import Workspace, WorkspaceError

Approver = Callable[[dict], dict]  # request -> {"approved": bool, "by": str, "note": str}


def deny_all(request: dict) -> dict:
    return {"approved": False, "by": "auto-deny", "note": "no human approver available"}


def approve_all(request: dict) -> dict:
    return {"approved": True, "by": "auto-approve", "note": ""}


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

    def _error(self, call: ToolCall, error: dict) -> ToolResult:
        return ToolResult(call.id, call.name, json.dumps(error), is_error=True)

    def _fail(self, call: ToolCall, error_type: str, message: str, **extra) -> ToolResult:
        self.rec.emit(
            "tool_failed",
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "error_type": error_type,
                "message": message,
                **extra,
            },
        )
        return self._error(call, {"error": error_type, "message": message})

    def handle(self, call: ToolCall) -> ToolResult:
        self.rec.emit(
            "tool_requested",
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "arguments": call.arguments,
                "raw_arguments": call.raw_arguments,
                "parse_error": call.parse_error,
            },
        )

        # 1. Is it a well-formed call to a real tool, pointing inside the workspace?
        if call.arguments is None:
            return self._fail(
                call,
                "invalid_input",
                f"{call.parse_error}; the call was not executed. "
                f"Received: {call.raw_arguments[:200]}",
            )
        tool = self.tools.get(call.name)
        if tool is None:
            return self._fail(call, "unknown_tool", f"there is no tool named {call.name!r}")
        args = call.arguments
        if (problem := validate_input(tool, args)) is not None:
            return self._fail(call, "invalid_input", problem)
        try:
            # canonical workspace-relative path; "." is the workspace root
            path = self.workspace.relative(self.workspace.resolve(args["path"]))
        except WorkspaceError as e:
            return self._fail(call, "invalid_path", str(e))

        # 2. May this agent do it? Policy first, then a human if the policy says "ask".
        verdict = self.policy.check(tool.capability, path)
        self.rec.emit(
            "permission_checked",
            {
                "tool_call_id": call.id,
                "tool": call.name,
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
                "tool": call.name,
                "input": args,
                "capability": tool.capability,
                "path": path,
                "rule": verdict.rule_id,
            }
            self.rec.emit("approval_requested", {"tool_call_id": call.id, **request})
            answer = self.approver(request)
            self.rec.emit("approval_resolved", {"tool_call_id": call.id, **answer})
            decided_by, note, allowed = "human", answer.get("note", ""), bool(answer["approved"])

        facts = {
            "tool_call_id": call.id,
            "capability": tool.capability,
            "path": path,
            "rule": verdict.rule_id,
            "by": decided_by,
        }
        if not allowed:
            self.rec.emit("permission_denied", {**facts, "note": note})
            return self._error(
                call,
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
        self.rec.emit("tool_started", {"tool_call_id": call.id, "tool": call.name})
        started = time.perf_counter()

        def ms() -> float:
            return round((time.perf_counter() - started) * 1000, 3)

        try:
            out = tool.run(self.workspace, args)
        except WorkspaceError as e:
            return self._fail(call, "WorkspaceError", str(e), duration_ms=ms())
        except Exception as e:  # a bug in a tool: record it, tell the model, keep going
            return self._fail(call, type(e).__name__, str(e), duration_ms=ms())
        self.rec.emit(
            "tool_completed",
            {
                "tool_call_id": call.id,
                "tool": call.name,
                "duration_ms": ms(),
                "output_chars": len(out.text),
                "output_preview": out.text[:200],
                "output_blob": self.rec.blob({"text": out.text}),
                "details": out.details,
            },
        )
        return ToolResult(call.id, call.name, out.text, is_error=False)
