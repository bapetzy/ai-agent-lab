"""The journal: an append-only log of everything that happens in a run.

Two SQLite tables:

- ``events``: one row per event. ``(run_id, seq)`` gives the order inside a run.
- ``blobs``: large payloads (full API requests/responses, file contents), stored once
  under the SHA-256 of their canonical JSON. Events refer to them by hash.

"Append-only" is enforced by the database itself: triggers abort any UPDATE or DELETE.
That is a guard against bugs in our own code, not a security boundary: anyone who can
open the file can still edit it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EVENT_TYPES = frozenset(
    {
        "run_started",  # task, agent config, limits, policy, workspace fixture
        "model_request",  # what we send to the model (full body in a blob)
        "model_response",  # what came back (full body in a blob) + usage + actual model
        "model_retry",  # a transient API error; the kernel will try again
        "model_error",  # a final API error
        "tool_requested",  # the model asked for a tool (tool_use block)
        "permission_checked",  # policy verdict: allow / deny / ask, and which rule
        "approval_requested",  # policy said "ask": waiting for a human
        "approval_resolved",  # the human's answer
        "permission_approved",  # final verdict: the tool may run
        "permission_denied",  # final verdict: the tool may not run
        "tool_started",
        "tool_completed",
        "tool_failed",
        "limit_reached",  # a budget limit stopped the run
        "check_result",  # one acceptance check of the task
        "run_completed",  # the agent stopped normally (says nothing about task success)
        "run_failed",  # the run ended abnormally
    }
)

REDACTED = "[REDACTED]"


def canonical_json(obj: Any) -> str:
    """One stable text form for any JSON value: sorted keys, no extra whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def new_id() -> str:
    """Time-ordered unique id (UUIDv7)."""
    return str(uuid.uuid7())


@dataclass(frozen=True)
class Event:
    run_id: str
    seq: int
    event_id: str
    ts: str
    type: str
    agent_id: str | None
    task_id: str | None
    payload: dict


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    run_id   TEXT    NOT NULL,
    seq      INTEGER NOT NULL,
    event_id TEXT    NOT NULL UNIQUE,
    ts       TEXT    NOT NULL,
    type     TEXT    NOT NULL,
    agent_id TEXT,
    task_id  TEXT,
    payload  TEXT    NOT NULL,
    PRIMARY KEY (run_id, seq)
) STRICT;

CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    data BLOB NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS blobs_no_update BEFORE UPDATE ON blobs
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
CREATE TRIGGER IF NOT EXISTS blobs_no_delete BEFORE DELETE ON blobs
BEGIN SELECT RAISE(ABORT, 'journal is append-only'); END;
"""


class Journal:
    def __init__(self, path: str | Path, *, secrets: list[str] | None = None):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), isolation_level=None)  # autocommit
        self.db.executescript(_SCHEMA)
        # Never write these strings to disk. Defence in depth: the API key should not
        # reach the journal in the first place.
        self._secrets = [s for s in (secrets or []) if s]

    def close(self) -> None:
        self.db.close()

    def _redact(self, text: str) -> str:
        for s in self._secrets:
            text = text.replace(s, REDACTED)
        return text

    # -- events -------------------------------------------------------------------

    def append(
        self,
        run_id: str,
        type: str,
        payload: dict,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
    ) -> Event:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {type!r}")
        payload_text = self._redact(canonical_json(payload))
        event_id = new_id()
        ts = datetime.now(UTC).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            (seq,) = self.db.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()
            self.db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, seq, event_id, ts, type, agent_id, task_id, payload_text),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return Event(run_id, seq, event_id, ts, type, agent_id, task_id, json.loads(payload_text))

    def events(self, run_id: str) -> list[Event]:
        rows = self.db.execute(
            "SELECT run_id, seq, event_id, ts, type, agent_id, task_id, payload "
            "FROM events WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [Event(*row[:7], json.loads(row[7])) for row in rows]

    def runs(self) -> list[Event]:
        """The run_started event of every run, oldest first."""
        rows = self.db.execute(
            "SELECT run_id, seq, event_id, ts, type, agent_id, task_id, payload "
            "FROM events WHERE type = 'run_started' ORDER BY ts"
        ).fetchall()
        return [Event(*row[:7], json.loads(row[7])) for row in rows]

    def find_run(self, fragment: str) -> str:
        """Find a run by a unique prefix or suffix of its id.

        UUIDv7 ids start with a timestamp, so runs started close together share long
        prefixes; the random end of the id (what ``agent-lab runs`` shows) is easier to type.
        """
        matches = [
            r
            for (r,) in self.db.execute(
                "SELECT DISTINCT run_id FROM events WHERE type = 'run_started' "
                "AND (run_id LIKE ? || '%' OR run_id LIKE '%' || ?)",
                (fragment, fragment),
            )
        ]
        if not matches:
            raise LookupError(f"no run id starts or ends with {fragment!r}")
        if len(matches) > 1:
            raise LookupError(f"run id fragment {fragment!r} is ambiguous: {len(matches)} runs")
        return matches[0]

    # -- blobs --------------------------------------------------------------------

    def put_blob(self, obj: Any) -> str:
        text = self._redact(canonical_json(obj))
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.db.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?, ?)", (digest, zlib.compress(text.encode()))
        )
        return digest

    def get_blob(self, digest: str) -> Any:
        row = self.db.execute("SELECT data FROM blobs WHERE hash = ?", (digest,)).fetchone()
        if row is None:
            raise KeyError(digest)
        return json.loads(zlib.decompress(row[0]).decode())


@dataclass(frozen=True)
class Recorder:
    """A journal bound to one run / agent / task, so every event gets the same ids."""

    journal: Journal
    run_id: str
    agent_id: str
    task_id: str

    def emit(self, type: str, payload: dict) -> Event:
        return self.journal.append(
            self.run_id, type, payload, agent_id=self.agent_id, task_id=self.task_id
        )

    def blob(self, obj: Any) -> str:
        return self.journal.put_blob(obj)
