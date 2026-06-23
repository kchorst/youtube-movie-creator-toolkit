from __future__ import annotations

from typing import Any, Callable, Dict, List

import sys


def _is_timeout_exc(exc: BaseException) -> bool:
    # googleapiclient can raise timeouts from different layers depending on transport
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, OSError):
        # Common Windows timeout codes: 10060 (connection timed out)
        try:
            return getattr(exc, "winerror", None) in (10060, 10061)
        except Exception:
            return False
    return False


def _sleep_backoff(attempt: int) -> None:
    import time

    time.sleep(min(6.0, 1.5 * attempt))


def _import_youtube_upload():
    try:
        import youtube_upload as yt_upload
        return yt_upload
    except ImportError as e:
        raise ImportError(
            "youtube_upload.py not found or missing dependencies. "
            "Ensure google-api-python-client and google-auth-oauthlib are installed."
        ) from e


def authenticate():
    yt_upload = _import_youtube_upload()
    return yt_upload.authenticate()


def get_channel_id(youtube: Any) -> str:
    try:
        import cot_config as _cfg
        _cfg.load(gui_mode=True)
    except Exception:
        _cfg = None

    preferred = _cfg.get("YT_CHANNEL_ID", "") if _cfg else ""

    last_exc: Exception | None = None
    response = None
    for attempt in range(1, 4):
        try:
            response = youtube.channels().list(part="id,snippet", mine=True).execute()
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if _is_timeout_exc(e) and attempt < 3:
                _sleep_backoff(attempt)
                continue
            raise

    if last_exc is not None or response is None:
        raise RuntimeError("Connection to YouTube API timed out while fetching channel list.") from last_exc

    items = response.get("items", [])
    if not items:
        raise RuntimeError("No channel found for this account.")

    channels = []
    for it in items:
        cid = it.get("id", "")
        title = (it.get("snippet") or {}).get("title", "")
        if cid:
            channels.append((cid, title))

    if preferred and any(cid == preferred for cid, _t in channels):
        return preferred

    if len(channels) == 1:
        chosen = channels[0][0]
        if _cfg and not preferred:
            try:
                _cfg.set("YT_CHANNEL_ID", chosen, save_now=True)
            except Exception:
                pass
        return chosen

    # Multiple channels available: prompt if interactive.
    try:
        is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    except Exception:
        is_tty = False

    if not is_tty:
        # Non-interactive: pick the first channel.
        return channels[0][0]

    print("\n  Multiple YouTube channels detected for this Google account:")
    for i, (cid, title) in enumerate(channels, 1):
        label = title or "(no title)"
        print(f"    {i}. {label}  [{cid}]")

    while True:
        sel = input("\n  Select channel number (or Enter for 1): ").strip()
        if not sel:
            idx = 1
            break
        if sel.isdigit() and 1 <= int(sel) <= len(channels):
            idx = int(sel)
            break
        print("  Invalid selection.")

    chosen = channels[idx - 1][0]
    if _cfg:
        try:
            _cfg.set("YT_CHANNEL_ID", chosen, save_now=True)
        except Exception:
            pass
    return chosen


def fetch_all_channel_videos(youtube: Any, channel_id: str) -> List[Dict[str, Any]]:
    from googleapiclient.errors import HttpError

    last_exc: Exception | None = None
    ch_resp = None
    for attempt in range(1, 4):
        try:
            ch_resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
            last_exc = None
            break
        except Exception as e:
            last_exc = e
            if _is_timeout_exc(e) and attempt < 3:
                _sleep_backoff(attempt)
                continue
            raise

    if last_exc is not None or ch_resp is None:
        raise RuntimeError("Connection to YouTube API timed out while fetching uploads playlist.") from last_exc

    try:
        uploads_playlist = (
            ch_resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        )
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Error fetching uploads playlist: {e}") from e

    video_ids: List[str] = []
    seen_video_ids = set()
    page_token = None
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = None
            last_exc = None
            for attempt in range(1, 4):
                try:
                    resp = youtube.playlistItems().list(**params).execute()
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    if _is_timeout_exc(e) and attempt < 3:
                        _sleep_backoff(attempt)
                        continue
                    raise

            if last_exc is not None or resp is None:
                raise RuntimeError("Connection to YouTube API timed out while fetching playlist items.") from last_exc
        except HttpError as e:
            raise RuntimeError(f"Error fetching playlist items: {e}") from e

        for item in resp.get("items", []):
            vid_id = item["contentDetails"]["videoId"]
            if vid_id in seen_video_ids:
                continue
            seen_video_ids.add(vid_id)
            video_ids.append(vid_id)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return []

    videos: List[Dict[str, Any]] = []
    seen_videos = set()
    for i in range(0, len(video_ids), 50):
        batch_ids = ",".join(video_ids[i : i + 50])
        try:
            resp = None
            last_exc = None
            for attempt in range(1, 4):
                try:
                    resp = youtube.videos().list(part="snippet,status", id=batch_ids).execute()
                    last_exc = None
                    break
                except Exception as e:
                    last_exc = e
                    if _is_timeout_exc(e) and attempt < 3:
                        _sleep_backoff(attempt)
                        continue
                    raise

            if last_exc is not None or resp is None:
                raise RuntimeError("Connection to YouTube API timed out while fetching video details.") from last_exc
        except HttpError as e:
            raise RuntimeError(f"Error fetching video details: {e}") from e

        for item in resp.get("items", []):
            yt_id = item["id"]
            if yt_id in seen_videos:
                continue
            seen_videos.add(yt_id)
            videos.append(
                {
                    "youtube_id": yt_id,
                    "title": item["snippet"]["title"],
                    "published_at": item["snippet"]["publishedAt"][:10],
                    "privacy": item["status"]["privacyStatus"],
                }
            )

    for v in videos:
        privacy = v.get("privacy", "")
        title = v.get("title", "")
        is_draft = privacy == "private" and (
            (not title) or title.startswith("Video uploaded") or title == "Untitled"
        )
        v["is_draft"] = is_draft
        v["is_unlisted"] = privacy == "unlisted"
        v["studio_url"] = f"https://studio.youtube.com/video/{v['youtube_id']}/edit"

    privacy_order = {"private": 0, "unlisted": 1, "public": 2}
    videos.sort(key=lambda v: privacy_order.get(v.get("privacy", ""), 3))

    return videos


def get_live_video_metadata(youtube: Any, youtube_id: str) -> Dict[str, Any] | None:
    yt_upload = _import_youtube_upload()
    return yt_upload.get_live_video_metadata(youtube, youtube_id)


def push_metadata_update(
    youtube: Any,
    youtube_id: str,
    title: str,
    description: str,
    tags_str: str,
    *,
    privacy: str | None = None,
    category: str = "19",
    made_for_kids: bool = False,
    license: str = "youtube",
    embeddable: bool = True,
    public_stats: bool = True,
    default_language: str = "en",
    audio_language: str = "en",
    paid_promo: bool = False,
) -> bool:
    yt_upload = _import_youtube_upload()
    return yt_upload.push_metadata_update(
        youtube,
        youtube_id,
        title,
        description,
        tags_str,
        privacy=privacy,
        category=category,
        made_for_kids=made_for_kids,
        license=license,
        embeddable=embeddable,
        public_stats=public_stats,
        default_language=default_language,
        audio_language=audio_language,
        paid_promo=paid_promo,
    )


def bulk_privacy_change(
    youtube: Any,
    videos: List[Dict[str, Any]],
    *,
    new_privacy: str,
    filter_privacy: str | None = None,
    dry_run: bool = False,
    sleep_seconds: float = 0.5,
    progress_cb: Callable[[int, int, Dict[str, Any], str], None] | None = None,
) -> Dict[str, int]:
    """Bulk-update privacy for eligible videos.

    This is prompt-free and intended to be called by CLI/GUI wrappers.
    Skips videos marked as unlisted/draft (those generally need Studio workflows).
    """
    if new_privacy not in ("public", "private", "unlisted"):
        raise ValueError("new_privacy must be one of: public, private, unlisted")

    if filter_privacy and filter_privacy != "all":
        targets = [v for v in videos if v.get("privacy") == filter_privacy]
    else:
        targets = list(videos)

    skipped = [v for v in targets if v.get("is_unlisted") or v.get("is_draft")]
    targets = [v for v in targets if not v.get("is_unlisted") and not v.get("is_draft")]

    updated = 0
    errors = 0
    total = len(targets)

    import time as _time

    for i, v in enumerate(targets, 1):
        if progress_cb:
            progress_cb(i, total, v, "starting")

        if dry_run:
            if progress_cb:
                progress_cb(i, total, v, "dry_run")
            updated += 1
            continue

        try:
            ok = push_metadata_update(
                youtube,
                v["youtube_id"],
                v.get("title", ""),
                v.get("description", ""),
                "",
                privacy=new_privacy,
            )
            if ok:
                v["privacy"] = new_privacy
                updated += 1
                if progress_cb:
                    progress_cb(i, total, v, "updated")
            else:
                errors += 1
                if progress_cb:
                    progress_cb(i, total, v, "failed")
        except Exception:
            errors += 1
            if progress_cb:
                progress_cb(i, total, v, "error")

        if sleep_seconds:
            _time.sleep(sleep_seconds)

    return {
        "total": len(videos),
        "eligible": total,
        "updated": updated,
        "errors": errors,
        "skipped": len(skipped),
    }
