import sqlite3
import uuid

import pytest

from agentlab.journal import Journal


def test_append_assigns_increasing_seq_per_run():
    j = Journal(":memory:")
    a = [j.append("run-a", "run_started", {"n": i}) for i in range(3)]
    b = j.append("run-b", "run_started", {})
    assert [e.seq for e in a] == [1, 2, 3]
    assert b.seq == 1  # each run has its own sequence


def test_every_event_carries_identifiers_and_timestamp():
    j = Journal(":memory:")
    e = j.append("run-1", "tool_requested", {"tool": "read_file"}, agent_id="ag-1", task_id="t-1")
    stored = j.events("run-1")[0]
    assert stored.run_id == "run-1"
    assert stored.agent_id == "ag-1"
    assert stored.task_id == "t-1"
    assert stored.type == "tool_requested"
    assert stored.payload == {"tool": "read_file"}
    assert stored.ts == e.ts and stored.ts.endswith("+00:00")
    assert uuid.UUID(stored.event_id).version == 7


def test_unknown_event_type_is_rejected():
    j = Journal(":memory:")
    with pytest.raises(ValueError, match="unknown event type"):
        j.append("run-1", "tool_requestd", {})  # typo


def test_events_cannot_be_updated_or_deleted():
    j = Journal(":memory:")
    j.append("run-1", "run_started", {})
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        j.db.execute("UPDATE events SET type = 'run_failed'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        j.db.execute("DELETE FROM events")


def test_blobs_are_content_addressed_and_immutable():
    j = Journal(":memory:")
    h1 = j.put_blob({"b": 1, "a": [1, 2]})
    h2 = j.put_blob({"a": [1, 2], "b": 1})  # same content, different key order
    assert h1 == h2
    assert j.get_blob(h1) == {"a": [1, 2], "b": 1}
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        j.db.execute("DELETE FROM blobs")


def test_registered_secrets_never_reach_the_database(tmp_path):
    secret = "sk-ant-api03-THIS-IS-A-FAKE-TEST-SECRET"
    db = tmp_path / "lab.db"
    j = Journal(db, secrets=[secret])
    j.append("run-1", "model_error", {"message": f"bad key {secret}"})
    j.put_blob({"headers": {"x-api-key": secret}})
    j.close()
    assert secret.encode() not in db.read_bytes()
    j2 = Journal(db)
    assert "[REDACTED]" in j2.events("run-1")[0].payload["message"]


def test_run_ids_can_be_found_by_unique_prefix_or_suffix():
    j = Journal(":memory:")
    j.append("01abc-run", "run_started", {"task": "x"})
    j.append("01abd-run", "run_started", {"task": "y"})
    assert j.find_run("01abc") == "01abc-run"
    with pytest.raises(LookupError, match="ambiguous"):
        j.find_run("01ab")
    assert j.find_run("d-run") == "01abd-run"  # suffix: UUIDv7 prefixes are timestamps
    with pytest.raises(LookupError, match="no run"):
        j.find_run("zzz")
