"""CampusFix: Shared campus issue board and leaderboard powered by Gemma 4.

Matte Dark Civic Interface.
Palette: #0b0b0e (base), #151518 (surface), #1c1c21 (surfaceAlt), #24242a (border), #2563eb (blue).
Zero emojis, clean typography, system telemetry, and functional hierarchy.

Run from repo root:
    python campusfix/app.py
Or for LAN access:
    $env:CAMPUSFIX_LAN=1; python campusfix/app.py
"""

from datetime import datetime, timezone
import difflib
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gradio as gr

from campusfix.config import (
    CAMPUS_TZ,
    CATEGORIES,
    MAX_TEXT_CHARS,
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
    SUBMITS_PER_10_MIN,
)
from campusfix.images import save_clean_image
from campusfix.leaderboard import (
    campus_stats,
    clean_nickname,
    compute_leaderboard,
    nick_key,
    rank_of,
)
from campusfix.priority import _parse_utc, age_days, age_label, priority_score, rank_open
from campusfix.store import (
    add_issue,
    add_me_too,
    confirm_review,
    get_issue,
    list_issues,
    reopen,
    resolve,
)
from campusfix.triage import triage
from harness import Agent

# Application title
TITLE = "CampusFix"

# Load styles from static/css/styles.css
CSS_PATH = ROOT / "static" / "css" / "styles.css"
if CSS_PATH.exists():
    custom_css = CSS_PATH.read_text(encoding="utf-8")
else:
    custom_css = ""

# Session tracking for rate limiting (session_id -> list of submission timestamps)
_SESSION_SUBMITS = {}


def _check_rate_limit(session_id):
    """Enforce maximum SUBMITS_PER_10_MIN reports per session within 10 minutes."""
    now_ts = time.time()
    cutoff = now_ts - 600.0
    history = _SESSION_SUBMITS.get(session_id, [])
    recent = [t for t in history if t > cutoff]
    if len(recent) >= SUBMITS_PER_10_MIN:
        _SESSION_SUBMITS[session_id] = recent
        return False
    recent.append(now_ts)
    _SESSION_SUBMITS[session_id] = recent
    return True


def format_campus_time(iso_str):
    """Format an ISO UTC string into readable local CAMPUS_TZ time."""
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        try:
            tz = ZoneInfo(CAMPUS_TZ)
        except Exception:
            tz = timezone.utc
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(iso_str)[:16]




def render_stats_cards_html():
    """Render four compact statistical metric cards matching the KeyMetricsGrid."""
    all_issues = list_issues()
    total = len(all_issues)
    open_cnt = len([i for i in all_issues if i.get("status") == "open"])
    urgent_cnt = len([i for i in all_issues if i.get("status") == "open" and i.get("severity") in ("high", "critical")])
    resolved_cnt = len([i for i in all_issues if i.get("status") == "resolved"])

    return f'''
    <section aria-label="System Metrics" style="margin-bottom: 20px; width: 100%; box-sizing: border-box;">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; padding: 0 2px;">
            <h3 style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #8e8e99; margin: 0;">System Telemetry</h3>
            <span style="font-size: 10px; color: #71717a; font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace;">Live Sync</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; width: 100%; box-sizing: border-box;">
            <article style="background: #151518; border: 1px solid #24242a; padding: 14px 16px; display: flex; flex-direction: column; justify-content: space-between;">
                <div style="display: flex; align-items: center; justify-content: space-between; color: #8e8e99; margin-bottom: 8px;">
                    <span style="font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Total Reports</span>
                    <span style="width: 6px; height: 6px; border-radius: 50%; background: #71717a;"></span>
                </div>
                <div style="font-size: 1.75rem; font-weight: 700; font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; color: #ffffff; letter-spacing: -0.02em; line-height: 1; margin-bottom: 4px;">{total}</div>
                <span style="font-size: 11px; color: #8e8e99;">Logged in system</span>
            </article>
            <article style="background: #151518; border: 1px solid #24242a; padding: 14px 16px; display: flex; flex-direction: column; justify-content: space-between;">
                <div style="display: flex; align-items: center; justify-content: space-between; color: #8e8e99; margin-bottom: 8px;">
                    <span style="font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Open Issues</span>
                    <span style="width: 6px; height: 6px; border-radius: 50%; background: #2563eb;"></span>
                </div>
                <div style="font-size: 1.75rem; font-weight: 700; font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; color: #60a5fa; letter-spacing: -0.02em; line-height: 1; margin-bottom: 4px;">{open_cnt}</div>
                <span style="font-size: 11px; color: #8e8e99;">Awaiting resolution</span>
            </article>
            <article style="background: #151518; border: 1px solid #24242a; padding: 14px 16px; display: flex; flex-direction: column; justify-content: space-between;">
                <div style="display: flex; align-items: center; justify-content: space-between; color: #8e8e99; margin-bottom: 8px;">
                    <span style="font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">High Priority</span>
                    <span style="width: 6px; height: 6px; border-radius: 50%; background: #ef4444;"></span>
                </div>
                <div style="font-size: 1.75rem; font-weight: 700; font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; color: #f87171; letter-spacing: -0.02em; line-height: 1; margin-bottom: 4px;">{urgent_cnt}</div>
                <span style="font-size: 11px; color: #8e8e99;">High &amp; Critical</span>
            </article>
            <article style="background: #151518; border: 1px solid #24242a; padding: 14px 16px; display: flex; flex-direction: column; justify-content: space-between;">
                <div style="display: flex; align-items: center; justify-content: space-between; color: #8e8e99; margin-bottom: 8px;">
                    <span style="font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;">Resolved</span>
                    <span style="width: 6px; height: 6px; border-radius: 50%; background: #10b981;"></span>
                </div>
                <div style="font-size: 1.75rem; font-weight: 700; font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; color: #34d399; letter-spacing: -0.02em; line-height: 1; margin-bottom: 4px;">{resolved_cnt}</div>
                <span style="font-size: 11px; color: #8e8e99;">Verified fixed</span>
            </article>
        </div>
    </section>
    '''


def get_recent_issues_table():
    """Load the 5 most recent issues for the overview dashboard."""
    all_issues = list_issues()
    all_issues.sort(key=lambda x: _parse_utc(x.get("created_at")).timestamp(), reverse=True)
    rows = []
    for itm in all_issues[:5]:
        status_label = "OPEN" if itm.get("status") == "open" else "RESOLVED"
        rows.append([
            itm.get("id"),
            itm.get("severity", "").upper(),
            status_label,
            itm.get("category"),
            itm.get("title"),
            itm.get("location_hint") or "unknown",
            format_campus_time(itm.get("created_at")),
            f"+{itm.get('confirmations', 0)}",
        ])
    return rows


def get_points_rules_md():
    """Generate the points rules card directly from constants without emojis."""
    return f"""### How to Earn Points
- **Report an issue:** **{PTS_REPORT}** pts (+**{PTS_FULL_REPORT_BONUS}** bonus if report includes both photo & text)
- **Confirm an issue ("I face this too"):** **{PTS_CONFIRM_OTHERS}** pts (daily cap: **{PTS_CONFIRM_DAILY_CAP}** pts)
- **Bonus when someone confirms your report:** **{PTS_REPORTER_PER_CONFIRM}** pt per confirmation (max **{PTS_REPORTER_CONFIRM_CAP}** pts per issue)
- **Resolve someone else's issue:** **{PTS_RESOLVE_OTHERS}** pts (+**{PTS_OLD_ISSUE_PER_7_DAYS}** pt per 7 days the issue was open, up to +**{PTS_OLD_ISSUE_CAP}** pts)
- **Resolve your own issue:** **{PTS_RESOLVE_OWN}** pts (no aging bonus)

*Note: Anonymous actions earn 0 points. Reports marked for review earn points only once approved. If an issue is reopened, the resolve points are cancelled.*
"""


# ---------------- TAB 1: OVERVIEW & TAB 2: ISSUES ----------------

def filter_board_data(search_query, status_filter, category_filter, severity_filter):
    """Filter, score, and rank issues based on user controls."""
    now_utc = datetime.now(timezone.utc)
    all_issues = list_issues()

    # 1. Filter by status
    if status_filter == "Open":
        filtered = [i for i in all_issues if i.get("status") == "open" and not i.get("needs_review")]
    elif status_filter == "Resolved (Archive)":
        filtered = [i for i in all_issues if i.get("status") == "resolved"]
    else:  # "All"
        filtered = all_issues

    # 2. Filter by category
    if category_filter and category_filter != "All":
        clean_cat = category_filter.split(" (")[0].strip()
        filtered = [i for i in filtered if i.get("category") == clean_cat]

    # 3. Filter by severity
    if severity_filter and severity_filter != "All":
        filtered = [i for i in filtered if i.get("severity", "").lower() == severity_filter.lower()]

    # 4. Search query
    if search_query and search_query.strip():
        q = search_query.strip().lower()
        filtered = [
            i for i in filtered
            if q in f"{i.get('title', '')} {i.get('summary', '')} {i.get('text', '')} {i.get('location_hint', '')} {i.get('id', '')}".lower()
        ]

    # 5. Build presentation rows
    rows = []
    for item in filtered:
        days = age_days(item, now_utc)
        score = priority_score(item, now_utc) if item.get("status") == "open" else 0.0
        label = age_label(days) if item.get("status") == "open" else "Resolved"
        rows.append({
            "score": score,
            "id": item.get("id"),
            "severity": item.get("severity", "medium").upper(),
            "status": "OPEN" if item.get("status") == "open" else "RESOLVED",
            "category": item.get("category"),
            "title": item.get("title"),
            "location": item.get("location_hint") or "unknown",
            "reported": format_campus_time(item.get("created_at")),
            "age": f"{days:.1f}d",
            "status_label": label,
            "confirmations": f"+{item.get('confirmations', 0)}",
            "created_ts": _parse_utc(item.get("created_at")).timestamp(),
        })

    # Sort open items by score desc then created_at asc; resolved by date desc
    rows.sort(key=lambda r: (-r["score"], r["created_ts"]))

    table_data = []
    for r in rows:
        table_data.append([
            f"{r['score']:.1f}" if r["status"] == "OPEN" else "-",
            r["id"],
            r["severity"],
            r["status"],
            r["category"],
            r["title"],
            r["location"],
            r["reported"],
            r["age"],
            r["confirmations"],
        ])

    return table_data


def get_category_choices():
    """Return category dropdown choices with current open issue counts."""
    open_issues = list_issues(status="open")
    counts = {}
    for i in open_issues:
        c = i.get("category", "Other")
        counts[c] = counts.get(c, 0) + 1

    choices = ["All"]
    for cat in CATEGORIES:
        cnt = counts.get(cat, 0)
        choices.append(f"{cat} ({cnt})")
    return choices


def get_review_choices():
    """List IDs of issues waiting for moderator review."""
    review_issues = [i for i in list_issues() if i.get("needs_review")]
    return [f"{i['id']} - {i['title']}" for i in review_issues]


def view_issue_details(issue_id):
    """Render details, photo, and event log for a selected issue."""
    if not issue_id or not str(issue_id).strip():
        return "Select an issue ID from the table to inspect details.", None, []

    clean_id = str(issue_id).split()[0].strip()
    issue = get_issue(clean_id)
    if not issue:
        return f"Issue `{clean_id}` was not found.", None, []

    status_tag = "OPEN" if issue.get("status") == "open" else "RESOLVED"
    sev_badge = issue.get("severity", "medium").upper()

    img_path = ROOT / issue["image"] if issue.get("image") else None
    if img_path and not img_path.exists():
        img_path = None

    details_md = f"""### {status_tag} | #{issue['id']} — {issue['title']}

- **Category:** `{issue['category']}`
- **Severity:** `{sev_badge}` | **Safety Risk:** `{'Yes' if issue.get('safety_risk') else 'No'}`
- **Reported By:** `{issue['reporter']}` | **Reported Time:** {format_campus_time(issue['created_at'])}
- **Location:** {issue['location_hint']}
- **Confirmations (+1):** {issue.get('confirmations', 0)}
- **Summary:** {issue['summary']}
- **AI Triage Reasoning:** *{issue['reason']}*
"""
    if issue.get("status") == "resolved":
        details_md += f"""
---
#### Resolution Details:
- **Resolved By:** `{issue.get('resolved_by')}` at {format_campus_time(issue.get('resolved_at'))}
- **Resolution Note:** {issue.get('resolution_note')}
"""

    log_rows = []
    for ev in issue.get("log", []):
        log_rows.append([
            format_campus_time(ev.get("at")),
            ev.get("event"),
            ev.get("by"),
            ev.get("note", "-"),
        ])

    return details_md, str(img_path) if img_path else None, log_rows


def handle_add_me_too(issue_id, nickname):
    """Handle '+1' confirmation button."""
    if not issue_id:
        return "Please select an issue ID first."
    clean_id = str(issue_id).split()[0].strip()
    try:
        msg = add_me_too(clean_id, by=nickname)
        return msg
    except Exception as e:
        return f"Error: {e}"


def handle_resolve(issue_id, nickname, note):
    """Handle 'Mark resolved' button."""
    if not issue_id:
        return "Please select an issue ID first."
    clean_id = str(issue_id).split()[0].strip()
    try:
        resolve(clean_id, by=nickname, note=note)
        return f"Issue #{clean_id} successfully marked as resolved."
    except ValueError as ve:
        return f"Validation error: {ve}"
    except Exception as e:
        return f"Error: {e}"


def handle_reopen(issue_id, nickname):
    """Handle 'Reopen' button."""
    if not issue_id:
        return "Please select an issue ID first."
    clean_id = str(issue_id).split()[0].strip()
    try:
        reopen(clean_id, by=nickname)
        return f"Issue #{clean_id} reopened and restored to active board."
    except Exception as e:
        return f"Error: {e}"


def handle_confirm_review(review_selection, new_category, nickname):
    """Approve or recategorize a flagged issue."""
    if not review_selection:
        return "Please choose an issue from the review queue."
    clean_id = str(review_selection).split()[0].strip()
    cat = new_category if (new_category and new_category != "Keep Current") else None
    try:
        confirm_review(clean_id, by=nickname, category=cat)
        return f"Issue #{clean_id} approved and restored to active board."
    except Exception as e:
        return f"Error: {e}"


# ---------------- TAB 3: REPORT A PROBLEM ----------------

def submit_report(image_path, text_input, nickname_input, session_token):
    """Process a new issue report: validation, cleaning, Gemma triage, duplicate check, and save."""
    clean_text = (text_input or "").strip()
    has_image = bool(image_path)

    if not has_image and not clean_text:
        return "Please add a photo or describe the problem.", ""

    if len(clean_text) > MAX_TEXT_CHARS:
        return f"Text description exceeds {MAX_TEXT_CHARS} characters limit.", ""

    # Check session rate limit
    sess_key = session_token or "default_user"
    if not _check_rate_limit(sess_key):
        return f"Rate limit reached: maximum {SUBMITS_PER_10_MIN} reports per 10 minutes. Please wait before submitting again.", ""

    try:
        clean_user = clean_nickname(nickname_input)
    except ValueError as e:
        return f"Nickname error: {e}", ""

    # Step 1: Temporary ID for image storage
    temp_id = f"img_{int(time.time() * 1000) % 1000000:06d}"
    saved_img_rel = None
    if has_image:
        try:
            saved_img_rel = save_clean_image(image_path, temp_id)
        except ValueError as err:
            return f"Image upload rejected: {err}", ""

    # Step 2: Exactly one Gemma triage call
    triage_result = triage(clean_text, image_path=image_path)

    # Step 3: Check possible duplicates among open issues in same category
    all_open = list_issues(status="open")
    dup_hints = []
    category = triage_result.get("category", "Other")
    for existing in all_open:
        if existing.get("category") == category:
            ex_text = existing.get("text", "") or existing.get("title", "")
            if clean_text and ex_text:
                sim = difflib.SequenceMatcher(None, clean_text.lower(), ex_text.lower()).ratio()
                if sim > 0.55:
                    dup_hints.append(f"#{existing['id']} '{existing['title']}' ({int(sim*100)}% match)")

    # Step 4: Save to persistent store
    fields = {
        "reporter": clean_user,
        "text": clean_text,
        "image": saved_img_rel,
        "title": triage_result.get("title", "Campus issue"),
        "summary": triage_result.get("summary", ""),
        "category": triage_result.get("category", "Other"),
        "severity": triage_result.get("severity", "medium"),
        "safety_risk": triage_result.get("safety_risk", False),
        "location_hint": triage_result.get("location_hint", "unknown"),
        "reason": triage_result.get("reason", ""),
        "needs_review": triage_result.get("needs_review", False),
        "triage_source": triage_result.get("triage_source", "gemma"),
    }
    created = add_issue(fields)

    # If image was saved with temp_id, rename to real 6-char issue ID
    if saved_img_rel:
        real_img_rel = f"notes/images/{created['id']}.jpg"
        old_path = ROOT / saved_img_rel
        new_path = ROOT / real_img_rel
        try:
            if old_path.exists():
                os.replace(old_path, new_path)
                created["image"] = real_img_rel
                from campusfix.store import _get_store_path, _read_data, _write_data
                p = _get_store_path()
                d = _read_data(p)
                for itm in d.get("issues", []):
                    if itm["id"] == created["id"]:
                        itm["image"] = real_img_rel
                _write_data(p, d)
        except OSError:
            pass

    # Points calculation
    points_earned = 0
    if clean_user != "anonymous" and not created["needs_review"]:
        points_earned = PTS_REPORT
        if has_image and clean_text:
            points_earned += PTS_FULL_REPORT_BONUS

    pts_notice = f"**Points awarded:** {points_earned} points." if points_earned > 0 else (
        "*(No points awarded: anonymous report or needs moderation)*"
    )

    review_notice = ""
    if created["needs_review"]:
        review_notice = (
            "\n\n> **Needs Moderation:** This report was flagged for review "
            "(it may be ambiguous or uncertain). It is queued in 'Needs a Look'."
        )

    dup_notice = ""
    if dup_hints:
        dup_notice = "\n\n**Similar existing issues:**\n- " + "\n- ".join(dup_hints) + "\n*(Consider confirming an existing issue instead of creating duplicate tickets.)*"

    result_md = f"""### Issue Logged Successfully

- **Issue ID:** `{created['id']}`
- **Title:** **{created['title']}**
- **Category:** `{created['category']}`
- **Severity:** `{created['severity'].upper()}`
- **Location:** {created['location_hint']}
- **Summary:** {created['summary']}
- **AI Triage Reasoning:** *{created['reason']}*

{pts_notice}{review_notice}{dup_notice}
"""
    return result_md, ""


# ---------------- TAB 4: LEADERBOARD ----------------

def load_leaderboard_view(time_window, nickname):
    """Compute leaderboard rankings and campus statistics without emojis."""
    now_utc = datetime.now(timezone.utc)
    all_issues = list_issues()
    window_days = 7 if time_window == "Last 7 days" else None

    rows = compute_leaderboard(all_issues, now_utc, window_days=window_days)

    table_data = []
    for r in rows[:15]:
        table_data.append([
            f"#{r['rank']}",
            r["name"],
            r["points"],
            r["reports"],
            r["resolves"],
            r["badges"],
        ])

    # Rank of current user
    user_rank = rank_of(rows, nickname)
    if user_rank is not None:
        user_row = next(r for r in rows if r["rank"] == user_rank)
        rank_md = f"**Your standing:** You are **Rank #{user_rank}** with **{user_row['points']} pts** ({user_row['badges']})."
    elif nickname and clean_nickname(nickname) != "anonymous":
        rank_md = f"Hello **{clean_nickname(nickname)}**! You currently have 0 points. Submit a report or resolve an issue to appear on the leaderboard."
    else:
        rank_md = "*Enter your nickname in the header above to check your personal rank.*"

    # Campus stats summary
    stats = campus_stats(all_issues, now_utc)
    oldest_txt = f"#{stats['oldest_open']['id']} ({stats['oldest_age_days']} days old)" if stats['oldest_open'] else "None"
    stats_md = f"""| Open Issues | Resolved This Week | Oldest Open Issue | Average Issue Age |
| :---: | :---: | :---: | :---: |
| **{stats['open_count']}** | **{stats['resolved_this_week']}** | **{oldest_txt}** | **{stats['average_age_days']} days** |
"""
    return table_data, rank_md, stats_md


# ---------------- STRETCH FEATURE: ASK THE BOARD ----------------

def ask_the_board(query):
    """Use the model harness with read-only tools to answer campus questions."""
    if not query or not str(query).strip():
        return "", "Please enter a question to ask the board."
    rule = (
        "You are the CampusFix assistant. You help students and staff find campus issues using "
        "the available tools: list_top_issues, count_by_category, and find_issues. "
        "Keep answers concise, direct, and factual."
    )
    agent = Agent(system=rule, max_steps=3)
    try:
        answer = agent.run(str(query).strip())
    except Exception as err:
        import gemma_client
        return "", gemma_client.describe_error(err)
    steps = "\n".join(f"{i}. {s['text']}" for i, s in enumerate(agent.steps[:-1], 1))
    return steps or "(No tools needed)", answer


# ---------------- PRODUCTION GRADIO INTERFACE ----------------

custom_theme = gr.themes.Default(primary_hue="blue", neutral_hue="zinc")

with gr.Blocks(title=TITLE) as demo:
    session_id_state = gr.State(lambda: f"sess_{int(time.time()*1000)}")

    # Top Brand & Global Identity Bar
    with gr.Row(elem_classes=["cf-card"]):
        with gr.Column(scale=3):
            gr.HTML(
                '''
                <div style="display: flex; flex-direction: column; gap: 4px;">
                    <div style="display: flex; items-center; gap: 8px; align-items: center;">
                        <span style="width: 10px; height: 10px; background: #2563eb; display: inline-block;"></span>
                        <h1 style="font-weight: 700; font-size: 1.25rem; color: #ffffff; letter-spacing: -0.02em; margin: 0; line-height: 1;">CampusFix</h1>
                        <span style="font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; font-size: 10px; letter-spacing: 0.04em; color: #a1a1aa; background: #0b0b0e; border: 1px solid #24242a; padding: 2px 6px; border-radius: 2px;">
                            gemma-4-26b-a4b-it
                        </span>
                    </div>
                    <p style="font-size: 11px; color: #8e8e99; margin: 2px 0 0 0;">
                        Civic Infrastructure &amp; Maintenance • Single-call AI Triage
                    </p>
                </div>
                '''
            )
        with gr.Column(scale=2):
            nickname_input = gr.Textbox(
                label="Your Public Nickname (ID)",
                placeholder="e.g. campus_fixer",
                value="",
            )

    # Main Navigation Tabs
    with gr.Tabs() as tabs:

        # ---------------- 1. OVERVIEW DASHBOARD ----------------
        with gr.Tab("Overview", id="tab_overview"):
            with gr.Row():
                nav_report_btn = gr.Button("Report an Issue", variant="primary", scale=1)
                nav_browse_btn = gr.Button("Browse All Issues", variant="secondary", scale=1)

            stats_view = gr.HTML(value=render_stats_cards_html)

            gr.Markdown("### Recently Logged Campus Issues")
            recent_table = gr.Dataframe(
                headers=["ID", "Severity", "Status", "Category", "Title", "Location", f"Reported ({CAMPUS_TZ})", "+1s"],
                value=get_recent_issues_table,
                interactive=False,
            )

            with gr.Row():
                refresh_overview_btn = gr.Button("Refresh Overview", variant="secondary")

            def refresh_overview_action():
                return render_stats_cards_html(), get_recent_issues_table()

            refresh_overview_btn.click(
                refresh_overview_action,
                outputs=[stats_view, recent_table],
            )

            nav_report_btn.click(
                lambda: gr.Tabs(selected="tab_report"),
                outputs=[tabs],
            )
            nav_browse_btn.click(
                lambda: gr.Tabs(selected="tab_issues"),
                outputs=[tabs],
            )

        # ---------------- 2. BROWSE CAMPUS ISSUES ----------------
        with gr.Tab("Browse Issues", id="tab_issues"):
            with gr.Row():
                search_box = gr.Textbox(
                    label="Search Issues",
                    placeholder="Search by title, location, or keyword...",
                    scale=2,
                )
                status_dropdown = gr.Dropdown(
                    label="Status",
                    choices=["Open", "All", "Resolved (Archive)"],
                    value="Open",
                    scale=1,
                )
                category_dropdown = gr.Dropdown(
                    label="Category",
                    choices=get_category_choices(),
                    value="All",
                    scale=1,
                )
                severity_dropdown = gr.Dropdown(
                    label="Severity",
                    choices=["All", "Critical", "High", "Medium", "Low"],
                    value="All",
                    scale=1,
                )
                board_refresh_btn = gr.Button("Filter / Refresh", variant="secondary", scale=1)

            board_table = gr.Dataframe(
                headers=["Score", "ID", "Severity", "Status", "Category", "Title", "Location", f"Reported ({CAMPUS_TZ})", "Age", "+1s"],
                datatype=["str", "str", "str", "str", "str", "str", "str", "str", "str", "str"],
                interactive=False,
            )

            gr.Markdown("---")
            gr.Markdown("### Issue Details & Actions")

            with gr.Row():
                with gr.Column(scale=1):
                    selected_issue_input = gr.Textbox(
                        label="Issue ID to Inspect",
                        placeholder="Enter or paste 6-hex ID (e.g. b55cc4)",
                    )
                    inspect_btn = gr.Button("Inspect Issue", variant="secondary")
                    action_feedback = gr.Markdown("")

                    me_too_btn = gr.Button("I Face This Too (+1)", variant="secondary")

                    resolve_note_input = gr.Textbox(
                        label="Resolution Note",
                        placeholder="Explain what was repaired (min 10 characters required)...",
                        lines=2,
                    )
                    resolve_btn = gr.Button("Mark Resolved", variant="primary")
                    reopen_btn = gr.Button("Reopen Issue", variant="secondary")

                with gr.Column(scale=2):
                    issue_details_md = gr.Markdown("Select an issue ID on the left to view full details.")
                    issue_photo = gr.Image(label="Attached Photo", visible=True)
                    log_table = gr.Dataframe(
                        headers=["Timestamp", "Action", "By", "Note"],
                        interactive=False,
                        label="Issue Audit Log",
                    )

            # Moderation Review Accordion
            with gr.Accordion("Moderator Review Queue (Needs a Look)", open=False):
                with gr.Row():
                    review_select = gr.Dropdown(
                        label="Flagged Issues",
                        choices=get_review_choices(),
                        interactive=True,
                    )
                    review_cat = gr.Dropdown(
                        label="Assign Category",
                        choices=["Keep Current"] + CATEGORIES,
                        value="Keep Current",
                    )
                    confirm_review_btn = gr.Button("Approve / Recategorize", variant="primary")
                review_feedback = gr.Markdown("")

            # Stretch Agent Tool Accordion
            with gr.Accordion("Ask the Board (AI Agent with Tools)", open=False):
                gr.Markdown("*(Note: Executes an agent loop using read-only tools: list_top_issues, count_by_category, find_issues)*")
                with gr.Row():
                    ask_query = gr.Textbox(
                        label="Query",
                        placeholder="e.g. Which issues are critical? How many issues in Electrical?",
                        scale=3,
                    )
                    ask_btn = gr.Button("Ask the Board", variant="primary", scale=1)
                agent_steps_txt = gr.Textbox(label="Agent Tool Steps", lines=3)
                agent_answer_txt = gr.Textbox(label="Agent Answer", lines=3)

                ask_btn.click(
                    ask_the_board,
                    inputs=[ask_query],
                    outputs=[agent_steps_txt, agent_answer_txt],
                )

            # Board Interaction Event Bindings
            def update_board_view(search, status, cat, sev):
                return filter_board_data(search, status, cat, sev)

            for trigger in [board_refresh_btn.click, search_box.submit, status_dropdown.change, category_dropdown.change, severity_dropdown.change]:
                trigger(
                    update_board_view,
                    inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
                    outputs=[board_table],
                )

            inspect_btn.click(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            )

            me_too_btn.click(
                handle_add_me_too,
                inputs=[selected_issue_input, nickname_input],
                outputs=[action_feedback],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
                outputs=[board_table],
            )

            resolve_btn.click(
                handle_resolve,
                inputs=[selected_issue_input, nickname_input, resolve_note_input],
                outputs=[action_feedback],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
                outputs=[board_table],
            )

            reopen_btn.click(
                handle_reopen,
                inputs=[selected_issue_input, nickname_input],
                outputs=[action_feedback],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
                outputs=[board_table],
            )

            confirm_review_btn.click(
                handle_confirm_review,
                inputs=[review_select, review_cat, nickname_input],
                outputs=[review_feedback],
            ).then(
                update_board_view,
                inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
                outputs=[board_table],
            )

        # ---------------- 3. REPORT AN ISSUE ----------------
        with gr.Tab("Report an Issue", id="tab_report"):
            gr.Markdown(
                "### Report a Campus Facility Problem  \n"
                "<span style='font-size:0.85rem; color:#8e8e99;'>"
                "Submit a photo, description, or both. Gemma 4 will automatically categorize and assess severity."
                "</span>"
            )

            with gr.Row():
                with gr.Column(scale=1):
                    report_img_input = gr.Image(type="filepath", label="Upload Photo (optional)")
                    report_txt_input = gr.Textbox(
                        label="Problem Description (optional, max 1000 characters)",
                        placeholder="Describe the issue, location, or physical damage...",
                        lines=4,
                        max_lines=8,
                    )
                    report_submit_btn = gr.Button("Submit Report to Gemma 4", variant="primary")
                with gr.Column(scale=1):
                    report_result_card = gr.Markdown(
                        "Submit a photo or description to initiate Gemma 4 triage."
                    )

            report_submit_btn.click(
                submit_report,
                inputs=[report_img_input, report_txt_input, nickname_input, session_id_state],
                outputs=[report_result_card, report_txt_input],
            )

        # ---------------- 4. LEADERBOARD ----------------
        with gr.Tab("Leaderboard", id="tab_leaderboard"):
            with gr.Row():
                time_radio = gr.Radio(
                    label="Leaderboard Window",
                    choices=["All time", "Last 7 days"],
                    value="All time",
                )
                refresh_leaderboard_btn = gr.Button("Refresh Rankings", variant="secondary")

            user_rank_card = gr.Markdown("Enter your nickname in the header above to see your personal rank.")
            stats_strip = gr.Markdown("")

            leaderboard_table = gr.Dataframe(
                headers=["Rank", "Participant", "Points", "Reports", "Resolves", "Badges"],
                datatype=["str", "str", "number", "number", "number", "str"],
                interactive=False,
            )

            gr.Markdown(get_points_rules_md())
            gr.Markdown(
                "> **Fairness & Privacy Notice:** Nicknames are self-assigned and unauthenticated. "
                "The leaderboard is designed for friendly community motivation rather than high-stakes prizes."
            )

            def update_leaderboard_action(window, nick):
                return load_leaderboard_view(window, nick)

            refresh_leaderboard_btn.click(
                update_leaderboard_action,
                inputs=[time_radio, nickname_input],
                outputs=[leaderboard_table, user_rank_card, stats_strip],
            )
            time_radio.change(
                update_leaderboard_action,
                inputs=[time_radio, nickname_input],
                outputs=[leaderboard_table, user_rank_card, stats_strip],
            )

    # Initial data load
    demo.load(
        update_board_view,
        inputs=[search_box, status_dropdown, category_dropdown, severity_dropdown],
        outputs=[board_table],
    ).then(
        update_leaderboard_action,
        inputs=[time_radio, nickname_input],
        outputs=[leaderboard_table, user_rank_card, stats_strip],
    )

if __name__ == "__main__":
    is_lan = os.environ.get("CAMPUSFIX_LAN") == "1"
    server = "0.0.0.0" if is_lan else "127.0.0.1"
    print(f"Starting CampusFix on {server}:7860 (LAN access: {is_lan})...")
    allowed = [str(ROOT / "assets"), str(ROOT / "static"), str(ROOT / "notes")]
    dark_js = "() => document.documentElement.classList.add('dark')"
    if is_lan:
        demo.launch(server_name="0.0.0.0", share=False, css=custom_css, theme=custom_theme, js=dark_js, allowed_paths=allowed)
    else:
        demo.launch(share=False, css=custom_css, theme=custom_theme, js=dark_js, allowed_paths=allowed)
