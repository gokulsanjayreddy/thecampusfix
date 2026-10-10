"""Leaderboard module: points replay, badges, ranking, and campus statistics"""
#this is leaderboard
from datetime import datetime, timedelta, timezone
import re

from campusfix.config import (
    MIN_RESOLVE_NOTE_CHARS,
    PTS_CONFIRM_DAILY_CAP,
    PTS_CONFIRM_OTHERS,
    PTS_FULL_REPORT_BONUS,
    PTS_OLD_ISSUE_CAP,
    PTS_OLD_ISSUE_PER_7_DAYS,
    PTS_REPORT,
    PTS_REPORTER_CONFIRM_CAP,
    PTS_REPORTER_PER_CONFIRM,
    PTS_RESOLVE_OTHERS,
    PTS_RESOLVE_OWN,
)
from campusfix.priority import _parse_utc, age_days


def clean_nickname(raw):
    """Sanitize a user-provided nickname or return 'anonymous'."""
    if raw is None:
        return "anonymous"
    text = re.sub(r"\s+", " ", str(raw).strip())
    if not text:
        return "anonymous"
    if text.lower() in ("anonymous", "anon"):
        return "anonymous"
    if text.lower() in ("admin", "system"):
        raise ValueError(f"The nickname '{text}' is reserved. Please pick another nickname.")
    if len(text) > 24:
        text = text[:24].strip()
    if not re.match(r"^[A-Za-z0-9 _.\-]+$", text):
        raise ValueError("Nicknames may only contain letters, numbers, spaces, underscores, dots, and hyphens.")
    return text


def nick_key(name):
    """Normalize a nickname for case-insensitive matching."""
    return (clean_nickname(name) if name else "anonymous").lower()


def _is_within_window(event_time, now_utc, window_days):
    """Check if an event falls within the rolling window."""
    if window_days is None:
        return True
    window_start = now_utc - timedelta(days=float(window_days))
    return window_start <= event_time <= now_utc


def compute_leaderboard(issues, now, window_days=None):
    """Replay issue event logs to calculate user points, activities, and badges."""
    now_utc = _parse_utc(now)

    # Per-user tracking: key -> dict
    # {
    #   "name": display_name,
    #   "points": int,
    #   "reports": int,
    #   "confirmations_given": int,
    #   "resolves": int,
    #   "old_timer_resolves": int,
    #   "earliest_activity": timestamp,
    #   "daily_confirm_pts": { "YYYY-MM-DD": int },
    # }
    users = {}

    def get_user_slot(nickname, event_dt):
        key = nick_key(nickname)
        if key == "anonymous":
            return None
        if key not in users:
            users[key] = {
                "name": clean_nickname(nickname),
                "points": 0,
                "reports": 0,
                "confirmations_given": 0,
                "resolves": 0,
                "old_timer_resolves": 0,
                "earliest_activity": event_dt.timestamp(),
                "daily_confirm_pts": {},
            }
        else:
            if event_dt.timestamp() < users[key]["earliest_activity"]:
                users[key]["earliest_activity"] = event_dt.timestamp()
        return users[key]

    for issue in issues:
        is_review = bool(issue.get("needs_review", False))
        reporter = issue.get("reporter", "anonymous")
        reporter_key = nick_key(reporter)
        created_at_dt = _parse_utc(issue.get("created_at", now_utc))
        has_photo_and_text = bool(issue.get("image")) and bool((issue.get("text") or "").strip())

        log = issue.get("log", [])

        # Step 1: Annotate resolves and match with subsequent reopen events
        # A resolve is cancelled if followed by a reopened event before another resolve
        active_resolves = set()
        last_resolve_idx = None
        for idx, event in enumerate(log):
            ev_type = event.get("event")
            if ev_type == "resolved":
                last_resolve_idx = idx
            elif ev_type == "reopened":
                last_resolve_idx = None

        if last_resolve_idx is not None:
            active_resolves.add(last_resolve_idx)

        # Step 2: Track reporter confirm bonuses per issue
        reporter_confirm_pts_earned = 0

        # Step 3: Replay log events
        for idx, event in enumerate(log):
            ev_type = event.get("event")
            actor = event.get("by", "anonymous")
            actor_key = nick_key(actor)
            event_dt = _parse_utc(event.get("at", created_at_dt))

            in_window = _is_within_window(event_dt, now_utc, window_days)

            if ev_type == "created":
                # Only earn points if not currently flagged for review and actor not anonymous
                if not is_review and in_window:
                    user_slot = get_user_slot(actor, event_dt)
                    if user_slot:
                        user_slot["reports"] += 1
                        pts = PTS_REPORT
                        if has_photo_and_text:
                            pts += PTS_FULL_REPORT_BONUS
                        user_slot["points"] += pts

            elif ev_type == "me_too":
                # Confirming someone else's issue
                if actor_key != "anonymous" and actor_key != reporter_key:
                    if in_window:
                        actor_slot = get_user_slot(actor, event_dt)
                        if actor_slot:
                            actor_slot["confirmations_given"] += 1
                            # Daily cap on confirmation points
                            day_key = event_dt.strftime("%Y-%m-%d")
                            today_pts = actor_slot["daily_confirm_pts"].get(day_key, 0)
                            allowance = max(0, PTS_CONFIRM_DAILY_CAP - today_pts)
                            awarded = min(PTS_CONFIRM_OTHERS, allowance)
                            actor_slot["daily_confirm_pts"][day_key] = today_pts + awarded
                            actor_slot["points"] += awarded

                # Reporter gets bonus when someone else confirms their issue
                if (
                    not is_review
                    and reporter_key != "anonymous"
                    and actor_key != "anonymous"
                    and actor_key != reporter_key
                ):
                    if reporter_confirm_pts_earned < PTS_REPORTER_CONFIRM_CAP:
                        reporter_confirm_pts_earned += PTS_REPORTER_PER_CONFIRM
                        if in_window:
                            rep_slot = get_user_slot(reporter, event_dt)
                            if rep_slot:
                                rep_slot["points"] += PTS_REPORTER_PER_CONFIRM

            elif ev_type == "resolved":
                # Only award points if this resolve stayed resolved (was not reopened)
                if idx in active_resolves:
                    note = event.get("note", "")
                    if len(note) >= MIN_RESOLVE_NOTE_CHARS and actor_key != "anonymous":
                        # Compute age in days at resolve time
                        age_days_at_resolve = max(0.0, (event_dt - created_at_dt).total_seconds() / 86400.0)
                        is_own_issue = actor_key == reporter_key

                        if in_window:
                            actor_slot = get_user_slot(actor, event_dt)
                            if actor_slot:
                                actor_slot["resolves"] += 1
                                if is_own_issue:
                                    actor_slot["points"] += PTS_RESOLVE_OWN
                                else:
                                    old_bonus = min(int(age_days_at_resolve // 7) * PTS_OLD_ISSUE_PER_7_DAYS, PTS_OLD_ISSUE_CAP)
                                    actor_slot["points"] += PTS_RESOLVE_OTHERS + old_bonus
                                    if age_days_at_resolve >= 30:
                                        actor_slot["old_timer_resolves"] += 1

    # Format rows and compute badges
    rows = []
    for user_info in users.values():
        badges = []
        if user_info["reports"] >= 1:
            badges.append("First Report")
        if user_info["reports"] >= 5:
            badges.append("Regular")
        if user_info["resolves"] >= 3:
            badges.append("Fixer")
        if user_info["old_timer_resolves"] >= 1:
            badges.append("Old-Timer Slayer")
        if user_info["confirmations_given"] >= 10:
            badges.append("Team Player")

        rows.append({
            "name": user_info["name"],
            "points": user_info["points"],
            "reports": user_info["reports"],
            "confirmations_given": user_info["confirmations_given"],
            "resolves": user_info["resolves"],
            "badges": ", ".join(badges) if badges else "-",
            "_resolves": user_info["resolves"],
            "_earliest": user_info["earliest_activity"],
        })

    # Tie-breaking: points desc, resolves desc, earliest activity asc
    rows.sort(key=lambda r: (-r["points"], -r["_resolves"], r["_earliest"]))

    # Assign ranks
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        del row["_resolves"]
        del row["_earliest"]

    return rows


def rank_of(rows, nickname):
    """Find the 1-based rank of a nickname in computed leaderboard rows."""
    if not nickname:
        return None
    key = nick_key(nickname)
    if key == "anonymous":
        return None
    for row in rows:
        if nick_key(row["name"]) == key:
            return row["rank"]
    return None


def campus_stats(issues, now):
    """Compute aggregate campus statistics from issue data."""
    now_utc = _parse_utc(now)
    week_ago = now_utc - timedelta(days=7)

    open_issues = [i for i in issues if i.get("status") == "open"]
    resolved_this_week = 0

    for i in issues:
        if i.get("status") == "resolved" and i.get("resolved_at"):
            res_dt = _parse_utc(i["resolved_at"])
            if res_dt >= week_ago:
                resolved_this_week += 1

    oldest_open = None
    oldest_age = 0.0
    total_age = 0.0

    for i in open_issues:
        days = age_days(i, now_utc)
        total_age += days
        if days >= oldest_age:
            oldest_age = days
            oldest_open = i

    avg_age = (total_age / len(open_issues)) if open_issues else 0.0

    return {
        "open_count": len(open_issues),
        "resolved_this_week": resolved_this_week,
        "oldest_open": oldest_open,
        "oldest_age_days": round(oldest_age, 1),
        "average_age_days": round(avg_age, 1),
    }
