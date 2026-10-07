"""The permission policy: decides whether one tool call may run.

A rule says: for this capability (and optionally only these paths), the decision is
allow / deny / ask. Rules are checked in order; the first match wins; if nothing
matches, the answer is deny ("default deny").

This is an application-level policy mechanism. The kernel asks it before running a tool,
so it controls what agents can do *through the kernel's tools*. It is not isolation: it
cannot stop anything that does not go through the kernel. See docs/SANDBOX.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

DECISIONS = ("allow", "deny", "ask")


@dataclass(frozen=True)
class Rule:
    id: str
    capability: str
    decision: str
    paths: tuple[str, ...] = ("**",)

    def matches(self, capability: str, path: str | None) -> bool:
        if capability != self.capability:
            return False
        if path is None:
            return True
        p = PurePosixPath(path.lower())
        return any(p.full_match(pattern.lower()) for pattern in self.paths)


@dataclass(frozen=True)
class Decision:
    decision: str  # allow | deny | ask
    rule_id: str

    def as_tuple(self) -> tuple[str, str]:
        return (self.decision, self.rule_id)


class Policy:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    @classmethod
    def from_rules(cls, rules: list[dict]) -> Policy:
        parsed = []
        for r in rules:
            if r.get("decision") not in DECISIONS:
                raise ValueError(f"rule {r.get('id')!r}: decision must be one of {DECISIONS}")
            parsed.append(
                Rule(
                    id=r["id"],
                    capability=r["capability"],
                    decision=r["decision"],
                    paths=tuple(r.get("paths", ["**"])),
                )
            )
        return cls(parsed)

    def check(self, capability: str, path: str | None) -> Decision:
        for rule in self.rules:
            if rule.matches(capability, path):
                return Decision(rule.decision, rule.id)
        return Decision("deny", "default-deny")

    def to_list(self) -> list[dict]:
        return [
            {"id": r.id, "capability": r.capability, "decision": r.decision, "paths": list(r.paths)}
            for r in self.rules
        ]
