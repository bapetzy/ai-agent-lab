"""Turn a run's events into text a human can follow, plus the metrics for experiments.

Plain ASCII on purpose: it must read well in Windows PowerShell. Reads both journal schemas:
v2 (provider-neutral) and v1 (Phase 1, Anthropic-shaped field names), so old runs stay
inspectable even though they can no longer be replayed.
"""

from __future__ import annotations

import json
from datetime import datetime

from agentlab.journal import Event, Journal


def _short(obj, limit: int = 90) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    s = s.replace("\r", "").replace("\n", " | ")
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _call(c: dict) -> str:
    if c.get("parse_error"):
        return f"{c['name']}(MALFORMED: {_short(c['raw_arguments'], 50)})"
    return f"{c['name']}({_short(c['arguments'], 70)})"


def _line(e: Event) -> str:
    p = e.payload
    match e.type:
        case "run_started":
            backend = (p.get("backend") or {}).get("name", p.get("model_backend", "?"))
            return (
                f"RUN STARTED   task={p['task']['name']}  model={p['model_requested']}  "
                f"backend={str(backend).upper()}  fallback={p['fallback']}"
            )
        case "model_request":
            o = p.get("options") or p.get("output_config")  # v2 / v1
            opts = f", options={_short(o, 40)}" if o else ""
            return (
                f"-> MODEL      turn {p['turn']}: {p['n_messages']} message(s), "
                f"tools={','.join(p['tools'])}, "
                f"max_output_tokens={p.get('max_output_tokens', p.get('max_tokens'))}"
                f"{opts}"
            )
        case "model_response":
            u = p.get("usage") or {}
            cached = f" cached={u['cached_input_tokens']}" if u.get("cached_input_tokens") else ""
            out = (
                f"<- MODEL      finish={p.get('finish_reason', p.get('stop_reason'))}  "
                f"tokens in={u.get('input_tokens')}"
                f"{cached} out={u.get('output_tokens')}  model={p['actual_model']}"
            )
            if p.get("text"):
                out += f"\n                says: {_short(p['text'], 100)}"
            for c in p.get("tool_calls", []):
                out += f"\n                asks: {_call(c)}"
            return out
        case "model_retry":
            return f"   model retry #{p['attempt']}: {p['error_type']} (wait {p['delay_s']}s)"
        case "model_error":
            return f"!! MODEL ERROR {p['error_type']}: {_short(p['message'], 100)}"
        case "tool_requested":
            if p.get("parse_error"):
                return (
                    f"   TOOL        {p['tool']}(MALFORMED arguments: "
                    f"{_short(p['raw_arguments'], 50)})"
                )
            return f"   TOOL        {p['tool']}({_short(p.get('arguments', p.get('input')), 70)})"
        case "permission_checked":
            return (
                f"     policy    {p['capability']} on '{p['path']}' -> {p['decision']}"
                f" (rule: {p['rule']})"
            )
        case "approval_requested":
            return "     human?    approval requested"
        case "approval_resolved":
            verdict = "approved" if p["approved"] else "rejected"
            return f"     human     {verdict} (by {p['by']}) {p.get('note') or ''}".rstrip()
        case "permission_approved":
            return f"     allowed   approved by {p['by']}"
        case "permission_denied":
            return f"     DENIED    by {p['by']} (rule: {p['rule']})"
        case "tool_started":
            return "     running"
        case "tool_completed":
            return f"     ok        {p['duration_ms']} ms: {_short(p['output_preview'], 70)}"
        case "tool_failed":
            return f"     FAILED    {p['error_type']}: {_short(p['message'], 80)}"
        case "limit_reached":
            return f"!! LIMIT      {p['limit']}"
        case "check_result":
            what = p.get("path") or ""
            if p.get("text"):
                what += f" contains '{p['text']}'"
            return (
                f"CHECK         {p['type']} {what.strip()} -> {'PASS' if p['passed'] else 'FAIL'}"
            )
        case "run_completed":
            return f"RUN COMPLETED success={p['success']}"
        case "run_failed":
            return f"RUN FAILED    {p['reason']}"
    return f"{e.type} {_short(p)}"


def summarize(journal: Journal, run_id: str) -> dict:
    """The metrics recorded for every run (used by E001 and the local-model experiments)."""
    events = journal.events(run_id)
    started = events[0].payload
    end = events[-1]
    responses = [e.payload for e in events if e.type == "model_response"]
    t0 = datetime.fromisoformat(events[0].ts)
    t1 = datetime.fromisoformat(end.ts)
    usage = end.payload.get("usage", {})
    backend = (started.get("backend") or {}).get("name", started.get("model_backend"))
    return {
        "run_id": run_id,
        "schema_version": started.get("schema_version", 1),
        "task": started["task"]["name"],
        "backend": backend,
        "model_requested": started["model_requested"],
        "models_used": sorted({r["actual_model"] for r in responses if r["actual_model"]}),
        "tools": [t["name"] for t in started["tools"]],
        "policy": [f"{r['id']}:{r['capability']}:{r['decision']}" for r in started["policy"]],
        "status": "completed" if end.type == "run_completed" else "failed",
        "reason": end.payload.get("reason"),
        "success": end.payload.get("success"),
        "turns": len(responses),
        "tool_calls": sum(1 for e in events if e.type == "tool_requested"),
        "tool_ok": sum(1 for e in events if e.type == "tool_completed"),
        "tool_failed": sum(1 for e in events if e.type == "tool_failed"),
        "tool_malformed": sum(
            1 for e in events if e.type == "tool_requested" and e.payload.get("parse_error")
        ),
        "tool_denied": sum(1 for e in events if e.type == "permission_denied"),
        "approvals": sum(1 for e in events if e.type == "approval_requested"),
        "input_tokens": usage.get("input_tokens"),
        "cached_input_tokens": usage.get("cached_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "est_cost_usd": usage.get("cost_usd"),  # None for free backends
        "duration_s": round((t1 - t0).total_seconds(), 3),
        "final_output": end.payload.get("final_text", ""),
    }


def format_trace(journal: Journal, run_id: str) -> str:
    events = journal.events(run_id)
    if not events:
        return f"no events for run {run_id}"
    t0 = datetime.fromisoformat(events[0].ts)
    s = summarize(journal, run_id)
    lines = [
        f"run   {run_id}",
        f"agent {events[0].agent_id}   task {events[0].task_id}",
        "",
        "  seq   time   event",
    ]
    for e in events:
        dt = (datetime.fromisoformat(e.ts) - t0).total_seconds()
        first, *rest = _line(e).split("\n")
        lines.append(f"  {e.seq:>3}  {dt:6.2f}s {first}")
        lines.extend(f"               {r}" for r in rest)
    cost = "free" if s["est_cost_usd"] is None else f"est. ${s['est_cost_usd']}"
    lines += [
        "",
        f"summary: {s['status']}"
        + (f" ({s['reason']})" if s["reason"] else "")
        + f", success={s['success']}, {s['turns']} model turn(s), {s['tool_calls']} tool call(s)"
        f" [{s['tool_ok']} ok, {s['tool_failed']} failed ({s['tool_malformed']} malformed), "
        f"{s['tool_denied']} denied], {s['approvals']} approval(s)",
        f"tokens: in={s['input_tokens']} out={s['output_tokens']}  {cost}  "
        f"duration {s['duration_s']}s  backend={s['backend']}  "
        f"model requested={s['model_requested']} used={','.join(s['models_used']) or '-'}",
    ]
    if s["final_output"]:
        lines.append(f"final output: {s['final_output']}")
    lines.append("details of one event: agent-lab show <run> <seq>")
    return "\n".join(lines)
