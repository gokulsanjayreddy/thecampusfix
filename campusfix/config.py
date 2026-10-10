"""Configuration and constants for CampusFix."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Read .env if present
try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _number(name, default):
    """Read a number from an environment variable. Use default if missing or invalid."""
    try:
        val = os.environ.get(name, "").strip()
        return float(val) if val else default
    except (ValueError, TypeError):
        return default


CATEGORIES = [
    "Infrastructure & Building",
    "Electrical",
    "Water, Plumbing & Washrooms",
    "Cleanliness & Waste",
    "Hostel",
    "Mess & Food",
    "Classrooms & Labs",
    "Wi-Fi & IT",
    "Safety & Security",
    "Transport & Roads",
    "Library & Academic Services",
    "Other",
]

SEVERITIES = ["low", "medium", "high", "critical"]

SEVERITY_BASE = {
    "critical": 100,
    "high": 60,
    "medium": 30,
    "low": 10,
}

# Environmental overrides with safe fallbacks
AGING_PER_DAY = float(_number("AGING_PER_DAY", 1.5))
AGE_CAP_DAYS = int(_number("AGE_CAP_DAYS", 40))
CONFIRM_BONUS_EACH = 2
CONFIRM_BONUS_CAP = 10
MAX_TEXT_CHARS = 1000
MAX_IMAGE_MB = 8
MAX_IMAGE_SIDE = 1280
SUBMITS_PER_10_MIN = 5
CAMPUS_TZ = os.environ.get("CAMPUS_TZ", "").strip() or "Asia/Kolkata"

# Leaderboard points (overridable only in code, not env, to keep the board fair)
PTS_REPORT = 10
PTS_FULL_REPORT_BONUS = 3  # photo AND text
PTS_REPORTER_PER_CONFIRM = 1  # to reporter, max 5 per issue
PTS_REPORTER_CONFIRM_CAP = 5
PTS_CONFIRM_OTHERS = 2  # to the person who confirms
PTS_CONFIRM_DAILY_CAP = 20
PTS_RESOLVE_OTHERS = 15
PTS_RESOLVE_OWN = 5
PTS_OLD_ISSUE_PER_7_DAYS = 1  # extra on others' issues, max 5
PTS_OLD_ISSUE_CAP = 5
MIN_RESOLVE_NOTE_CHARS = 10
