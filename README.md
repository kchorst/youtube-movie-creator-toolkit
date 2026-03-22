# CatsofTravels — YouTube Video Pipeline

A menu-driven Python pipeline for producing, managing, and publishing travel videos to YouTube. Built for the [CatsofTravels](https://www.youtube.com/@CatsofTravels) channel — but fully configurable for any YouTube creator.

---

## What It Does

| Stage | Script | Description |
|-------|--------|-------------|
| UC1 | `make_show.py` | Render silent MP4s from photo folders |
| UC2 | `make_show.py` | Add music to rendered videos |
| UC3 | `youtube_meta.py` | Generate titles, descriptions, tags via local LLM |
| UC4 | `youtube_upload.py` | Upload videos to YouTube via API |
| UC5 | `cot_analytics.py` | Pull YouTube Analytics |
| UC6 | `youtube_meta.py` | View, search and edit live YouTube metadata |

Everything is launched from a single menu: `python cot_pipeline.py`

---

## Prerequisites

### Python
- Python 3.9 or higher
- Run `python --version` to check

### Required packages
```bash
pip install requests google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client pyreadline3
```

`pyreadline3` is optional (Windows only) — enables inline editing with arrow keys.

### Google Cloud Console setup (one-time)
You need a Google Cloud project with the YouTube Data API enabled and an OAuth 2.0 client credential.

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Go to **APIs & Services → Enable APIs** and enable:
   - YouTube Data API v3
   - YouTube Analytics API
4. Go to **APIs & Services → OAuth consent screen**:
   - Set app name, support email
   - Add your YouTube account email as a **Test user**
   - Scopes: `youtube`, `youtube.upload`, `youtube.force-ssl`, `yt-analytics.readonly`
5. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**:
   - Application type: **Desktop app**
   - Download the JSON file
   - Rename it to `client_secrets.json`
   - Place it in your scripts folder (same folder as `cot_pipeline.py`)

### LM Studio (optional — for AI metadata generation)
- Download [LM Studio](https://lmstudio.ai)
- Load a model (recommended: `meta-llama-3.1-8b-instruct` or `mistral-7b-instruct`)
- Start the Local Server (the `<->` tab) on port 1234
- If you don't want to use a local LLM, set `LLM_MODE = manual_only` in the setup wizard

---

## Installation

```bash
git clone https://github.com/yourname/catsoftravels-pipeline.git
cd catsoftravels-pipeline
pip install -r requirements.txt
```

Place `client_secrets.json` in the same folder.

---

## First Run

```bash
python cot_pipeline.py
```

On first run, the setup wizard launches automatically and asks for:
- Your pictures folder path
- Your output/movies folder path
- Google auth file locations
- LLM mode (local LM Studio or manual editing only)
- LM Studio URL and model name (if using LLM)
- YouTube channel defaults (category, language, kids setting, etc.)

Settings are saved to `cot_config.json` (gitignored — never committed).

---

## Configuration

All settings live in `cot_config.json`. To edit them at any time:

```
python cot_pipeline.py → A (ADMIN) → 1 (Edit configuration)
```

Or run the config module directly:
```bash
python cot_config.py
```

### Key settings

| Setting | Description | Default |
|---------|-------------|---------|
| `PICTURES_DIR` | Root folder containing photo subfolders | — |
| `OUTPUT_DIR` | Where rendered MP4s are saved | — |
| `LLM_MODE` | `lmstudio_local` or `manual_only` | `lmstudio_local` |
| `LMSTUDIO_URL` | LM Studio server URL | `http://127.0.0.1:1234/v1/chat/completions` |
| `MODEL_NAME` | LM Studio model identifier | — |
| `YT_CATEGORY` | YouTube category ID | `19` (Travel & Events) |
| `YT_LANGUAGE` | Default video language | `en` |
| `YT_KIDS` | Made for kids | `false` |

---

## ADMIN Menu

From the pipeline menu, press **A** to open ADMIN:

- **Edit configuration** — re-run the setup wizard
- **Check dependencies** — scan all required packages, show pip install commands for missing ones
- **Check Google auth** — verify `client_secrets.json` and `token.json` exist and are readable
- **Check LM Studio** — verify server is running and list loaded models
- **Show current config** — display all settings

---

## UC6 — Live Metadata Editor

UC6 lets you view, search and edit metadata directly on YouTube without re-uploading.

Features:
- Lists all channel videos including **private** ones (uses uploads playlist, not search API)
- Sorted: private → unlisted → public
- Search by title or date with `/query`
- Edit title, description, tags inline
- Change privacy, kids setting, license, category
- Regenerate with local LLM (T/D/B/S keys)
- **Bulk privacy change** — set multiple videos at once (B key)
- **Dry run mode** — preview changes without pushing (V key)
- After push: re-fetches and confirms fields were saved

> **Note on drafts and unlisted videos:** YouTube's API does not allow changing the privacy of unlisted videos or draft videos (videos never fully published). These are flagged with ⚠ in the list with a direct link to YouTube Studio where you can make the change manually.

---

## Folder Structure

```
COTCode/
├── cot_pipeline.py       # Main launcher
├── cot_config.py         # Config manager
├── make_show.py          # Video renderer
├── youtube_meta.py       # Metadata generator + UC6
├── youtube_upload.py     # YouTube uploader
├── cot_analytics.py      # Analytics puller
├── client_secrets.json   # Google OAuth (gitignored)
├── token.json            # Google token (gitignored)
└── cot_config.json       # Your settings (gitignored)

COTMovies/                # Output folder (set in config)
├── youtube_uploads.csv   # Metadata CSV (gitignored)
├── upload_log.json       # Upload history (gitignored)
├── quota_log.json        # API quota log (gitignored)
└── seeds.json            # Saved seed notes (gitignored)
```

---

## YouTube API Quota

The YouTube Data API has a free daily quota of **10,000 units**.

| Operation | Cost |
|-----------|------|
| Video upload | ~1,600 units |
| Thumbnail upload | 50 units |
| Metadata update | 50 units |
| Read operations | 1–5 units |

Safe daily limit: **~6 videos with thumbnails**. The pipeline tracks quota in `quota_log.json` and shows remaining capacity before and after each upload session.

To request higher quota: [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → YouTube Data API v3 → Quotas.

---

## Metadata Style

UC3 generates metadata in the voice of **Mark Twain's travel journals** — dry, worldly, amused. The channel persona is "the Cats" — a seasoned traveler who notices what others walk past.

The LLM prompt includes:
- Full Twain style guide
- Banned words list (explore, vibrant, stunning, etc.)
- Few-shot examples from real channel videos
- Optional eyewitness "seed notes" per video
- Location-specific knowledge for 10+ destinations

Seed notes are saved per folder in `seeds.json` and reloaded automatically on re-runs.

---

## License

MIT — fork freely, just remove the CatsofTravels branding from your channel defaults.
