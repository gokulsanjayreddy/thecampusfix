"""Tests for campusfix/leaderboard.py points replay, badges, windowing, and rankings."""

from datetime import datetime, timedelta, timezone
import pytest

from campusfix.config import (
    PTS_CONFIRM_DAILY_CAP,
    PTS_CONFIRM_OTHERS,
    PTS_FULL_REPORT_BONUS,
    PTS_OLD_ISSUE_CAP,
    PTS_REPORT,
    PTS_REPORTER_CONFIRM_CAP,
    PTS_REPORTER_PER_CONFIRM,
    PTS_RESOLVE_OTHERS,
    PTS_RESOLVE_OWN,
)
from campusfix.leaderboard import (
    campus_stats,
    clean_nickname,
    compute_leaderboard,
    nick_key,
    rank_of,
)


def test_nickname_cleaning_and_reserved():
    assert clean_nickname("  rahul_dev  ") == "rahul_dev"
    assert clean_nickname("alice   bob") == "alice bob"
    assert clean_nickname("") == "anonymous"
    assert clean_nickname(None) == "anonymous"
    assert clean_nickname("anon") == "anonymous"
    assert clean_nickname("ANONYMOUS") == "anonymous"

    # Reserved names
    with pytest.raises(ValueError, match="reserved"):
        clean_nickname("admin")
    with pytest.raises(ValueError, match="reserved"):
        clean_nickname("Admin")
    with pytest.raises(ValueError, match="reserved"):
        clean_nickname("SYSTEM")

    # Invalid characters
    with pytest.raises(ValueError, match="letters, numbers"):
        clean_nickname("user@campus<hack>")

    # Truncation at 24 chars
    long_name = "a" * 30
    assert len(clean_nickname(long_name)) == 24


def test_report_points_and_full_report_bonus():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Standard text report = PTS_REPORT (10)
    issue1 = {
        "id": "iss001",
        "reporter": "alice",
        "created_at": now.isoformat(),
        "text": "Water tap loose",
        "image": None,
        "needs_review": False,
        "log": [{"at": now.isoformat(), "event": "created", "by": "alice"}],
    }

    # 2. Full report (text + photo) = PTS_REPORT + PTS_FULL_REPORT_BONUS (10 + 3 = 13)
    issue2 = {
        "id": "iss002",
        "reporter": "bob",
        "created_at": now.isoformat(),
        "text": "Broken window in library",
        "image": "notes/images/iss002.jpg",
        "needs_review": False,
        "log": [{"at": now.isoformat(), "event": "created", "by": "bob"}],
    }

    rows = compute_leaderboard([issue1, issue2], now)
    score_by_name = {r["name"]: r["points"] for r in rows}

    assert score_by_name["alice"] == PTS_REPORT
    assert score_by_name["bob"] == PTS_REPORT + PTS_FULL_REPORT_BONUS


def test_needs_review_and_anonymous_earn_nothing():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # Needs review issue
    issue_review = {
        "id": "iss003",
        "reporter": "charlie",
        "created_at": now.isoformat(),
        "text": "Spam message here",
        "needs_review": True,
        "log": [{"at": now.isoformat(), "event": "created", "by": "charlie"}],
    }

    # Anonymous report
    issue_anon = {
        "id": "iss004",
        "reporter": "anonymous",
        "created_at": now.isoformat(),
        "text": "Anonymous leak",
        "needs_review": False,
        "log": [{"at": now.isoformat(), "event": "created", "by": "anonymous"}],
    }

    rows = compute_leaderboard([issue_review, issue_anon], now)
    assert len(rows) == 0


def test_confirmations_points_and_caps():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)

    # Reporter is devan. 6 different people confirm
    confirm_events = []
    for i in range(7):
        confirm_events.append({
            "at": (now + timedelta(minutes=i + 1)).isoformat(),
            "event": "me_too",
            "by": f"student_{i}",
        })

    issue = {
        "id": "iss005",
        "reporter": "devan",
        "created_at": now.isoformat(),
        "text": "Mess food shortage",
        "needs_review": False,
        "log": [{"at": now.isoformat(), "event": "created", "by": "devan"}] + confirm_events,
    }

    rows = compute_leaderboard([issue], now + timedelta(hours=1))
    score_map = {r["name"]: r["points"] for r in rows}

    # Reporter gets PTS_REPORT (10) + capped confirm bonuses (max 5) = 15
    assert score_map["devan"] == PTS_REPORT + PTS_REPORTER_CONFIRM_CAP

    # Each confirmer gets PTS_CONFIRM_OTHERS (2)
    assert score_map["student_0"] == PTS_CONFIRM_OTHERS


def test_confirmations_daily_cap():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    # student_x confirms 15 different issues on the same day
    issues = []
    for i in range(15):
        iss = {
            "id": f"c_{i}",
            "reporter": f"reporter_{i}",
            "created_at": now.isoformat(),
            "needs_review": False,
            "log": [
                {"at": now.isoformat(), "event": "created", "by": f"reporter_{i}"},
                {"at": (now + timedelta(minutes=i + 5)).isoformat(), "event": "me_too", "by": "super_confirmer"},
            ],
        }
        issues.append(iss)

    rows = compute_leaderboard(issues, now + timedelta(hours=2))
    score_map = {r["name"]: r["points"] for r in rows}

    # Capped at PTS_CONFIRM_DAILY_CAP (20 points max from confirmations per day)
    assert score_map["super_confirmer"] == PTS_CONFIRM_DAILY_CAP


def test_resolve_points_own_vs_others_and_old_issue_bonus():
    created_dt = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    resolve_dt = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # 30 days later: 30 // 7 = 4 bonus points
    now = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

    # 1. Resolving someone else's 30-day old issue
    other_issue = {
        "id": "old_01",
        "reporter": "victim_user",
        "created_at": created_dt.isoformat(),
        "needs_review": False,
        "log": [
            {"at": created_dt.isoformat(), "event": "created", "by": "victim_user"},
            {
                "at": resolve_dt.isoformat(),
                "event": "resolved",
                "by": "campus_hero",
                "note": "Replaced the rusted pipe after thorough check",
            },
        ],
    }

    # 2. Resolving own issue
    own_issue = {
        "id": "own_01",
        "reporter": "self_fixer",
        "created_at": created_dt.isoformat(),
        "needs_review": False,
        "log": [
            {"at": created_dt.isoformat(), "event": "created", "by": "self_fixer"},
            {
                "at": resolve_dt.isoformat(),
                "event": "resolved",
                "by": "self_fixer",
                "note": "Found my lost keys and closed the ticket",
            },
        ],
    }

    rows = compute_leaderboard([other_issue, own_issue], now)
    score_map = {r["name"]: r["points"] for r in rows}

    # campus_hero: PTS_RESOLVE_OTHERS (15) + 4 old bonus (30//7 = 4) = 19
    assert score_map["campus_hero"] == PTS_RESOLVE_OTHERS + 4

    # self_fixer: PTS_REPORT (10) + PTS_RESOLVE_OWN (5) = 15 (no old bonus)
    assert score_map["self_fixer"] == PTS_REPORT + PTS_RESOLVE_OWN


def test_reopen_removes_resolve_points_and_second_resolver_earns_them():
    t0 = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    t_res1 = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    t_reopen = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
    t_res2 = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)

    issue = {
        "id": "cycle_01",
        "reporter": "reporter_raj",
        "created_at": t0.isoformat(),
        "needs_review": False,
        "log": [
            {"at": t0.isoformat(), "event": "created", "by": "reporter_raj"},
            {"at": t_res1.isoformat(), "event": "resolved", "by": "false_resolver", "note": "Claimed fixed"},
            {"at": t_reopen.isoformat(), "event": "reopened", "by": "reporter_raj"},
            {"at": t_res2.isoformat(), "event": "resolved", "by": "true_resolver", "note": "Actually fixed and sealed properly"},
        ],
    }

    rows = compute_leaderboard([issue], now)
    score_map = {r["name"]: r["points"] for r in rows}

    # false_resolver earned 0 because their resolve was reopened
    assert "false_resolver" not in score_map

    # true_resolver earned resolve points
    assert score_map["true_resolver"] == PTS_RESOLVE_OTHERS


def test_window_days_excludes_older_events():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    old_time = now - timedelta(days=20)
    recent_time = now - timedelta(days=2)

    old_issue = {
        "id": "old_w",
        "reporter": "ancient_user",
        "created_at": old_time.isoformat(),
        "needs_review": False,
        "log": [{"at": old_time.isoformat(), "event": "created", "by": "ancient_user"}],
    }

    recent_issue = {
        "id": "new_w",
        "reporter": "recent_user",
        "created_at": recent_time.isoformat(),
        "needs_review": False,
        "log": [{"at": recent_time.isoformat(), "event": "created", "by": "recent_user"}],
    }

    # 7-day window
    rows = compute_leaderboard([old_issue, recent_issue], now, window_days=7)
    score_map = {r["name"]: r["points"] for r in rows}

    assert "ancient_user" not in score_map
    assert score_map["recent_user"] == PTS_REPORT


def test_badges_derivation():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    old_created = now - timedelta(days=35)

    # User earns First Report, Regular (5+ reports), Fixer (3+ resolves), Old-Timer Slayer, Team Player (10+ confirms)
    issues = []
    # 5 reports
    for i in range(5):
        issues.append({
            "id": f"badge_rep_{i}",
            "reporter": "ace_student",
            "created_at": now.isoformat(),
            "needs_review": False,
            "log": [{"at": now.isoformat(), "event": "created", "by": "ace_student"}],
        })

    # 3 resolves, 1 of which is 35 days old
    for i in range(3):
        created_ts = old_created if i == 0 else now
        issues.append({
            "id": f"badge_res_{i}",
            "reporter": "other_guy",
            "created_at": created_ts.isoformat(),
            "needs_review": False,
            "log": [
                {"at": created_ts.isoformat(), "event": "created", "by": "other_guy"},
                {"at": now.isoformat(), "event": "resolved", "by": "ace_student", "note": "Resolved and tested thoroughly"},
            ],
        })

    # 10 confirmations
    for i in range(10):
        issues.append({
            "id": f"badge_conf_{i}",
            "reporter": f"person_{i}",
            "created_at": now.isoformat(),
            "needs_review": False,
            "log": [
                {"at": now.isoformat(), "event": "created", "by": f"person_{i}"},
                {"at": (now + timedelta(days=i)).isoformat(), "event": "me_too", "by": "ace_student"},
            ],
        })

    rows = compute_leaderboard(issues, now + timedelta(days=12))
    ace_row = next(r for r in rows if r["name"] == "ace_student")

    assert "First Report" in ace_row["badges"]
    assert "Regular" in ace_row["badges"]
    assert "Fixer" in ace_row["badges"]
    assert "Old-Timer Slayer" in ace_row["badges"]
    assert "Team Player" in ace_row["badges"]


def test_rank_of_and_tie_breaking():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    t1 = now - timedelta(hours=5)
    t2 = now - timedelta(hours=2)

    # User 1 and User 2 have same points (10), 0 resolves, but User 1 was active earlier
    i1 = {
        "id": "t1",
        "reporter": "user_one",
        "created_at": t1.isoformat(),
        "needs_review": False,
        "log": [{"at": t1.isoformat(), "event": "created", "by": "user_one"}],
    }
    i2 = {
        "id": "t2",
        "reporter": "user_two",
        "created_at": t2.isoformat(),
        "needs_review": False,
        "log": [{"at": t2.isoformat(), "event": "created", "by": "user_two"}],
    }

    rows = compute_leaderboard([i1, i2], now)
    assert rows[0]["name"] == "user_one"
    assert rows[1]["name"] == "user_two"

    assert rank_of(rows, "user_one") == 1
    assert rank_of(rows, "user_two") == 2
    assert rank_of(rows, "unknown_person") is None


def test_campus_stats():
    now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    t_20_days_ago = now - timedelta(days=20)
    t_2_days_ago = now - timedelta(days=2)

    issues = [
        {
            "id": "i1",
            "status": "open",
            "created_at": t_20_days_ago.isoformat(),
        },
        {
            "id": "i2",
            "status": "open",
            "created_at": t_2_days_ago.isoformat(),
        },
        {
            "id": "i3",
            "status": "resolved",
            "created_at": t_20_days_ago.isoformat(),
            "resolved_at": (now - timedelta(days=1)).isoformat(),
        },
    ]

    stats = campus_stats(issues, now)
    assert stats["open_count"] == 2
    assert stats["resolved_this_week"] == 1
    assert stats["oldest_open"]["id"] == "i1"
    assert stats["oldest_age_days"] == 20.0
    assert stats["average_age_days"] == 11.0  # (20 + 2) / 2 = 11.0
