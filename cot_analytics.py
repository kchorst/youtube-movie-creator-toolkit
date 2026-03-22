# ------------------------------------------------------------
# cot_analytics.py
# Version: 1.0.0
#
# Purpose:
#   Pull YouTube Analytics for all videos on the CatsofTravels
#   channel. Discovers videos automatically — works on videos
#   uploaded manually as well as via youtube_upload.py.
#
# Features:
#   - Auto-discovers all videos on the channel via YouTube Data API
#   - Matches videos to folder_name via upload_log.json where possible
#   - Pulls per-video analytics: views, watch time, CTR, countries,
#     traffic sources, subscribers, impressions
#   - Writes analytics.csv sorted by views descending
#   - Prints top 10 leaderboard after pulling
#   - Overwrites existing rows on re-run (always fresh data)
#   - date_pulled recorded per row
#
# Usage:
#   python cot_analytics.py
#   Or via cot_pipeline.py UC5
#
# Notes:
#   - YouTube Analytics data has a 2-3 day delay
#   - Requires YouTube Analytics API enabled in Google Cloud Console
#   - token.json must include analytics scope (delete old token.json
#     and re-authenticate if upgrading from youtube_upload.py only)
#
# Dependencies:
#   pip install google-auth google-auth-oauthlib google-auth-httplib2
#   pip install google-api-python-client
# ------------------------------------------------------------

import os
import sys
import csv
import json
from datetime import datetime, timedelta

try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
except ImportError:
    print("ERROR: Google API libraries not installed.")
    print("Run:")
    print("  pip install google-auth google-auth-oauthlib google-auth-httplib2")
    print("  pip install google-api-python-client")
    sys.exit(1)


# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

SCRIPTS_DIR     = r"C:\Users\kchor\Desktop\COTCode"
OUTPUT_DIR      = r"C:\Users\kchor\Pictures\COTMovies"

CLIENT_SECRETS  = os.path.join(SCRIPTS_DIR, "client_secrets.json")
TOKEN_FILE      = os.path.join(SCRIPTS_DIR, "token.json")
UPLOAD_LOG      = os.path.join(OUTPUT_DIR,  "upload_log.json")
ANALYTICS_CSV   = os.path.join(OUTPUT_DIR,  "analytics.csv")

# OAuth scopes — covers upload + analytics + reporting
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

# Analytics date range — how far back to pull data
# YouTube Analytics has a 2-3 day delay so end date is 3 days ago
DAYS_BACK       = 365 * 3      # Pull up to 3 years of history
DATE_END_OFFSET = 3            # End 3 days ago to avoid empty data

# Top countries to report
TOP_COUNTRIES   = 5

# Leaderboard size
LEADERBOARD_N   = 10

# Analytics CSV columns
CSV_FIELDS = [
    "folder_name",
    "youtube_id",
    "youtube_url",
    "title",
    "published_at",
    "views",
    "watch_time_minutes",
    "avg_view_duration_seconds",
    "avg_view_duration_formatted",
    "subscribers_gained",
    "subscribers_lost",
    "impressions",
    "ctr",
    "top_country_1",
    "top_country_2",
    "top_country_3",
    "top_country_4",
    "top_country_5",
    "traffic_search",
    "traffic_suggested",
    "traffic_external",
    "traffic_direct",
    "traffic_other",
    "date_pulled",
]


# ------------------------------------------------------------
# LOGGING
# ------------------------------------------------------------

def log(msg, also_print=True):
    """Write timestamped message to console and log file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line      = f"[{timestamp}] {msg}"
    if also_print:
        print(line)
    log_path = os.path.join(OUTPUT_DIR, "analytics_log.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------------------------------------
# OAUTH AUTHENTICATION
# ------------------------------------------------------------

def authenticate():
    """
    Authenticate with Google OAuth2.
    Uses shared token.json — covers Data API, Analytics API, Upload.
    First run opens browser. Subsequent runs load token silently.
    """
    if not os.path.isfile(CLIENT_SECRETS):
        print(f"\n  ERROR: client_secrets.json not found at:")
        print(f"  {CLIENT_SECRETS}")
        sys.exit(1)

    creds = None

    if os.path.isfile(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                log("  Token refreshed.")
            except Exception:
                creds = None

        if not creds:
            print("\n  Opening browser for Google authentication...")
            print("  Log in with the Google account linked to your YouTube channel.")
            flow  = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS, SCOPES)
            creds = flow.run_local_server(port=0)
            print("  Authentication successful.\n")

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    # Build both API services
    youtube   = build("youtube",         "v3",  credentials=creds)
    analytics = build("youtubeAnalytics", "v2", credentials=creds)

    return youtube, analytics


# ------------------------------------------------------------
# UPLOAD LOG
# ------------------------------------------------------------

def load_upload_log():
    """
    Load upload_log.json.
    Returns dict: { folder_name: { youtube_id, title, ... } }
    Also builds reverse map: { youtube_id: folder_name }
    """
    if not os.path.isfile(UPLOAD_LOG):
        return {}, {}

    try:
        with open(UPLOAD_LOG, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, {}

    reverse = {v["youtube_id"]: k for k, v in data.items() if "youtube_id" in v}
    return data, reverse


# ------------------------------------------------------------
# CHANNEL VIDEO DISCOVERY
# ------------------------------------------------------------

def get_channel_id(youtube):
    """Get the authenticated user's channel ID."""
    response = youtube.channels().list(
        part="id,snippet",
        mine=True
    ).execute()

    items = response.get("items", [])
    if not items:
        print("  ERROR: No channel found for this account.")
        sys.exit(1)

    channel_id   = items[0]["id"]
    channel_name = items[0]["snippet"]["title"]
    log(f"  Channel: {channel_name} ({channel_id})")
    return channel_id


def discover_all_videos(youtube, channel_id):
    """
    Discover all videos on the channel using the search API.
    Returns list of dicts: { youtube_id, title, published_at }
    Handles pagination — fetches all pages.
    """
    videos     = []
    page_token = None

    print("  Discovering channel videos...", end="", flush=True)

    while True:
        params = {
            "part":       "id,snippet",
            "channelId":  channel_id,
            "type":       "video",
            "maxResults": 50,
            "order":      "date",
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            response   = youtube.search().list(**params).execute()
        except HttpError as e:
            log(f"\n  ERROR discovering videos: {e}")
            break

        for item in response.get("items", []):
            videos.append({
                "youtube_id":   item["id"]["videoId"],
                "title":        item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"][:10],  # YYYY-MM-DD
            })

        page_token = response.get("nextPageToken")
        print(".", end="", flush=True)

        if not page_token:
            break

    print(f" {len(videos)} videos found.\n")
    return videos


# ------------------------------------------------------------
# ANALYTICS PULLING
# ------------------------------------------------------------

def get_date_range():
    """
    Calculate start and end dates for analytics query.
    End date is 3 days ago (YouTube data delay).
    Start date is DAYS_BACK days before end date.
    Returns (start_date, end_date) as YYYY-MM-DD strings.
    """
    end_date   = datetime.now() - timedelta(days=DATE_END_OFFSET)
    start_date = end_date - timedelta(days=DAYS_BACK)
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def pull_video_metrics(analytics, youtube_id, start_date, end_date):
    """
    Pull core metrics for a single video from YouTube Analytics API.
    Returns dict of metric values, or empty dict on failure.
    """
    try:
        response = analytics.reports().query(
            ids=f"channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics=",".join([
                "views",
                "estimatedMinutesWatched",
                "averageViewDuration",
                "subscribersGained",
                "subscribersLost",
                "impressions",
                "impressionsClickThroughRate",
            ]),
            filters=f"video=={youtube_id}",
            dimensions="video",
        ).execute()

        rows = response.get("rows", [])
        if not rows:
            return {}

        row = rows[0]
        return {
            "views":                    int(row[1]),
            "watch_time_minutes":       round(float(row[2]), 1),
            "avg_view_duration_seconds": int(row[3]),
            "subscribers_gained":       int(row[4]),
            "subscribers_lost":         int(row[5]),
            "impressions":              int(row[6]),
            "ctr":                      round(float(row[7]) * 100, 2),  # as percentage
        }

    except HttpError as e:
        log(f"  WARNING: Could not pull metrics for {youtube_id}: {e}", also_print=False)
        return {}


def pull_top_countries(analytics, youtube_id, start_date, end_date):
    """
    Pull top countries by views for a single video.
    Returns list of up to TOP_COUNTRIES country codes.
    """
    try:
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views",
            filters=f"video=={youtube_id}",
            dimensions="country",
            sort="-views",
            maxResults=TOP_COUNTRIES,
        ).execute()

        rows = response.get("rows", [])
        return [row[0] for row in rows]

    except HttpError:
        return []


def pull_traffic_sources(analytics, youtube_id, start_date, end_date):
    """
    Pull traffic source breakdown for a single video.
    Returns dict: { search, suggested, external, direct, other }
    All values as percentage of total views.
    """
    try:
        response = analytics.reports().query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=end_date,
            metrics="views",
            filters=f"video=={youtube_id}",
            dimensions="insightTrafficSourceType",
        ).execute()

        rows = response.get("rows", [])
        if not rows:
            return {}

        # Map YouTube traffic source types to friendly names
        source_map = {
            "YT_SEARCH":            "traffic_search",
            "SUGGESTED_VIDEO":      "traffic_suggested",
            "EXT_URL":              "traffic_external",
            "DIRECT_OR_UNKNOWN":    "traffic_direct",
            "NO_LINK_OTHER":        "traffic_other",
            "NOTIFICATION":         "traffic_other",
            "PLAYLIST":             "traffic_other",
            "SUBSCRIBER":           "traffic_other",
            "YT_CHANNEL":           "traffic_other",
            "END_SCREEN":           "traffic_other",
            "CAMPAIGN_CARD":        "traffic_other",
            "SHORTS":               "traffic_other",
        }

        total  = sum(int(r[1]) for r in rows)
        result = {
            "traffic_search":    0.0,
            "traffic_suggested": 0.0,
            "traffic_external":  0.0,
            "traffic_direct":    0.0,
            "traffic_other":     0.0,
        }

        for row in rows:
            source_type = row[0]
            views       = int(row[1])
            key         = source_map.get(source_type, "traffic_other")
            if total > 0:
                result[key] = round(result[key] + (views / total * 100), 1)

        return result

    except HttpError:
        return {}


def format_duration(seconds):
    """Convert seconds to MM:SS string for readability."""
    if not seconds:
        return "0:00"
    mins = seconds // 60
    secs = seconds % 60
    return f"{mins}:{secs:02d}"


# ------------------------------------------------------------
# ANALYTICS CSV
# ------------------------------------------------------------

def save_analytics_csv(rows):
    """
    Write analytics data to analytics.csv.
    Sorted by views descending.
    Overwrites existing file completely on each run.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    sorted_rows = sorted(rows, key=lambda r: int(r.get("views", 0)), reverse=True)

    with open(ANALYTICS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sorted_rows)

    log(f"  Analytics saved: {ANALYTICS_CSV}")
    return sorted_rows


# ------------------------------------------------------------
# LEADERBOARD
# ------------------------------------------------------------

def print_leaderboard(sorted_rows, n=LEADERBOARD_N):
    """Print top N videos by views as a formatted leaderboard."""
    print(f"\n{'='*70}")
    print(f"  TOP {n} VIDEOS BY VIEWS — CatsofTravels")
    print(f"{'='*70}")
    print(f"  {'#':<4} {'TITLE':<40} {'VIEWS':>8} {'WATCH TIME':>12} {'AVG DUR':>8}")
    print(f"  {'─'*4} {'─'*40} {'─'*8} {'─'*12} {'─'*8}")

    for i, row in enumerate(sorted_rows[:n], 1):
        title    = row.get("title", "")[:39]
        views    = f"{int(row.get('views', 0)):,}"
        wt_mins  = row.get("watch_time_minutes", 0)
        wt_str   = f"{wt_mins:,.0f} min" if wt_mins else "—"
        avg_dur  = row.get("avg_view_duration_formatted", "—")
        print(f"  {i:<4} {title:<40} {views:>8} {wt_str:>12} {avg_dur:>8}")

    print(f"{'='*70}\n")


# ------------------------------------------------------------
# MAIN ANALYTICS PULL
# ------------------------------------------------------------

def run_analytics():
    """
    Main analytics pull function.
    1. Authenticate
    2. Discover all channel videos
    3. Pull analytics per video
    4. Save CSV and print leaderboard
    """
    print("\n  Authenticating...")
    youtube, analytics = authenticate()
    print("  Authenticated.\n")

    # Get channel
    channel_id = get_channel_id(youtube)

    # Discover all videos
    videos = discover_all_videos(youtube, channel_id)
    if not videos:
        print("  No videos found on channel.")
        return

    # Load upload log for folder_name matching
    upload_log, reverse_log = load_upload_log()

    # Date range
    start_date, end_date = get_date_range()
    log(f"  Analytics period: {start_date} to {end_date}")

    # Pull analytics per video
    print(f"  Pulling analytics for {len(videos)} videos...\n")
    date_pulled = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results     = []
    done        = 0

    for i, video in enumerate(videos, 1):
        youtube_id = video["youtube_id"]
        title      = video["title"]
        folder_name = reverse_log.get(youtube_id, "")

        print(f"  [{i}/{len(videos)}] {title[:55]}...", end="\r")

        # Pull all metrics
        metrics  = pull_video_metrics(analytics, youtube_id, start_date, end_date)
        countries = pull_top_countries(analytics, youtube_id, start_date, end_date)
        traffic  = pull_traffic_sources(analytics, youtube_id, start_date, end_date)

        # Pad countries list to TOP_COUNTRIES length
        while len(countries) < TOP_COUNTRIES:
            countries.append("")

        # Format average view duration
        avg_dur_sec = metrics.get("avg_view_duration_seconds", 0)

        row = {
            "folder_name":                folder_name,
            "youtube_id":                 youtube_id,
            "youtube_url":                f"https://youtu.be/{youtube_id}",
            "title":                      title,
            "published_at":               video["published_at"],
            "views":                      metrics.get("views", 0),
            "watch_time_minutes":         metrics.get("watch_time_minutes", 0),
            "avg_view_duration_seconds":  avg_dur_sec,
            "avg_view_duration_formatted": format_duration(avg_dur_sec),
            "subscribers_gained":         metrics.get("subscribers_gained", 0),
            "subscribers_lost":           metrics.get("subscribers_lost", 0),
            "impressions":                metrics.get("impressions", 0),
            "ctr":                        metrics.get("ctr", 0),
            "top_country_1":              countries[0],
            "top_country_2":              countries[1],
            "top_country_3":              countries[2],
            "top_country_4":              countries[3],
            "top_country_5":              countries[4],
            "traffic_search":             traffic.get("traffic_search", 0),
            "traffic_suggested":          traffic.get("traffic_suggested", 0),
            "traffic_external":           traffic.get("traffic_external", 0),
            "traffic_direct":             traffic.get("traffic_direct", 0),
            "traffic_other":              traffic.get("traffic_other", 0),
            "date_pulled":                date_pulled,
        }

        results.append(row)
        done += 1

    print(f"\n  Pulled analytics for {done} videos.        ")

    # Save and display
    sorted_rows = save_analytics_csv(results)
    print_leaderboard(sorted_rows)


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║      CatsofTravels — YouTube Analytics               ║")
    print("║                cot_analytics.py                      ║")
    print("║                Version 1.0.0                         ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  NOTE: YouTube Analytics data has a 2-3 day delay.")
    print(f"  Analytics CSV: {ANALYTICS_CSV}")
    print()

    print("  SELECT ACTION")
    print("  A. Pull analytics for all channel videos")
    print("  L. Show leaderboard from existing analytics.csv")
    print("  Q. Quit")
    print()

    while True:
        choice = input("  Choice: ").strip().upper()
        if choice in ("A", "L", "Q"):
            break
        print("  Invalid choice.")

    if choice == "Q":
        print("  Goodbye.")
        return

    elif choice == "L":
        # Load and display existing analytics without re-pulling
        if not os.path.isfile(ANALYTICS_CSV):
            print(f"\n  No analytics.csv found. Run option A first.")
            return
        rows = []
        with open(ANALYTICS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                row["views"] = int(row.get("views", 0))
                rows.append(row)
        sorted_rows = sorted(rows, key=lambda r: r["views"], reverse=True)
        print_leaderboard(sorted_rows)

    elif choice == "A":
        run_analytics()

    print("  Done.")


if __name__ == "__main__":
    main()
