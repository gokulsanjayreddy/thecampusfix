"""CampusFix: Shared campus issue board and leaderboard powered by Gemma 4.

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

from harness import Agent

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

# CHANGE THIS: title shown at the top of the page.
TITLE = "CampusFix"

# In-memory session tracking for rate limiting (session_id -> list of submission timestamps)
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


def get_points_rules_md():
    """Generate the points rules card directly from constants."""
    return f"""### 🎯 How to Earn Points
- **Report an issue:** **{PTS_REPORT}** pts (+**{PTS_FULL_REPORT_BONUS}** bonus if report includes both photo & text)
- **Confirm an issue ("I face this too"):** **{PTS_CONFIRM_OTHERS}** pts (daily cap: **{PTS_CONFIRM_DAILY_CAP}** pts)
- **Bonus when someone confirms your report:** **{PTS_REPORTER_PER_CONFIRM}** pt per confirmation (max **{PTS_REPORTER_CONFIRM_CAP}** pts per issue)
- **Resolve someone else's issue:** **{PTS_RESOLVE_OTHERS}** pts (+**{PTS_OLD_ISSUE_PER_7_DAYS}** pt per 7 days the issue was open, up to +**{PTS_OLD_ISSUE_CAP}** pts)
- **Resolve your own issue:** **{PTS_RESOLVE_OWN}** pts (no aging bonus)

*Note: Anonymous actions earn 0 points. Reports marked for review earn points only once approved. If an issue is reopened, the resolve points are cancelled.*
"""


# ---------------- TAB 1: REPORT A PROBLEM ----------------

def submit_report(image_path, text_input, nickname_input, session_token):
    """Process a new issue report: validation, cleaning, Gemma triage, duplicate check, and save."""
    clean_text = (text_input or "").strip()
    has_image = bool(image_path)

    if not has_image and not clean_text:
        return "⚠️ Please add a photo or describe the problem.", None

    if len(clean_text) > MAX_TEXT_CHARS:
        return f"⚠️ Text description exceeds {MAX_TEXT_CHARS} characters limit.", None

    # Check session rate limit
    sess_key = session_token or "default_user"
    if not _check_rate_limit(sess_key):
        return f"⚠️ Rate limit reached: maximum {SUBMITS_PER_10_MIN} reports per 10 minutes. Please wait before submitting again.", None

    try:
        clean_user = clean_nickname(nickname_input)
    except ValueError as e:
        return f"⚠️ Nickname error: {e}", None

    # Step 1: Temporary ID for image storage
    temp_id = f"img_{int(time.time() * 1000) % 1000000:06d}"
    saved_img_rel = None
    if has_image:
        try:
            saved_img_rel = save_clean_image(image_path, temp_id)
        except ValueError as err:
            return f"⚠️ Image upload rejected: {err}", None

    # Step 2: One Gemma triage call
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
                # Update store with real image path
                all_issues = list_issues()
                for itm in all_issues:
                    if itm["id"] == created["id"]:
                        itm["image"] = real_img_rel
                from campusfix.store import _get_store_path, _read_data, _write_data
                p = _get_store_path()
                d = _read_data(p)
                for itm in d.get("issues", []):
                    if itm["id"] == created["id"]:
                        itm["image"] = real_img_rel
                _write_data(p, d)
        except OSError:
            pass

    # Points earned calculation
    points_earned = 0
    if clean_user != "anonymous" and not created["needs_review"]:
        points_earned = PTS_REPORT
        if has_image and clean_text:
            points_earned += PTS_FULL_REPORT_BONUS

    # Build result card
    sev = created["severity"].upper()
    sev_color = {
        "CRITICAL": "#d9383a",
        "HIGH": "#e67e22",
        "MEDIUM": "#f39c12",
        "LOW": "#27ae60",
    }.get(sev, "#888")

    pts_notice = f"⭐ **You earned {points_earned} points!**" if points_earned > 0 else (
        "*(No points awarded: anonymous report or needs moderation)*"
    )

    review_notice = ""
    if created["needs_review"]:
        review_notice = (
            "\n\n> 🔍 **Needs a look:** This report was flagged for moderator verification "
            "(it may be off-topic or uncertain). It appears in the 'Needs a look' review queue."
        )

    dup_notice = ""
    if dup_hints:
        dup_notice = "\n\n💡 **Possible existing issues:**\n- " + "\n- ".join(dup_hints) + "\n*(Consider confirming an existing issue instead of creating duplicate reports!)*"

    result_md = f"""### ✅ Issue Reported Successfully!

**Issue ID:** `{created['id']}`  
**Title:** **{created['title']}**  
**Category:** `{created['category']}`  
**Severity:** <span style="background:{sev_color}; color:#fff; padding:2px 8px; border-radius:4px; font-weight:bold;">{sev}</span>  
**Location:** {created['location_hint']}  
**Summary:** {created['summary']}  
**Reasoning:** {created['reason']}  

{pts_notice}{review_notice}{dup_notice}
"""
    return result_md, ""


# ---------------- TAB 2: BOARD ----------------

def load_board_data(category_filter, show_archive):
    """Load ranked active issues or resolved archive table."""
    now_utc = datetime.now(timezone.utc)
    all_issues = list_issues()

    if show_archive:
        # Show resolved archive
        resolved_list = [i for i in all_issues if i.get("status") == "resolved"]
        if category_filter and category_filter != "All":
            resolved_list = [i for i in resolved_list if i.get("category") == category_filter]

        rows = []
        for i in resolved_list:
            rows.append([
                i.get("id"),
                i.get("category"),
                i.get("severity", "").upper(),
                i.get("title"),
                i.get("location_hint"),
                format_campus_time(i.get("created_at")),
                format_campus_time(i.get("resolved_at")),
                i.get("resolved_by", "-"),
                i.get("resolution_note", "-"),
            ])
        headers = ["ID", "Category", "Severity", "Title", "Location", "Reported", "Resolved", "Resolved By", "Note"]
        return rows, headers

    # Active open issues
    ranked = rank_open(all_issues, now_utc)
    if category_filter and category_filter != "All":
        ranked = [i for i in ranked if i.get("category") == category_filter]

    rows = []
    for r in ranked:
        rows.append([
            r.get("score"),
            r.get("id"),
            r.get("category"),
            r.get("severity", "").upper(),
            r.get("title"),
            r.get("location_hint"),
            format_campus_time(r.get("created_at")),
            f"{r.get('age_days', 0):.1f}d",
            r.get("age_label") or "-",
            f"+{r.get('confirmations', 0)}",
        ])
    headers = ["Score", "ID", "Category", "Severity", "Title", "Location", f"Reported ({CAMPUS_TZ})", "Age", "Status", "+1s"]
    return rows, headers


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


def get_issue_choices():
    """List IDs of all issues for selection dropdown."""
    issues = list_issues()
    return [i["id"] for i in issues]


def get_review_choices():
    """List IDs of issues waiting for moderator review."""
    review_issues = [i for i in list_issues() if i.get("needs_review")]
    return [f"{i['id']} - {i['title']}" for i in review_issues]


def view_issue_details(issue_id):
    """Render details, photo, and event log for a selected issue."""
    if not issue_id:
        return "Select an issue to inspect its details.", None, []

    # Strip label if selected from composite dropdown
    clean_id = issue_id.split()[0].strip()
    issue = get_issue(clean_id)
    if not issue:
        return f"Issue `{clean_id}` not found.", None, []

    status_tag = "🟢 OPEN" if issue.get("status") == "open" else "⚪ RESOLVED"
    img_path = ROOT / issue["image"] if issue.get("image") else None
    if img_path and not img_path.exists():
        img_path = None

    details_md = f"""### {status_tag}: #{issue['id']} - {issue['title']}

- **Category:** `{issue['category']}`
- **Severity:** `{issue['severity'].upper()}` | **Safety Risk:** `{'Yes' if issue.get('safety_risk') else 'No'}`
- **Reporter:** `{issue['reporter']}` | **Reported:** {format_campus_time(issue['created_at'])}
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
        return "⚠️ Please select an issue ID first."
    clean_id = issue_id.split()[0].strip()
    try:
        msg = add_me_too(clean_id, by=nickname)
        return f"ℹ️ {msg}"
    except Exception as e:
        return f"⚠️ Error: {e}"


def handle_resolve(issue_id, nickname, note):
    """Handle 'Mark resolved' button."""
    if not issue_id:
        return "⚠️ Please select an issue ID first."
    clean_id = issue_id.split()[0].strip()
    try:
        resolve(clean_id, by=nickname, note=note)
        return f"✅ Issue #{clean_id} marked as resolved! Points updated on the leaderboard."
    except ValueError as ve:
        return f"⚠️ Resolve validation: {ve}"
    except Exception as e:
        return f"⚠️ Error: {e}"


def handle_reopen(issue_id, nickname):
    """Handle 'Reopen' button."""
    if not issue_id:
        return "⚠️ Please select an issue ID first."
    clean_id = issue_id.split()[0].strip()
    try:
        reopen(clean_id, by=nickname)
        return f"🔄 Issue #{clean_id} reopened and restored to active board with original report date."
    except Exception as e:
        return f"⚠️ Error: {e}"


def handle_confirm_review(review_selection, new_category, nickname):
    """Approve or recategorize a flagged issue."""
    if not review_selection:
        return "⚠️ Please choose an item from the review queue."
    clean_id = review_selection.split()[0].strip()
    cat = new_category if (new_category and new_category != "Keep Current") else None
    try:
        confirm_review(clean_id, by=nickname, category=cat)
        return f"✅ Issue #{clean_id} verified and released to the active board!"
    except Exception as e:
        return f"⚠️ Error: {e}"


def ask_the_board(query):
    """Use the model harness with read-only tools to answer campus issue queries."""
    if not query or not query.strip():
        return "", "Please enter a question to ask the board."
    rule = (
        "You are the CampusFix assistant. You help students and staff find campus issues using "
        "the available tools: list_top_issues, count_by_category, and find_issues. "
        "Keep answers concise, direct, and factual."
    )
    agent = Agent(system=rule, max_steps=3)
    try:
        answer = agent.run(query)
    except Exception as err:
        import gemma_client
        return "", gemma_client.describe_error(err)
    steps = "\n".join(f"{i}. {s['text']}" for i, s in enumerate(agent.steps[:-1], 1))
    return steps or "(No tools were needed)", answer


# ---------------- TAB 3: LEADERBOARD ----------------

def load_leaderboard_view(time_window, nickname):
    """Compute leaderboard and campus statistics for all-time or last 7 days."""
    now_utc = datetime.now(timezone.utc)
    all_issues = list_issues()
    window_days = 7 if time_window == "Last 7 days" else None

    rows = compute_leaderboard(all_issues, now_utc, window_days=window_days)

    table_data = []
    for r in rows[:10]:
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
        rank_md = f"🏅 **Your standing:** You are **Rank #{user_rank}** with **{user_row['points']} pts** ({user_row['badges']})!"
    elif nickname and clean_nickname(nickname) != "anonymous":
        rank_md = f"👋 Hello **{clean_nickname(nickname)}**! You haven't earned points yet. Report or fix an issue to appear on the leaderboard!"
    else:
        rank_md = "💡 *Enter a nickname in the box above to see your personal rank and badges.*"

    # Campus stats strip
    stats = campus_stats(all_issues, now_utc)
    oldest_txt = f"#{stats['oldest_open']['id']} ({stats['oldest_age_days']} days)" if stats['oldest_open'] else "None"
    stats_md = f"""| 🏛️ Open Issues | ✅ Resolved This Week | ⏳ Oldest Open Issue | 📊 Average Issue Age |
| :---: | :---: | :---: | :---: |
| **{stats['open_count']}** | **{stats['resolved_this_week']}** | **{oldest_txt}** | **{stats['average_age_days']} days** |
"""
    return table_data, rank_md, stats_md


# ---------------- GRADIO UI CONSTRUCTION ----------------

# CHANGE THIS: customize CSS or component structure
custom_css = """
.main-header { text-align: center; margin-bottom: 8px; }
.sub-header { text-align: center; color: #666; margin-bottom: 16px; font-size: 0.95em; }
.stat-box { border-radius: 8px; padding: 12px; }
"""

with gr.Blocks(title=TITLE, css=custom_css) as demo:
    session_id_state = gr.State(lambda: f"sess_{int(time.time()*1000)}")

    gr.Markdown(f"# 🛠️ {TITLE}: Campus Issue Tracker & Leaderboard", elem_classes=["main-header"])
    gr.Markdown(
        "Report campus problems with text or photos. Gemma 4 classifies category & severity. "
        "Earn community points for reporting, confirming, and resolving issues!",
        elem_classes=["sub-header"],
    )

    # Shared Nickname Box
    with gr.Row():
        with gr.Column(scale=3):
            nickname_input = gr.Textbox(
                label="👤 Your Nickname (shown on leaderboard; use a nickname, not your real name)",
                placeholder="e.g. campus_fixer_42",
                value="",
            )
        with gr.Column(scale=1):
            gr.Markdown(
                "*Public board: Pick a fun nickname to track your points and badges across tabs.*"
            )

    # Tabs
    with gr.Tabs() as tabs:
        # TAB 1: Report a Problem
        with gr.Tab("📝 Report a Problem"):
            with gr.Row():
                with gr.Column():
                    report_img = gr.Image(type="filepath", label="📷 Photo (optional)")
                    report_txt = gr.Textbox(
                        label="📄 Description (optional, max 1000 characters)",
                        placeholder="Describe the issue, location, or what needs fixing...",
                        lines=4,
                        max_lines=8,
                    )
                    report_submit_btn = gr.Button("🚀 Submit Report to Gemma 4", variant="primary")
                with gr.Column():
                    report_status = gr.Markdown("Submit a photo, description, or both to classify your report.")

            report_submit_btn.click(
                submit_report,
                inputs=[report_img, report_txt, nickname_input, session_id_state],
                outputs=[report_status, report_txt],
            )

        # TAB 2: Board
        with gr.Tab("📋 Campus Issue Board"):
            with gr.Row():
                cat_dropdown = gr.Dropdown(
                    label="Filter by Category",
                    choices=get_category_choices(),
                    value="All",
                    interactive=True,
                )
                show_archive_cb = gr.Checkbox(label="Show Resolved Archive", value=False)
                board_refresh_btn = gr.Button("🔄 Refresh Board", variant="secondary")

            board_table = gr.Dataframe(
                headers=["Score", "ID", "Category", "Severity", "Title", "Location", f"Reported ({CAMPUS_TZ})", "Age", "Status", "+1s"],
                datatype=["number", "str", "str", "str", "str", "str", "str", "str", "str", "str"],
                interactive=False,
            )

            gr.Markdown("---")
            gr.Markdown("### 🔍 Inspect & Act on an Issue")

            with gr.Row():
                with gr.Column(scale=1):
                    selected_issue_input = gr.Textbox(
                        label="Issue ID to inspect (6 hex chars)",
                        placeholder="e.g. a1b2c3",
                    )
                    load_issue_btn = gr.Button("View Issue")
                    issue_action_msg = gr.Markdown("")
                    me_too_btn = gr.Button("👍 I face this too (+1)", variant="secondary")

                    resolve_note_input = gr.Textbox(
                        label="Resolution Note (min 10 characters required)",
                        placeholder="Explain what was repaired or resolved...",
                        lines=2,
                    )
                    resolve_btn = gr.Button("✅ Mark Resolved", variant="primary")
                    reopen_btn = gr.Button("🔄 Reopen Issue", variant="stop")

                with gr.Column(scale=2):
                    issue_details_md = gr.Markdown("Select an issue ID to view details.")
                    issue_photo = gr.Image(label="Issue Photo", visible=True)
                    log_table = gr.Dataframe(
                        headers=["Timestamp", "Action", "By", "Note"],
                        interactive=False,
                        label="Issue Event Log",
                    )

            gr.Markdown("---")
            with gr.Accordion("🔍 Needs a Look (Review Queue)", open=False):
                with gr.Row():
                    review_select = gr.Dropdown(
                        label="Flagged Issues",
                        choices=get_review_choices(),
                        interactive=True,
                    )
                    review_cat = gr.Dropdown(
                        label="Set Category",
                        choices=["Keep Current"] + CATEGORIES,
                        value="Keep Current",
                    )
                    confirm_review_btn = gr.Button("Approve / Recategorize", variant="primary")
                review_status_md = gr.Markdown("")

            with gr.Accordion("🤖 Stretch Feature: Ask the Board (Agent with Tools)", open=False):
                gr.Markdown("*(Note: This experimental feature uses the model harness and may make several Gemma calls)*")
                with gr.Row():
                    ask_board_query = gr.Textbox(
                        label="Ask a question about campus issues",
                        placeholder="e.g. Which issues are critical? Or how many problems are in Electrical?",
                        scale=3,
                    )
                    ask_board_btn = gr.Button("Ask the Board", variant="primary", scale=1)
                ask_board_steps = gr.Textbox(label="Agent Tool Steps", lines=3)
                ask_board_answer = gr.Textbox(label="Agent Answer", lines=3)

                ask_board_btn.click(
                    ask_the_board,
                    inputs=[ask_board_query],
                    outputs=[ask_board_steps, ask_board_answer],
                )

            # Board interactions
            def update_board_view(cat, archive):
                clean_cat = cat.split(" (")[0].strip() if cat else "All"
                rows, headers = load_board_data(clean_cat, archive)
                return gr.update(value=rows, headers=headers)

            board_refresh_btn.click(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )
            cat_dropdown.change(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )
            show_archive_cb.change(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )

            load_issue_btn.click(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            )

            me_too_btn.click(
                handle_add_me_too,
                inputs=[selected_issue_input, nickname_input],
                outputs=[issue_action_msg],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )

            resolve_btn.click(
                handle_resolve,
                inputs=[selected_issue_input, nickname_input, resolve_note_input],
                outputs=[issue_action_msg],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )

            reopen_btn.click(
                handle_reopen,
                inputs=[selected_issue_input, nickname_input],
                outputs=[issue_action_msg],
            ).then(
                view_issue_details,
                inputs=[selected_issue_input],
                outputs=[issue_details_md, issue_photo, log_table],
            ).then(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )

            confirm_review_btn.click(
                handle_confirm_review,
                inputs=[review_select, review_cat, nickname_input],
                outputs=[review_status_md],
            ).then(
                update_board_view,
                inputs=[cat_dropdown, show_archive_cb],
                outputs=[board_table],
            )

        # TAB 3: Leaderboard
        with gr.Tab("🏆 Leaderboard & Stats"):
            with gr.Row():
                time_radio = gr.Radio(
                    label="View Window",
                    choices=["All time", "Last 7 days"],
                    value="All time",
                )
                refresh_leaderboard_btn = gr.Button("🔄 Refresh Leaderboard", variant="secondary")

            user_rank_banner = gr.Markdown("Enter your nickname above to see your rank.")
            stats_banner = gr.Markdown("")

            leaderboard_table = gr.Dataframe(
                headers=["Rank", "Nickname", "Points", "Reports", "Resolves", "Badges"],
                datatype=["str", "str", "number", "number", "number", "str"],
                interactive=False,
            )

            gr.Markdown(get_points_rules_md())
            gr.Markdown(
                "> ℹ️ **Fairness & Privacy Notice:** Nicknames are self-assigned and unauthenticated. "
                "The leaderboard is designed for friendly campus community encouragement, not official prizes."
            )

            def update_leaderboard_tab(window, nick):
                table, rank_txt, stats_txt = load_leaderboard_view(window, nick)
                return table, rank_txt, stats_txt

            refresh_leaderboard_btn.click(
                update_leaderboard_tab,
                inputs=[time_radio, nickname_input],
                outputs=[leaderboard_table, user_rank_banner, stats_banner],
            )
            time_radio.change(
                update_leaderboard_tab,
                inputs=[time_radio, nickname_input],
                outputs=[leaderboard_table, user_rank_banner, stats_banner],
            )

    # Initial load trigger for Board and Leaderboard
    demo.load(
        update_board_view,
        inputs=[cat_dropdown, show_archive_cb],
        outputs=[board_table],
    ).then(
        update_leaderboard_tab,
        inputs=[time_radio, nickname_input],
        outputs=[leaderboard_table, user_rank_banner, stats_banner],
    )

if __name__ == "__main__":
    is_lan = os.environ.get("CAMPUSFIX_LAN") == "1"
    server = "0.0.0.0" if is_lan else "127.0.0.1"
    print(f"Starting CampusFix on {server}:7860 (LAN access: {is_lan})...")
    if is_lan:
        demo.launch(server_name="0.0.0.0", share=False)
    else:
        demo.launch(share=False)
