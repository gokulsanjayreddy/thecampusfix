"""Ranking and priority scoring for CampusFix issues."""

from datetime import datetime, timezone

from campusfix.config import (
    AGE_CAP_DAYS,
    AGING_PER_DAY,
    CONFIRM_BONUS_CAP,
    CONFIRM_BONUS_EACH,
    SEVERITY_BASE,
)


def _parse_utc(dt_val):
    """Parse a datetime value or ISO string into an aware UTC datetime."""
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is None:
            return dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(timezone.utc)
    if isinstance(dt_val, str):
        cleaned = dt_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raise ValueError(f"Cannot parse datetime: {dt_val}")


def age_days(issue, now):
    """Calculate days since the issue's original created_at timestamp."""
    created_at = _parse_utc(issue.get("created_at"))
    now_utc = _parse_utc(now)
    delta_seconds = (now_utc - created_at).total_seconds()
    return max(0.0, delta_seconds / 86400.0)


def priority_score(issue, now):
    """Calculate the priority score based on severity, aging, and confirmations."""
    severity = issue.get("severity", "medium").lower()
    base = SEVERITY_BASE.get(severity, SEVERITY_BASE["medium"])
    days = age_days(issue, now)
    capped_days = min(days, float(AGE_CAP_DAYS))
    confirmations = int(issue.get("confirmations", 0))
    confirm_bonus = min(confirmations * CONFIRM_BONUS_EACH, CONFIRM_BONUS_CAP)
    return round(base + (capped_days * AGING_PER_DAY) + confirm_bonus, 2)


def age_label(days):
    """Return an age status label based on elapsed days."""
    if days >= 30:
        return "Neglected"
    if days >= 14:
        return "Aging"
    if days >= 7:
        return "Waiting a week"
    return ""


def rank_open(issues, now):
    """Filter to active open issues, compute scores, and rank by score desc then created_at asc."""
    open_issues = [
        issue for issue in issues
        if issue.get("status") == "open" and not issue.get("needs_review", False)
    ]

    ranked = []
    for issue in open_issues:
        item = dict(issue)
        days = age_days(item, now)
        score = priority_score(item, now)
        label = age_label(days)
        item["score"] = score
        item["age_days"] = round(days, 2)
        item["age_label"] = label
        ranked.append(item)

    # Sort descending by score, then ascending by created_at timestamp
    ranked.sort(
        key=lambda x: (
            -x["score"],
            _parse_utc(x["created_at"]).timestamp()
        )
    )
    return ranked
