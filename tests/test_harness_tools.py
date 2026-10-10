"""Tests for read-only CampusFix tools added to harness/tools.py."""

import json
from pathlib import Path
import pytest

from harness.tools import count_by_category, find_issues, list_top_issues


@pytest.fixture
def mock_issues_store(monkeypatch, tmp_path):
    store_file = tmp_path / "notes" / "issues.json"
    store_file.parent.mkdir(parents=True, exist_ok=True)
    sample_data = {
        "version": 1,
        "issues": [
            {
                "id": "111111",
                "title": "Sparking socket in lab",
                "summary": "Dangerous sparks observed",
                "category": "Electrical",
                "severity": "critical",
                "status": "open",
                "confirmations": 3,
                "location_hint": "Room 101",
            },
            {
                "id": "222222",
                "title": "Broken chair leg",
                "summary": "Chair wobbles in canteen",
                "category": "Mess & Food",
                "severity": "low",
                "status": "open",
                "confirmations": 0,
                "location_hint": "Cafeteria",
            },
            {
                "id": "333333",
                "title": "Leaking faucet",
                "summary": "Water dripping constantly",
                "category": "Water, Plumbing & Washrooms",
                "severity": "high",
                "status": "resolved",
                "confirmations": 2,
                "location_hint": "Washroom 2B",
            },
        ],
    }
    store_file.write_text(json.dumps(sample_data), encoding="utf-8")

    from harness import tools
    monkeypatch.setattr(tools, "ISSUES_FILE", store_file)
    return store_file


def test_list_top_issues(mock_issues_store):
    output = list_top_issues(2)
    assert "#111111" in output
    assert "CRITICAL" in output
    # Resolved issue should not be in open top issues
    assert "#333333" not in output


def test_count_by_category(mock_issues_store):
    output = count_by_category()
    assert "Electrical: 1" in output
    assert "Mess & Food: 1" in output


def test_find_issues(mock_issues_store):
    output = find_issues("canteen")
    assert "#222222" in output

    output_resolved = find_issues("dripping")
    assert "#333333" in output_resolved
    assert "RESOLVED" in output_resolved

    output_missing = find_issues("spaceship")
    assert "No campus issues found" in output_missing
