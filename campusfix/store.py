"""JSON-based persistent issue store with atomic writes, locking, and event logging."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from campusfix.config import MIN_RESOLVE_NOTE_CHARS, ROOT
from campusfix.leaderboard import clean_nickname, nick_key

DEFAULT_NOTES_DIR = ROOT / "notes"
DEFAULT_STORE_FILE = DEFAULT_NOTES_DIR / "issues.json"

_STORE_LOCK = threading.Lock()


def _now_iso():
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _get_store_path(custom_path=None):
    """Resolve the store file path and ensure parent directories exist."""
    path = Path(custom_path) if custom_path else DEFAULT_STORE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    images_dir = path.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return path


def _read_data(file_path):
    """Read the store data. Recover gracefully if corrupt."""
    if not file_path.exists():
        return {"version": 1, "issues": []}
    try:
        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            return {"version": 1, "issues": []}
        data = json.loads(content)
        if not isinstance(data, dict) or "issues" not in data:
            raise ValueError("Invalid format: root must be a dict with 'issues' list.")
        return data
    except Exception:
        # Corrupt file recovery
        stamp = int(time.time())
        corrupt_backup = file_path.parent / f"issues.corrupt.{stamp}.json"
        try:
            file_path.rename(corrupt_backup)
        except OSError:
            pass
        return {"version": 1, "issues": []}


def _write_data(file_path, data):
    """Atomically write data to store using a temp file."""
    tmp_path = file_path.parent / f"{file_path.name}.tmp"
    content = json.dumps(data, indent=2, ensure_ascii=False)
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, file_path)


def add_issue(fields, store_path=None):
    """Add a new issue with a unique 6-character hex ID and an initial created event."""
    target_path = _get_store_path(store_path)
    now_str = _now_iso()

    with _STORE_LOCK:
        data = _read_data(target_path)
        existing_ids = {i["id"] for i in data.get("issues", [])}

        # Generate unique 6-hex-char ID
        issue_id = secrets.token_hex(3)
        while issue_id in existing_ids:
            issue_id = secrets.token_hex(3)

        reporter = clean_nickname(fields.get("reporter", "anonymous"))

        issue = {
            "id": issue_id,
            "created_at": fields.get("created_at") or now_str,
            "reporter": reporter,
            "text": fields.get("text", "") or "",
            "image": fields.get("image", None),
            "title": fields.get("title", "Campus issue") or "Campus issue",
            "summary": fields.get("summary", "") or "",
            "category": fields.get("category", "Other") or "Other",
            "severity": fields.get("severity", "medium") or "medium",
            "safety_risk": bool(fields.get("safety_risk", False)),
            "location_hint": fields.get("location_hint", "unknown") or "unknown",
            "reason": fields.get("reason", "") or "",
            "needs_review": bool(fields.get("needs_review", False)),
            "triage_source": fields.get("triage_source", "gemma") or "gemma",
            "status": "open",
            "confirmations": 0,
            "log": [
                {
                    "at": now_str,
                    "event": "created",
                    "by": reporter,
                }
            ],
            "resolved_at": None,
            "resolved_by": None,
            "resolution_note": None,
        }

        data.setdefault("issues", []).append(issue)
        _write_data(target_path, data)
        return issue


def get_issue(issue_id, store_path=None):
    """Fetch an issue by its 6-character ID, or return None."""
    target_path = _get_store_path(store_path)
    with _STORE_LOCK:
        data = _read_data(target_path)
        for issue in data.get("issues", []):
            if issue.get("id") == issue_id:
                return dict(issue)
        return None


def list_issues(status=None, store_path=None):
    """List issues, optionally filtered by status ('open' or 'resolved')."""
    target_path = _get_store_path(store_path)
    with _STORE_LOCK:
        data = _read_data(target_path)
        issues = data.get("issues", [])
        if status:
            return [dict(i) for i in issues if i.get("status") == status]
        return [dict(i) for i in issues]


def add_me_too(issue_id, by, store_path=None):
    """Add a 'I face this too' (+1) confirmation with duplication and self-confirm guards."""
    target_path = _get_store_path(store_path)
    cleaned_by = clean_nickname(by)
    key_by = nick_key(cleaned_by)
    now_str = _now_iso()

    with _STORE_LOCK:
        data = _read_data(target_path)
        issue = None
        for item in data.get("issues", []):
            if item.get("id") == issue_id:
                issue = item
                break

        if not issue:
            return "Issue not found."

        # Reporter cannot confirm their own issue (unless anonymous)
        if key_by != "anonymous" and key_by == nick_key(issue.get("reporter")):
            return "You cannot confirm your own issue."

        # Check if this user already confirmed
        if key_by != "anonymous":
            for event in issue.get("log", []):
                if event.get("event") == "me_too" and nick_key(event.get("by")) == key_by:
                    return "You have already confirmed this issue."

        # Record confirmation
        issue["confirmations"] = int(issue.get("confirmations", 0)) + 1
        issue.setdefault("log", []).append({
            "at": now_str,
            "event": "me_too",
            "by": cleaned_by,
        })

        _write_data(target_path, data)
        return "Thanks for confirming! Your +1 has been recorded."


def resolve(issue_id, by, note, store_path=None):
    """Mark an issue resolved with a required nickname and minimum explanation note."""
    target_path = _get_store_path(store_path)
    cleaned_by = clean_nickname(by)
    if cleaned_by == "anonymous":
        raise ValueError("Please provide a nickname to resolve an issue.")

    clean_note = (note or "").strip()
    if len(clean_note) < MIN_RESOLVE_NOTE_CHARS:
        raise ValueError(f"Resolution note must be at least {MIN_RESOLVE_NOTE_CHARS} characters explaining what was fixed.")

    now_str = _now_iso()

    with _STORE_LOCK:
        data = _read_data(target_path)
        issue = None
        for item in data.get("issues", []):
            if item.get("id") == issue_id:
                issue = item
                break

        if not issue:
            raise ValueError(f"Issue {issue_id} not found.")

        issue["status"] = "resolved"
        issue["resolved_at"] = now_str
        issue["resolved_by"] = cleaned_by
        issue["resolution_note"] = clean_note
        issue.setdefault("log", []).append({
            "at": now_str,
            "event": "resolved",
            "by": cleaned_by,
            "note": clean_note,
        })

        _write_data(target_path, data)
        return dict(issue)


def reopen(issue_id, by, store_path=None):
    """Reopen a resolved issue, preserving original created_at and logging a reopened event."""
    target_path = _get_store_path(store_path)
    cleaned_by = clean_nickname(by)
    now_str = _now_iso()

    with _STORE_LOCK:
        data = _read_data(target_path)
        issue = None
        for item in data.get("issues", []):
            if item.get("id") == issue_id:
                issue = item
                break

        if not issue:
            raise ValueError(f"Issue {issue_id} not found.")

        issue["status"] = "open"
        issue["resolved_at"] = None
        issue["resolved_by"] = None
        issue["resolution_note"] = None
        issue.setdefault("log", []).append({
            "at": now_str,
            "event": "reopened",
            "by": cleaned_by,
        })

        _write_data(target_path, data)
        return dict(issue)


def confirm_review(issue_id, by, category=None, store_path=None):
    """Clear needs_review flag after manual review and optionally update category."""
    target_path = _get_store_path(store_path)
    cleaned_by = clean_nickname(by)
    now_str = _now_iso()

    with _STORE_LOCK:
        data = _read_data(target_path)
        issue = None
        for item in data.get("issues", []):
            if item.get("id") == issue_id:
                issue = item
                break

        if not issue:
            raise ValueError(f"Issue {issue_id} not found.")

        issue["needs_review"] = False
        if category:
            issue["category"] = category

        note_str = f"Category set to {issue['category']}" if category else "Approved report"
        issue.setdefault("log", []).append({
            "at": now_str,
            "event": "review_confirmed",
            "by": cleaned_by,
            "note": note_str,
        })

        _write_data(target_path, data)
        return dict(issue)
