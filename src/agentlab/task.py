"""Tasks: what the agent should do, with which model and limits, and how we check the result.

A task is a directory:

    tasks/<name>/
        task.toml      [task] prompt, [model] backend + model name, [agent] system prompt +
                       tools, [limits], [[policy]] rules, [[check]]s
        workspace/     the files the agent starts with (copied for every run)

Checks are deterministic. They decide task *success*, which is a different thing from the
agent *finishing* (an agent can stop confidently and still be wrong).
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agentlab.budget import Limits
from agentlab.journal import canonical_json
from agentlab.policy import Policy
from agentlab.tools import TOOLS
from agentlab.workspace import Workspace, WorkspaceError

CHECK_TYPES = {"file_exists", "file_contains", "file_not_contains", "final_text_contains"}
BACKENDS = {"scripted", "openai_compat", "anthropic"}


@dataclass
class Task:
    name: str
    prompt: str
    model: dict  # backend, name, temperature, seed, options
    agent: dict  # system, tools
    limits: Limits
    policy: Policy
    checks: list[dict]
    workspace_fixture: Path
    source: dict = field(repr=False)  # the raw task definition, recorded with every run

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.source).encode()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict, *, workspace_fixture: Path) -> Task:
        m = dict(data.get("model", {}))
        if m.get("backend") not in BACKENDS:
            raise ValueError(
                f"[model] backend must be one of {sorted(BACKENDS)}, got {m.get('backend')!r}"
            )
        if not m.get("name"):
            raise ValueError("[model] needs 'name'")
        model = {
            "backend": m["backend"],
            "name": m["name"],
            "options": m.get("options", {}),
            "temperature": m.get("temperature"),
            "seed": m.get("seed"),
        }
        agent = dict(data["agent"])
        for key in ("system", "tools"):
            if key not in agent:
                raise ValueError(f"[agent] needs {key!r}")
        unknown = [t for t in agent["tools"] if t not in TOOLS]
        if unknown:
            raise ValueError(f"unknown tools: {unknown}; available: {sorted(TOOLS)}")
        checks = list(data.get("check", []))
        for c in checks:
            if c.get("type") not in CHECK_TYPES:
                raise ValueError(f"unknown check type {c.get('type')!r}; use one of {CHECK_TYPES}")
        return cls(
            name=data["task"]["name"],
            prompt=data["task"]["prompt"],
            model=model,
            agent=agent,
            limits=Limits(**data.get("limits", {})),
            policy=Policy.from_rules(data.get("policy", [])),
            checks=checks,
            workspace_fixture=Path(workspace_fixture),
            source=data,
        )

    @classmethod
    def load(cls, path: str | Path) -> Task:
        path = Path(path)
        task_dir = path.parent if path.is_file() else path
        with open(task_dir / "task.toml", "rb") as f:
            data = tomllib.load(f)
        return cls.from_dict(data, workspace_fixture=task_dir / "workspace")


def evaluate_checks(checks: list[dict], workspace: Workspace, final_text: str) -> list[dict]:
    results = []
    for check in checks:
        kind = check["type"]
        passed, detail = False, ""
        try:
            if kind == "file_exists":
                passed = workspace.resolve(check["path"]).is_file()
            elif kind == "file_contains":
                passed = check["text"].lower() in workspace.read_text(check["path"]).lower()
            elif kind == "file_not_contains":
                passed = check["text"].lower() not in workspace.read_text(check["path"]).lower()
            elif kind == "final_text_contains":
                passed = check["text"].lower() in final_text.lower()
        except WorkspaceError as e:
            detail = str(e)
        results.append({**check, "passed": passed, "detail": detail})
    return results
