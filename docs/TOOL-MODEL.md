# Tool model and permissions

> **Implementation status (Phase 1).** IMPLEMENTED: `list_files`, `read_file`, `write_file` with hand-written strict JSON Schemas (`src/agentlab/tools.py`); capabilities `filesystem.read` / `filesystem.write`; policy rules = capability + optional path globs + allow/deny/ask, first match wins, default deny (`policy.py`); human approval for `ask` via CLI prompt or `--approve yes|no`; denials and failures returned to the model as `is_error` results with a JSON explanation. NOT IMPLEMENTED: `request_capability`, capability attenuation, effect/reversibility/idempotency fields, taint tracking, the `<resource>:<action>:<scope>` capability syntax (D-022). The permission layer is an application-level policy mechanism, not a security boundary.

This document answers: how a tool call is represented, what a Tool object contains, how permissions are represented, what happens when an agent lacks a permission, and how human approval works.

## 1. What a tool call looks like on the wire (Verified)

The model never runs anything. It returns a structured *request*; the caller decides.

Request (abridged):

```json
{
  "model": "claude-opus-5-5",
  "system": "You are a careful code reviewer...",
  "tools": [{
    "name": "read_file",
    "description": "Read a UTF-8 text file from the workspace. Call this when you need the exact contents of a file you have located.",
    "strict": true,
    "input_schema": {
      "type": "object",
      "properties": {"path": {"type": "string", "description": "Workspace-relative path"}},
      "required": ["path"],
      "additionalProperties": false
    }
  }],
  "messages": [{"role": "user", "content": "Review src/parser.py for bugs."}]
}
```

Response (abridged): the assistant message may contain thinking blocks (empty text under the default display), text, and one or more `tool_use` blocks; `stop_reason` is `"tool_use"`.

```json
{"role": "assistant", "content": [
  {"type": "thinking", "thinking": "", "signature": "..."},
  {"type": "tool_use", "id": "toolu_01A", "name": "read_file", "input": {"path": "src/parser.py"}}
]}
```

Next request appends the assistant message unchanged and a user message with the results:

```json
{"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "toolu_01A", "content": "def parse(...):\n ..."}
]}
```

A failed or denied call is still answered, with `"is_error": true` and an explanation. Several `tool_use` blocks in one turn are answered together in one user message.

Two consequences:

- **The `tool_use` block is the natural syscall boundary.** It is typed, has an ID that links request to result, and arrives before anything happens. That is where the kernel intercepts.
- **Tool descriptions are prompts.** The model decides when to call a tool from its name and description. Descriptions say *when* to call the tool, not only what it does (recommended in Anthropic's tool-use guidance).

## 2. ToolSpec: what a tool object contains

```python
class ToolSpec(BaseModel):
    name: str                         # e.g. "read_file"
    version: str                      # bump on any behaviour or schema change
    description: str                  # model-facing; includes when to call it
    input_model: type[BaseModel]      # → input_schema (strict, additionalProperties: false)
    output_model: type[BaseModel] | None
    effect: Effect                    # none | read | write | exec | network | delegate | human
    reversible: bool                  # can the effect be undone?
    idempotent: bool                  # safe for the kernel to retry?
    parallel_safe: bool               # may run concurrently with other calls in the same turn?
    required_capabilities: Callable[[ResolvedInput], list[Capability]]
    timeout_s: float
    max_output_bytes: int             # larger outputs are truncated with an explicit marker
    output_trust: Trust               # trusted | untrusted (e.g. file contents, web pages)
    executor: Executor                # kernel | in_process | sandbox | mcp
```

Notes on the less obvious fields:

- **`required_capabilities` is a function of the resolved input**, not a static list. `write_file("out/report.md")` needs `fs:write:workspace:/out/report.md`; the same tool with another path needs a different capability. Resolution happens first (canonical path inside the workspace), then the capability check, so a check can never be fooled by `out/../../secrets`.
- **`effect`, `reversible`, `idempotent`** drive policy defaults (irreversible effects default to `ask`), retry rules (only idempotent tools are auto-retried), and scheduling (only `parallel_safe` calls run concurrently).
- **`output_trust`** marks whether the result may contain attacker-controlled text. It feeds the lethal-trifecta rule (section 6) and is recorded on the event.
- **Handlers get a `ToolContext`**, not the world: a workspace handle confined to the run directory, a sandbox handle, the journal for sub-events. They do not import `os`, `subprocess`, or an HTTP client directly. This is a code-review rule enforced by a lint check, because Python cannot enforce it at runtime.

### Bash vs dedicated tools

Anthropic's agent-design guidance: a bash tool gives the model broad leverage but gives the harness only an opaque command string; a dedicated tool gives the harness typed arguments it can gate, audit, render, and parallelise. This lab starts with **dedicated tools only**. "bash vs dedicated tools" is itself an experiment (E014), run later inside the sandbox.

### Phase 1 tool set

| Tool | Effect | Capability (example) |
|---|---|---|
| `list_dir(path)` | read | `fs:read:workspace:<path>` |
| `read_file(path, range?)` | read | `fs:read:workspace:<path>` |
| `search(pattern, path?)` | read | `fs:read:workspace:<path>` |
| `write_file(path, content, expected_hash?)` | write (reversible: kernel keeps the prior version as a blob) | `fs:write:workspace:<path>` |
| `complete_task(status, result, summary)` | kernel | always granted |
| `request_capability(capability, justification, scope)` | human | always granted |

Phase 2 adds `delegate`, `run_python` (sandbox), `ask_human`.

## 3. Capabilities: how permissions are represented

A capability is a typed, scoped right:

```
<resource>:<action>:<scope>[?constraints]

fs:read:workspace:/**                 read anything in the workspace
fs:write:workspace:/out/**            write only under out/
exec:sandbox:python?net=none&mem=512m run Python in a container, no network
net:http:GET:docs.python.org          (later) fetch from one domain
agent:delegate:reviewer               may delegate to the "reviewer" spec
memory:write:self                     (later) write own long-term memory
human:ask                             may ask the human a question
```

Rules:

1. **Default deny.** No capability, no effect.
2. **Grants are an upper bound** set in the agent spec. Effective capabilities of an instance = spec grants ∩ what its parent passed down ∩ policy.
3. **Attenuation on delegation.** A child can receive only a subset of its parent's capabilities. Rights can narrow down the tree, never widen. This is the object-capability principle that prevents a confused deputy: a low-privilege agent cannot get a high-privilege effect by asking another agent.
4. **Scopes match on resolved resources** (canonical paths, normalised domains), never on raw model input.

### Policy engine

The policy engine maps (instance, tool, resolved input, required capabilities, run context) to one of:

| Decision | Meaning |
|---|---|
| `allow` | Execute |
| `deny` | Do not execute; tell the model why |
| `ask` | Pause the instance; ask the human |

Policies are ordered TOML rule lists; the first match wins; the fallback is `deny`. Example:

```toml
[[rule]]
id = "irreversible-needs-human"
when = { reversible = false }
decision = "ask"

[[rule]]
id = "workspace-read"
when = { capability = "fs:read:workspace:/**" }
decision = "allow"

[[rule]]
id = "trifecta-guard"
when = { run_has_untrusted_input = true, effect = "network" }
decision = "deny"
```

Every decision is journaled as `permission.decided` with the matched rule ID, so "why was this allowed?" always has an answer.

## 4. When an agent lacks a permission

The design goal: denial is information, not a crash, and escalation is never silent.

1. The policy engine returns `deny`. The tool is not executed.
2. The model receives a structured error:

```json
{"error": "permission_denied",
 "capability_needed": "fs:write:workspace:/src/parser.py",
 "you_have": ["fs:read:workspace:/**", "fs:write:workspace:/out/**"],
 "options": "Work within your capabilities, or call request_capability with a justification."}
```

3. The agent can adapt (write to `out/` instead), report `blocked` via `complete_task`, or call `request_capability(capability, justification, scope: "once" | "task")`.
4. `request_capability` goes to the approval gate. The human sees the agent, task, requested capability, justification, and the recent actions that led there. The grant (or refusal) is an event. Grants never exceed the spec's upper bound unless the human explicitly overrides, which is a separate, loudly recorded event.
5. Agents can never grant capabilities to themselves or to other agents beyond attenuation.

Experiment E006 measures what agents actually do in this situation: comply, find workarounds, give up, or argue.

## 5. Human approval

| Aspect | Design |
|---|---|
| Triggers | Policy `ask` decisions; `request_capability`; `ask_human` (Phase 2) |
| What the human sees | Agent spec and instance, task objective, the exact tool call (resolved input), required capabilities, matched rule, recent action summary, for writes a diff |
| Decisions | approve once · approve for this task · deny · deny with message to the agent |
| While waiting | Instance status `waiting_approval`; other instances keep running |
| Timeout | Configurable; on timeout → `approval_timeout` failure delivered as a tool error |
| Interfaces | Phase 1: blocking CLI prompt. Phase 3: Observatory approval queue |
| Record | `approval.requested` and `approval.resolved` events with who, when, decision, message |

Approval fatigue is real: too many prompts and the human rubber-stamps. Experiment E008 measures approval counts and decision latency per policy, so policies can be tuned on data.

## 6. Prompt injection and the lethal-trifecta rule

Tool outputs with `output_trust = untrusted` (file contents, later web pages, other agents' free text) may carry instructions. The kernel does not try to detect injections by inspecting text; detection is unreliable. Instead:

- Runs track a **taint flag** per instance: set once the instance has received untrusted content.
- The policy denies any **external communication** capability (network egress, sending messages) to a tainted instance whose run also has **access to private data**. One agent may hold at most two legs of the trifecta (RESEARCH §6).
- Phase 1–2 agents have no network capability at all, so the rule is trivially satisfied; it becomes active when web tools are introduced.

This is an application-level policy. It reduces risk only as far as taint propagation is modelled correctly (for example, a child agent briefed by a tainted parent is tainted too).
