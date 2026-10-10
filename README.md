# CampusFix 🛠️

CampusFix is a shared campus issue tracker for students and staff to report, track, and resolve physical facility problems using photos or descriptions. Powered by Google's **Gemma 4** through the Gemini API, issues are automatically triaged by category and severity, with an event-sourced community leaderboard that rewards students for reporting, confirming ("+1"), and fixing problems.

Team: **The Creative Crew**  
Event: **MLH Hack Day: Gemma 4 through the Gemini API**  
Template: Generated from `RudraDudhat2509/gemma4-starter` (MIT)

---

## Screenshot

![screenshot](docs/screenshot.png)

---

## How Gemma 4 is Used

CampusFix uses **`gemma-4-26b-a4b-it`** through the official Google Gemini API via `google-genai` and `gemma_client.py`.

- **Judges Meaning, Not Scores:** Gemma 4 acts as an intelligent campus triage officer. It classifies the report into categories, assesses severity (`critical`, `high`, `medium`, `low`), checks safety risk, extracts location hints, and provides a clear reasoning summary.
- **Exact Call Location:**
  The multimodal triage call is executed in [`campusfix/triage.py`](file:///campusfix/triage.py):
  ```python
  # campusfix/triage.py line 208
  raw_response = call_ask_img(str(image_path), prompt, system=TRIAGE_RULE)
  ```
  which calls `gemma_client.ask_with_image(str(image_path), prompt, system=TRIAGE_RULE)`.
- **Strict Budget & High Efficiency:** Exactly **one Gemma 4 call is made per issue submission**. The Board, issue inspection, "+1" confirmations, resolution, reopening, leaderboard recalculations, and campus statistics all run in deterministic local Python with **zero additional API calls**.
- **Deterministic Safety Floor:** Python code enforces a hard safety floor that can only raise severity (never lower it), protecting against prompt injections and safeguarding campus hazards.

---

## How to run it (5 steps)

### 1. Get a free API key
Sign in to [Google AI Studio](https://aistudio.google.com) and create a Gemini API key.

### 2. Clone the repository
Open a terminal in the project folder:
```bash
git clone https://github.com/gokulsanjayreddy/thecreativecrew.git
cd thecreativecrew
```

### 3. Create a virtual environment and install dependencies

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
```
*(If PowerShell restricts scripts, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first).*

**Mac / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 4. Add your API key
Copy `.env.example` to `.env` and paste your Gemini API key:
- **Windows:** `Copy-Item .env.example .env`
- **Mac / Linux:** `cp .env.example .env`

Open `.env` and set:
```env
GEMINI_API_KEY=your_actual_api_key_here
```
*(Never commit `.env`; it is strictly ignored by git).*

### 5. Check setup and start CampusFix

Verify environment readiness:
```bash
python 00_check_setup.py
python -m pytest
```

Launch the **CampusFix** application:
```bash
python campusfix/app.py
```
Open **http://127.0.0.1:7860** in your browser.

#### Campus Network / LAN Access
To allow everyone on your college Wi-Fi or local network to access the issue tracker:
- **Windows (PowerShell):**
  ```powershell
  $env:CAMPUSFIX_LAN="1"; python campusfix/app.py
  ```
- **Mac / Linux:**
  ```bash
  CAMPUSFIX_LAN=1 python campusfix/app.py
  ```

---

## Architecture & Design

Full architecture documentation and interactive diagrams are available in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

- **Aging Algorithm:** Priority scores increase by `1.5` points per day (capped at 40 days) so long-ignored issues naturally bubble up to the top of the campus board:
  $$\text{Score} = \text{Severity Base} + \min(\text{Age in Days}, 40) \times 1.5 + \min(\text{Confirmations} \times 2, 10)$$
- **Event-Sourced Replay:** The leaderboard is computed directly by replaying the immutable event log in `notes/issues.json`. Reopening an issue cancels the points previously awarded to the resolver.
- **Atomic Persistence:** All issue records are saved using atomic `os.replace` operations with threading locks and corrupt-file recovery.
- **Privacy First:** Photos are stripped of EXIF metadata before saving to protect student privacy and location metadata.

---

## Honest Product Limits

To ensure full transparency with hackathon judges:
- **No Login / Authentication:** Nicknames are self-chosen without password authentication. The leaderboard is intended for friendly community motivation rather than high-stakes prizes.
- **File-Based Storage:** Persistence uses an atomic JSON store (`notes/issues.json`) and local images (`notes/images/`) rather than an external database engine or ORM.
- **Single-Machine Instance:** Designed for local campus servers or single-node deployments.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
