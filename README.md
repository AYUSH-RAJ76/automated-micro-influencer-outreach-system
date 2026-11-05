# Creator Outreach

A local tool for discovering real YouTube creators, qualifying them, preparing personalized outreach drafts, and tracking reviewed campaigns. The database is not pre-filled with sample creators.

## Technology stack

Python, Streamlit dashboard, FastAPI service, SQLAlchemy ORM, SQLite database, and pandas CSV exports.

## APIs and tools

- YouTube Data API v3 for channel and video discovery/enrichment.
- Groq OpenAI-compatible Chat Completions API for outreach drafts. The default model is `openai/gpt-oss-120b` (`GROQ_MODEL`).
- SMTP with STARTTLS for email. Campaign email is simulated by default (`DRY_RUN=true`).
- pytest for automated tests.

## Data sources

Channel and video facts come from public data returned by YouTube Data API v3. Email addresses and contact links are extracted from public channel descriptions or entered by a user after manual verification. The app does not guess email addresses or scrape external sites.

## Discovery methodology

Choose a niche and target count in the dashboard. The app builds niche-related search queries, runs a bounded number of YouTube `search.list` calls sequentially, deduplicates by channel ID, then batches channel and recent-video lookups for enrichment. Results are saved to the local database, including partial results if a later query fails. Re-running discovery can update existing channels without creating duplicates. Search responses are cached in-process to reduce quota use.

## Filtering logic

Channels are checked against configured subscriber limits, niche relevance in channel/video text, recent video activity, and available video engagement. Defaults require 5,000–100,000 subscribers, at least three recent videos within 180 days, and 1% engagement. Missing public metrics may produce `NEEDS_REVIEW`; a failed requirement produces `FAIL`. Adjust `MIN_SUBSCRIBERS`, `MAX_SUBSCRIBERS`, `RECENT_DAYS`, and `ENGAGEMENT_THRESHOLD` in `.env`.

## Enrichment process

The app fetches channel details and recent videos through the YouTube API. It scans the public channel description for email addresses and links, and uses recent video titles as content context. Hidden business inquiry emails are not available through this API; verify those on the creator's About page and enter the public contact details manually.

## AI model and prompt

Groq Chat Completions uses `GROQ_MODEL` (default `openai/gpt-oss-120b`) and requests a structured JSON response containing an email subject, email body, and DM draft. The prompt uses the creator's name, selected niche, and available recent-video context to draft a relevant collaboration message. It instructs the model not to claim personal familiarity or facts that are not present in the supplied profile. Drafts require human review and approval.

## Personalization logic

Drafts are grounded in the creator's saved profile, niche, and recent video titles. The app does not infer demographics or invent creator interests. Users can edit each draft before approving it.

## Sending mechanism

Approved messages with a valid public email address and no do-not-contact flag can be sent by configured SMTP. Live sending requires `DRY_RUN=false` and explicit confirmation in the dashboard. A separate test-email action sends only to the address entered for that test. Instagram DMs are drafts for manual sending; Instagram discovery and automated DM sending are not implemented. Outreach Tracker records send outcomes and manual follow-up activity.

## Limitations

- YouTube search consumes quota (each `search.list` call costs 100 units); results and public metrics depend on Google's API availability and data access.
- YouTube does not expose hidden business emails or creator demographics through this workflow.
- Niche relevance is based on text and is heuristic; qualification is not an endorsement of a creator.
- SMTP acceptance does not guarantee inbox delivery.
- Instagram discovery and automated messaging are not supported.
- Credentials and live API access are supplied by the user; no fake discovery records are generated.

## Setup

Requires Python 3.10 or later. From PowerShell in the project directory:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Add `YOUTUBE_API_KEY` and `GROQ_API_KEY` to `.env`; keep `DRY_RUN=true` while reviewing the workflow. Start the dashboard:

```powershell
streamlit run dashboard/streamlit_app.py
```

Optional FastAPI service and API docs:

```powershell
uvicorn app.api.main:app --reload
```

Open `http://127.0.0.1:8000/docs`. Run the tests with:

```powershell
python -m pytest -q
```

Never commit `.env` or share its API keys and SMTP credentials.
