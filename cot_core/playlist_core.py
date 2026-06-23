import csv
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class ChannelVideo:
    video_id: str
    title: str
    published_at: str = ""


@dataclass
class Suggestion:
    source: str  # 'channel' | 'queue'
    key: str     # video_id or queue row key
    title: str
    confidence: str  # 'do_fit' | 'may_fit'
    reason: str


def _norm_tokens(s: str) -> List[str]:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    toks = [t for t in s.split() if t]
    return toks


def suggest_from_channel(*, playlist_title: str, videos: Sequence[ChannelVideo]) -> List[Suggestion]:
    """Lightweight heuristic suggestions based on playlist title tokens."""
    ptoks = _norm_tokens(playlist_title)
    out: List[Suggestion] = []
    if not ptoks:
        return out

    for v in videos:
        vtoks = _norm_tokens(v.title)
        if not vtoks:
            continue

        overlap = [t for t in ptoks if t in vtoks]
        if not overlap:
            continue

        if len(overlap) >= max(1, min(2, len(ptoks))):
            conf = "do_fit"
        else:
            conf = "may_fit"

        out.append(
            Suggestion(
                source="channel",
                key=v.video_id,
                title=v.title,
                confidence=conf,
                reason=f"Title contains: {', '.join(overlap[:6])}",
            )
        )

    return out


def load_queue_csv(path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    if not path or not os.path.isfile(path):
        return [], []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def suggest_from_queue_csv(
    *,
    playlist_title: str,
    rows: Sequence[Dict[str, str]],
    title_field: str,
) -> List[Suggestion]:
    ptoks = _norm_tokens(playlist_title)
    out: List[Suggestion] = []
    if not ptoks:
        return out

    for i, r in enumerate(rows, 1):
        title = (r.get(title_field) or "").strip()
        if not title:
            continue
        vtoks = _norm_tokens(title)
        overlap = [t for t in ptoks if t in vtoks]
        if not overlap:
            continue
        conf = "do_fit" if len(overlap) >= max(1, min(2, len(ptoks))) else "may_fit"
        out.append(
            Suggestion(
                source="queue",
                key=str(i),
                title=title,
                confidence=conf,
                reason=f"Title contains: {', '.join(overlap[:6])}",
            )
        )
    return out
