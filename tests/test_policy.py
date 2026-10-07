from agentlab.policy import Policy


def make_policy():
    return Policy.from_rules(
        [
            {
                "id": "no-secrets",
                "capability": "filesystem.read",
                "paths": ["secrets/**"],
                "decision": "deny",
            },
            {"id": "read-all", "capability": "filesystem.read", "decision": "allow"},
            {
                "id": "write-out",
                "capability": "filesystem.write",
                "paths": ["out/**"],
                "decision": "allow",
            },
            {
                "id": "write-notes-ask",
                "capability": "filesystem.write",
                "paths": ["notes/**"],
                "decision": "ask",
            },
        ]
    )


def test_first_matching_rule_wins():
    p = make_policy()
    assert p.check("filesystem.read", "secrets/key.txt").as_tuple() == ("deny", "no-secrets")
    assert p.check("filesystem.read", "notes/a.md").as_tuple() == ("allow", "read-all")


def test_path_scopes_limit_a_capability():
    p = make_policy()
    assert p.check("filesystem.write", "out/report.md").decision == "allow"
    assert p.check("filesystem.write", "out/deep/x.md").decision == "allow"
    assert p.check("filesystem.write", "notes/a.md").decision == "ask"


def test_anything_unmatched_is_denied_by_default():
    p = make_policy()
    assert p.check("filesystem.write", "readme.txt").as_tuple() == ("deny", "default-deny")
    assert p.check("network.http", "x").as_tuple() == ("deny", "default-deny")


def test_path_matching_is_case_insensitive_like_windows():
    p = make_policy()
    assert p.check("filesystem.write", "OUT/Report.md").decision == "allow"


def test_invalid_decision_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        Policy.from_rules([{"id": "x", "capability": "filesystem.read", "decision": "maybe"}])
