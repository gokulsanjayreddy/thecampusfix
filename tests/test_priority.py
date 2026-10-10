"""Tests for priority scoring, age calculations, labels, and ranking."""

from datetime import datetime, timedelta, timezone
import pytest

from campusfix.priority import age_days, age_label, priority_score, rank_open


def test_new_critical():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": now.isoformat(),
        "severity": "critical",
        "confirmations": 0,
        "status": "open",
    }
    assert priority_score(issue, now) == 100.0


def test_high_at_20_days():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": (now - timedelta(days=20)).isoformat(),
        "severity": "high",
        "confirmations": 0,
        "status": "open",
    }
    # 60 + 20 * 1.5 = 90
    assert priority_score(issue, now) == 90.0


def test_new_high():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": now.isoformat(),
        "severity": "high",
        "confirmations": 0,
        "status": "open",
    }
    assert priority_score(issue, now) == 60.0


def test_medium_at_20_days():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": (now - timedelta(days=20)).isoformat(),
        "severity": "medium",
        "confirmations": 0,
        "status": "open",
    }
    # 30 + 20 * 1.5 = 60
    assert priority_score(issue, now) == 60.0


def test_low_at_40_plus_days_and_cap_at_60_days():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue_40 = {
        "created_at": (now - timedelta(days=40)).isoformat(),
        "severity": "low",
        "confirmations": 0,
        "status": "open",
    }
    # 10 + 40 * 1.5 = 70
    assert priority_score(issue_40, now) == 70.0

    issue_60 = {
        "created_at": (now - timedelta(days=60)).isoformat(),
        "severity": "low",
        "confirmations": 0,
        "status": "open",
    }
    # capped at 40 days: 10 + 40 * 1.5 = 70
    assert priority_score(issue_60, now) == 70.0


def test_new_low():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": now.isoformat(),
        "severity": "low",
        "confirmations": 0,
        "status": "open",
    }
    assert priority_score(issue, now) == 10.0


def test_confirmations_capped_at_plus_10():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)
    issue = {
        "created_at": now.isoformat(),
        "severity": "low",
        "confirmations": 8,  # 8 * 2 = 16 -> capped at 10
        "status": "open",
    }
    assert priority_score(issue, now) == 20.0


def test_age_label():
    assert age_label(0) == ""
    assert age_label(6.9) == ""
    assert age_label(7) == "Waiting a week"
    assert age_label(13.9) == "Waiting a week"
    assert age_label(14) == "Aging"
    assert age_label(29.9) == "Aging"
    assert age_label(30) == "Neglected"
    assert age_label(45) == "Neglected"


def test_rank_open_sorting_and_filtering():
    now = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)

    older_item = {
        "id": "old-med",
        "created_at": (now - timedelta(days=20)).isoformat(),
        "severity": "medium",  # score = 60
        "confirmations": 0,
        "status": "open",
    }
    new_high_item = {
        "id": "new-hi",
        "created_at": now.isoformat(),
        "severity": "high",  # score = 60
        "confirmations": 0,
        "status": "open",
    }
    old_low_item = {
        "id": "old-low",
        "created_at": (now - timedelta(days=45)).isoformat(),
        "severity": "low",  # score = 70
        "confirmations": 0,
        "status": "open",
    }
    resolved_item = {
        "id": "resolved",
        "created_at": (now - timedelta(days=5)).isoformat(),
        "severity": "critical",
        "confirmations": 0,
        "status": "resolved",
    }
    review_item = {
        "id": "review",
        "created_at": (now - timedelta(days=1)).isoformat(),
        "severity": "critical",
        "confirmations": 0,
        "status": "open",
        "needs_review": True,
    }

    issues = [new_high_item, older_item, resolved_item, review_item, old_low_item]
    ranked = rank_open(issues, now)

    # resolved and needs_review are excluded
    ids = [item["id"] for item in ranked]
    assert "resolved" not in ids
    assert "review" not in ids

    # old_low_item (70) ranks above both score-60 items
    assert ids[0] == "old-low"

    # between the two score-60 items, older item is listed first (earlier created_at)
    assert ids[1] == "old-med"
    assert ids[2] == "new-hi"
