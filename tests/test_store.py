"""Tests for campusfix/store.py persistence, concurrency, and integrity rules."""

from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import pytest

from campusfix.store import (
    add_issue,
    add_me_too,
    clean_nickname,
    confirm_review,
    get_issue,
    list_issues,
    nick_key,
    reopen,
    resolve,
)


@pytest.fixture
def temp_store(tmp_path):
    """Provide a path to a temporary store JSON file."""
    return tmp_path / "notes" / "issues.json"


def test_add_and_get_issue(temp_store):
    issue = add_issue(
        {
            "title": "Broken bench",
            "summary": "Bench near cafeteria has broken planks",
            "category": "Infrastructure & Building",
            "severity": "medium",
            "reporter": "alex_99",
            "location_hint": "Cafeteria lawn",
        },
        store_path=temp_store,
    )

    assert len(issue["id"]) == 6
    assert issue["status"] == "open"
    assert issue["confirmations"] == 0
    assert issue["reporter"] == "alex_99"
    assert len(issue["log"]) == 1
    assert issue["log"][0]["event"] == "created"
    assert issue["log"][0]["by"] == "alex_99"

    fetched = get_issue(issue["id"], store_path=temp_store)
    assert fetched is not None
    assert fetched["id"] == issue["id"]
    assert fetched["title"] == "Broken bench"


def test_list_issues_filter(temp_store):
    i1 = add_issue({"title": "Issue 1"}, store_path=temp_store)
    i2 = add_issue({"title": "Issue 2"}, store_path=temp_store)

    resolve(i1["id"], by="fixer_sam", note="Replaced the broken part properly", store_path=temp_store)

    all_issues = list_issues(store_path=temp_store)
    assert len(all_issues) == 2

    open_issues = list_issues(status="open", store_path=temp_store)
    assert len(open_issues) == 1
    assert open_issues[0]["id"] == i2["id"]

    resolved_issues = list_issues(status="resolved", store_path=temp_store)
    assert len(resolved_issues) == 1
    assert resolved_issues[0]["id"] == i1["id"]


def test_me_too_rules(temp_store):
    issue = add_issue(
        {"title": "Water leak", "reporter": "rahul"},
        store_path=temp_store,
    )
    issue_id = issue["id"]

    # 1. Reporter confirming own issue should be rejected
    msg_self = add_me_too(issue_id, by="rahul", store_path=temp_store)
    assert "cannot confirm your own" in msg_self
    assert get_issue(issue_id, store_path=temp_store)["confirmations"] == 0

    # 2. Another user confirms successfully
    msg_ok = add_me_too(issue_id, by="priya", store_path=temp_store)
    assert "recorded" in msg_ok
    assert get_issue(issue_id, store_path=temp_store)["confirmations"] == 1

    # 3. Duplicate confirmation from same user
    msg_dup = add_me_too(issue_id, by="Priya", store_path=temp_store)
    assert "already confirmed" in msg_dup
    assert get_issue(issue_id, store_path=temp_store)["confirmations"] == 1

    # 4. Anonymous confirmation is allowed
    msg_anon = add_me_too(issue_id, by="anonymous", store_path=temp_store)
    assert "recorded" in msg_anon
    assert get_issue(issue_id, store_path=temp_store)["confirmations"] == 2


def test_resolve_validation_and_reopen(temp_store):
    original_created = "2026-09-01T10:00:00+00:00"
    issue = add_issue(
        {
            "title": "Loose switch",
            "reporter": "karan",
            "created_at": original_created,
        },
        store_path=temp_store,
    )
    issue_id = issue["id"]

    # Anonymous resolve fails
    with pytest.raises(ValueError, match="Please provide a nickname"):
        resolve(issue_id, by="anonymous", note="Fixed it now", store_path=temp_store)

    # Note too short fails
    with pytest.raises(ValueError, match="at least 10 characters"):
        resolve(issue_id, by="fixer_dan", note="Done", store_path=temp_store)

    # Successful resolve
    resolved = resolve(
        issue_id,
        by="fixer_dan",
        note="Tightened the switch panel securely",
        store_path=temp_store,
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "fixer_dan"
    assert resolved["resolution_note"] == "Tightened the switch panel securely"
    assert resolved["log"][-1]["event"] == "resolved"

    # Reopen keeps original created_at
    reopened = reopen(issue_id, by="student_amy", store_path=temp_store)
    assert reopened["status"] == "open"
    assert reopened["created_at"] == original_created
    assert reopened["resolved_at"] is None
    assert reopened["resolved_by"] is None
    assert reopened["log"][-1]["event"] == "reopened"


def test_confirm_review(temp_store):
    issue = add_issue(
        {"title": "Unclear note", "needs_review": True, "category": "Other"},
        store_path=temp_store,
    )
    assert issue["needs_review"] is True

    reviewed = confirm_review(
        issue["id"],
        by="moderator_tim",
        category="Electrical",
        store_path=temp_store,
    )
    assert reviewed["needs_review"] is False
    assert reviewed["category"] == "Electrical"
    assert reviewed["log"][-1]["event"] == "review_confirmed"


def test_corrupt_file_recovery(temp_store):
    temp_store.parent.mkdir(parents=True, exist_ok=True)
    temp_store.write_text("{corrupt json file content -- invalid}", encoding="utf-8")

    # Should not crash, recovers empty and allows writing new issues
    new_issue = add_issue({"title": "After corruption"}, store_path=temp_store)
    assert new_issue["title"] == "After corruption"

    # Verify corrupt file was backed up
    corrupt_backups = list(temp_store.parent.glob("issues.corrupt.*.json"))
    assert len(corrupt_backups) == 1


def test_concurrent_writes(temp_store):
    def worker(idx):
        for i in range(10):
            add_issue(
                {"title": f"Worker {idx} Issue {i}", "reporter": f"worker_{idx}"},
                store_path=temp_store,
            )

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    issues = list_issues(store_path=temp_store)
    assert len(issues) == 50
