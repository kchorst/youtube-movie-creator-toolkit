# ------------------------------------------------------------
# youtube_meta.py
# Version: 2.5.0
#
# Changes from 2.4.0:
#   - All config now read from cot_config.py
#   - LLM fully optional: manual_only hides T/D/B/S menus
#   - Garbled output detection + auto-retry
#   - Character counts on title and description
#   - Seed notes saved/loaded per folder (seeds.json)
#   - Part 2/3 auto-detection from folder name
#   - Draft/unlisted warnings with YouTube Studio links
#   - Bulk privacy change in UC6
#   - After-push re-fetch to confirm fields saved
#   - Dry run mode in UC6
#   - Auto-backup CSV before any write
#
# Changes from 2.1.0:
#   - Fix LOCAL_LLM_BASE_URL to 127.0.0.1 (localhost was not resolving)
#   - Fix MODEL_NAME to phi-3-mini-4k-instruct
#   - Startup LLM check: queries /v1/models, lists available models,
#     lets user confirm or switch before proceeding
#   - Better error messages throughout with actionable hints
#   - _fetch_all_channel_videos: use uploads playlist (search() misses private videos)
#   - Videos sorted private -> unlisted -> public in UC6 list
#
# Changes from 1.5.1:
#   - Full CSV rewrite to match YouTube Data API requirements
#   - New columns: video_file_silent, video_file_music, category,
#     privacy, made_for_kids, license, thumbnail, comments, publish_time
#   - Privacy auto-detected: public if music+metadata, else private
#   - Thumbnail: thumbnail.jpg > final.jpg > blank
#   - video_file derived from folder name, flagged if MP4 missing
#   - Three modes: A=one by one, B=selective (tkinter browse), C=batch
#   - Batch mode: unseeded, auto-accept, no prompts
#   - batch_mode flag on generate_metadata_for_folder() for pipeline use
#
# Purpose:
#   Generate YouTube metadata (title, description, tags) for a
#   travel video folder using a local LLM endpoint.
#   Writes a YouTube Data API-ready CSV for youtube_upload.py.
#
# Usage:
#   python youtube_meta.py
#
# Future:
#   Called by cot_pipeline.py after make_show.py renders videos.
#
# Dependencies:
#   pip install requests pyreadline3
#   tkinter — bundled with Python on Windows
#
# Local LLM must be running with a model loaded at LOCAL_LLM_BASE_URL.
# ------------------------------------------------------------

import os
import csv
import re
import time
import requests
from cot_core.local_llm import chat_url_from_base, discover_local_llm, get_models as get_local_models, post_chat
import json
import tkinter as tk
from tkinter import filedialog
from googleapiclient.errors import HttpError
import argparse

# Force UTF-8 stdout on Windows
import sys as _sys_utf8
if _sys_utf8.platform == "win32" and hasattr(_sys_utf8.stdout, "reconfigure"):
    try:
        _sys_utf8.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass


# Inline editing with pre-populated fields
try:
    import readline
    HAS_READLINE = True
except ImportError:
    try:
        from pyreadline3 import Readline
        readline = Readline()
        HAS_READLINE = True
    except ImportError:
        HAS_READLINE = False


# ------------------------------------------------------------
# CONFIGURATION — loaded from cot_config.json via cot_config.py
# Run cot_pipeline.py → ADMIN to edit settings.
# ------------------------------------------------------------

import sys as _sys
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in _sys.path:
    _sys.path.insert(0, _SCRIPTS_DIR)

try:
    import cot_config as _cfg
    _cfg.load()
except ImportError:
    _cfg = None

def _c(key, default=None):
    """Read a config value, fall back to default."""
    return _cfg.get(key, default) if _cfg else default

# Paths — read at call time so wizard changes take effect
def PICTURES_DIR():    return _c("PICTURES_DIR", "")
def OUTPUT_DIR():      return _c("OUTPUT_DIR", "")
def CSV_PATH():        return _c("CSV_PATH", "")
def SCRIPTS_DIR():     return _c("SCRIPTS_DIR", os.path.dirname(os.path.abspath(__file__)))

# LLM
def LOCAL_LLM_BASE_URL():
    return _c("LOCAL_LLM_BASE_URL", "") or _c("LMSTUDIO_URL", "")
def LLM_MODE():
    mode = _c("LLM_MODE", "manual_only")
    return "local_llm" if mode == "lmstudio_local" else mode
def LLM_AVAILABLE():
    return LLM_MODE() == "local_llm"

# Session-level model override
_MODEL_NAME_OVERRIDE = ""

def MODEL_NAME():
    return _MODEL_NAME_OVERRIDE or _c("MODEL_NAME", "")

def _set_model(name):
    global _MODEL_NAME_OVERRIDE
    _MODEL_NAME_OVERRIDE = name


# Confirmed flag — avoids re-prompting per folder in a session
_LLM_CONFIRMED = False

# Max tokens
MAX_TOKENS_HEARTBEAT  = 5
MAX_TOKENS_TITLE_DESC = 400
MAX_TOKENS_TAGS       = 120
CALL_DELAY_SECONDS    = 1.5

# YouTube defaults — read from config
def YT_CATEGORY():         return _c("YT_CATEGORY", "19")
def YT_COMMENTS():         return _c("YT_COMMENTS", "allow")
def YT_KIDS():             return _c("YT_KIDS", False)
def YT_LICENSE():          return _c("YT_LICENSE", "youtube")
def YT_PUBLISH():          return _c("YT_PUBLISH", "immediate")
def YT_EMBEDDABLE():       return _c("YT_EMBEDDABLE", True)
def YT_PUBLIC_STATS():     return _c("YT_PUBLIC_STATS", True)
def YT_PAID_PROMO():       return _c("YT_PAID_PROMO", False)
def YT_DEFAULT_LANGUAGE(): return _c("YT_LANGUAGE", "en")
def YT_AUDIO_LANGUAGE():   return _c("YT_AUDIO_LANGUAGE", "en")
def FIXED_TAGS():
    if _cfg: return _cfg.get_fixed_tags()
    return ["travel", "video"]

def CHANNEL_NAME():
    return _c("CHANNEL_NAME", "")

def LLM_VOICE_STYLE():
    return _c("LLM_VOICE_STYLE", "")

# CSV columns — YouTube Data API ready
CSV_FIELDS = [
    "video_file_silent",    # Full path to folder_name.mp4 or blank
    "video_file_music",     # Full path to folder_name_music.mp4 or blank
    "title",                # LLM generated
    "description",          # LLM generated
    "tags",                 # LLM generated + fixed tags
    "category",             # 19 = Travel & Events
    "privacy",              # public if music+metadata complete, else private
    "made_for_kids",        # yes
    "license",              # youtube
    "thumbnail",            # Full path to thumbnail.jpg > final.jpg > blank
    "location",             # Extracted from folder name
    "comments",             # allow
    "publish_time",         # immediate
    "folder_name",          # Internal — pipeline tracking
]


# ------------------------------------------------------------
# FOLDER HELPERS
# ------------------------------------------------------------

def is_year_folder(name):
    """Check if folder name starts with a 4-digit year (e.g. '2023 Beijing China')."""
    parts = name.split()
    if not parts:
        return False
    return re.match(r"^\d{4}$", parts[0]) is not None


def extract_location(folder_name):
    """
    Extract the location from the folder name.
    Strips leading year if present (e.g. '2023 Beijing China' -> 'Beijing China').
    Also strips trailing part indicators (Part 2, pt2, (2), etc.)
    """
    parts = folder_name.split()
    if is_year_folder(folder_name):
        parts = parts[1:]
    location = " ".join(parts) if parts else folder_name
    return location


def extract_part_number(folder_name):
    """
    Detect part number from folder name.
    Handles: Part 2, Part2, pt2, (2), - 2
    Returns int or None.
    """
    patterns = [
        r'[Pp]art\s*(\d+)',
        r'pt\.?\s*(\d+)',
        r'\((\d+)\)\s*$',
        r'\s+-\s+(\d+)\s*$',
    ]
    for pat in patterns:
        m = re.search(pat, folder_name)
        if m:
            return int(m.group(1))
    return None


def get_subfolders(root):
    """
    Return sorted list of subfolder paths under root.
    Year folders sorted by year first, then non-year folders alphabetically.
    Excludes 'exclude' and '_temp_frames' folders.
    """
    EXCLUDE_NAMES = {"exclude", "_temp_frames", "YouTubeVideos"}
    try:
        items = os.listdir(root)
    except OSError:
        return []

    folders = [
        f for f in items
        if os.path.isdir(os.path.join(root, f))
        and f.lower() not in EXCLUDE_NAMES
    ]

    year_folders = sorted(
        [f for f in folders if is_year_folder(f)],
        key=lambda x: int(x.split()[0])
    )
    non_year = sorted([f for f in folders if not is_year_folder(f)])
    return [os.path.join(root, f) for f in year_folders + non_year]


# ------------------------------------------------------------
# VIDEO / THUMBNAIL FILE DETECTION
# ------------------------------------------------------------

def get_video_paths(folder_name):
    """
    Derive silent and music MP4 paths from folder name.
    Returns (silent_path_or_blank, music_path_or_blank, missing_warning).
    Flags in warning if expected files are missing.
    """
    silent_name = folder_name + ".mp4"
    music_name  = folder_name + "_music.mp4"
    silent_path = os.path.join(OUTPUT_DIR(), silent_name)
    music_path  = os.path.join(OUTPUT_DIR(), music_name)

    silent_exists = os.path.isfile(silent_path)
    music_exists  = os.path.isfile(music_path)

    warnings = []
    if not silent_exists and not music_exists:
        warnings.append(f"  WARNING: No MP4 found for '{folder_name}' — metadata saved, video_file blank.")

    return (
        silent_path if silent_exists else "",
        music_path  if music_exists  else "",
        warnings
    )


def get_thumbnail_path(folder_path):
    """
    Find thumbnail for this folder.
    Priority: thumbnail.jpg > final.jpg > blank
    Returns full path or empty string.
    """
    def _find_first(name: str) -> str:
        try:
            for root_dir, dirnames, filenames in os.walk(folder_path):
                dirnames[:] = [d for d in dirnames if d.lower() != "exclude" and not d.startswith(".")]
                for f in filenames:
                    if f.lower() == name:
                        p = os.path.join(root_dir, f)
                        return p if os.path.isfile(p) else ""
        except Exception:
            return ""
        return ""

    p = _find_first("thumbnail.jpg")
    if p:
        return p
    p = _find_first("final.jpg")
    if p:
        return p
    return ""


def get_privacy(folder_name, has_metadata):
    """
    Determine privacy setting.
    Public only if music MP4 exists AND metadata is being written now.
    Private otherwise — keeps unfinished videos out of public view.
    """
    music_path = os.path.join(OUTPUT_DIR(), folder_name + "_music.mp4")
    if os.path.isfile(music_path) and has_metadata:
        return "public"
    return "private"


# ------------------------------------------------------------
# SEED NOTES INPUT
# ------------------------------------------------------------

def get_seed_notes():
    """
    Prompt the user for optional eyewitness seed notes.
    Seeds are the real story — the kittens, not the Eiffel Tower.
    Specific things the Cats saw, did, ate, noticed, or conspicuously
    did NOT find. Type freely, blank line to finish, just Enter to skip.
    """
    print("\n  Seed notes — what the Cats actually saw, did, noticed, or didn't find.")
    print("  These are the kittens. The landmarks are the backdrop.")
    print("  Blank line to finish. Just Enter to skip.\n")

    lines = []
    while True:
        try:
            line = input("  > ")
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line.strip())

    notes = " ".join(lines).strip()
    if notes:
        print(f"\n  Seeds locked: {notes}\n")
    else:
        print("  No seeds — using LLM location knowledge only.\n")

    return notes


# ------------------------------------------------------------
# INLINE EDITOR — pre-populates field for in-place editing
# ------------------------------------------------------------

def input_with_prefill(prompt, prefill=""):
    """
    Show an input prompt with existing text pre-filled.
    User edits in place using arrow keys and backspace.
    Requires pyreadline3 on Windows.
    Falls back gracefully if pyreadline3 is not available.
    """
    if HAS_READLINE:
        def hook():
            readline.insert_text(prefill)
            readline.redisplay()
        readline.set_pre_input_hook(hook)
        try:
            result = input(prompt)
        finally:
            readline.set_pre_input_hook(None)
        return result
    else:
        print(f"  Current: {prefill}")
        new_val = input(prompt).strip()
        return new_val if new_val else prefill


# ------------------------------------------------------------
# TKINTER FOLDER BROWSER — matches make_show.py pattern
# ------------------------------------------------------------

def browse_folders(root):
    """
    Open a tkinter dialog to select specific subfolders.
    Returns list of selected folder paths.
    Uses a simple listbox since tkinter has no multi-folder dialog.
    """
    subfolders = get_subfolders(root)
    if not subfolders:
        print("  No subfolders found.")
        return []

    selected = []

    def on_select():
        indices = listbox.curselection()
        for i in indices:
            selected.append(subfolders[i])
        win.destroy()

    def on_cancel():
        win.destroy()

    win = tk.Tk()
    win.title("Select folders for metadata generation")
    win.geometry("600x400")

    tk.Label(win, text="Select folders (Ctrl+click for multiple):").pack(pady=5)

    existing = load_existing_folders()

    frame = tk.Frame(win)
    frame.pack(fill=tk.BOTH, expand=True, padx=10)

    scrollbar = tk.Scrollbar(frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    listbox = tk.Listbox(frame, selectmode=tk.MULTIPLE,
                         yscrollcommand=scrollbar.set, font=("Courier", 10))
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=listbox.yview)

    for path in subfolders:
        name = os.path.basename(path)
        tag  = "  [done]" if name in existing else ""
        listbox.insert(tk.END, f"{name}{tag}")

    btn_frame = tk.Frame(win)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="Select", command=on_select, width=12).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_frame, text="Cancel", command=on_cancel, width=12).pack(side=tk.LEFT, padx=5)

    win.mainloop()
    return selected


# ------------------------------------------------------------
# LLM HEARTBEAT CHECK
# ------------------------------------------------------------

def llm_alive():
    """Quick ping to verify the configured Local LLM endpoint responds."""
    try:
        payload = {
            "model": MODEL_NAME(),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": MAX_TOKENS_HEARTBEAT,
        }
        data = post_chat(LOCAL_LLM_BASE_URL(), payload, timeout=10)
        return "choices" in data or "response" in data or "content" in data
    except Exception:
        return False


def get_available_models():
    """Query a Local LLM /v1/models endpoint."""
    endpoint = LOCAL_LLM_BASE_URL()
    if not endpoint:
        status = discover_local_llm("")
        endpoint = status.chat_url if status.ok else ""
    if not endpoint:
        return []
    try:
        return get_local_models(endpoint, timeout=5)
    except Exception as e:
        print(f"  ERROR: Could not fetch Local LLM models: {e}")
        return []


def check_and_confirm_model(interactive=True):
    """Provider-neutral Local LLM startup check. Returns False without blocking manual metadata mode."""
    print(f"\n  Checking Local LLM endpoint: {LOCAL_LLM_BASE_URL() or 'auto-detect'} ...")

    endpoint = LOCAL_LLM_BASE_URL()
    status = discover_local_llm(endpoint)
    if not status.ok:
        print("\n  Local LLM not available. AI regeneration will be disabled, but manual editing still works.")
        if status.error:
            print(f"  Last error: {status.error}")
        print("  Start llama-server, LM Studio, or Ollama if you want AI-assisted metadata.")
        return False

    models = list(status.models)
    print(f"  {status.provider} reachable. Models available: {len(models)}")
    for i, m in enumerate(models, 1):
        marker = "  <-- current config" if m == MODEL_NAME() else ""
        print(f"    {i}. {m}{marker}")

    if MODEL_NAME() in models:
        print(f"\n  Using model: {MODEL_NAME()}")
        if interactive:
            try:
                ans = input("  Press Enter to continue, or type a number to switch model: ").strip()
            except EOFError:
                ans = ""
            if ans.isdigit():
                idx = int(ans) - 1
                if 0 <= idx < len(models):
                    _set_model(models[idx])
                    print(f"  Switched to: {MODEL_NAME()}")
    else:
        if interactive and models:
            print(f"\n  Configured model '{MODEL_NAME()}' is not listed by the endpoint." if MODEL_NAME() else "\n  No model configured.")
            print("  Select one of the available models, or Q to continue without AI:")
            while True:
                try:
                    ans = input("  Enter number (or Q): ").strip()
                except EOFError:
                    ans = "Q"
                if ans.upper() == "Q":
                    return False
                if ans.isdigit():
                    idx = int(ans) - 1
                    if 0 <= idx < len(models):
                        _set_model(models[idx])
                        print(f"  Using model: {MODEL_NAME()}")
                        break
                print("  Please enter a valid number or Q.")
        elif models:
            _set_model(models[0])
            print(f"  Auto-switched to: {MODEL_NAME()}")
        else:
            print("  Endpoint returned no models.")
            return False

    if not llm_alive():
        print(f"\n  ERROR: Model '{MODEL_NAME()}' did not respond to test ping.")
        return False

    global _LLM_CONFIRMED
    _LLM_CONFIRMED = True
    print(f"  Model ready: {MODEL_NAME()}\n")
    return True


def call_llm(prompt, max_tokens):
    """Send a prompt to a Local LLM and return text from common response formats."""
    payload = {
        "model": MODEL_NAME(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.9,
        "max_tokens": max_tokens,
    }
    data = post_chat(LOCAL_LLM_BASE_URL(), payload, timeout=180)

    text = ""
    if "choices" in data and len(data["choices"]) > 0:
        choice = data["choices"][0]
        if "message" in choice and "content" in choice["message"]:
            text = choice["message"]["content"]
        elif "delta" in choice and "content" in choice["delta"]:
            text = choice["delta"]["content"]
        elif "text" in choice:
            text = choice["text"]
    elif "output_text" in data:
        text = data["output_text"]
    elif "content" in data:
        text = data["content"]
    elif "response" in data:
        text = data["response"]
    else:
        text = json.dumps(data)
    return str(text).strip()


# ------------------------------------------------------------
# PROMPTS
# ------------------------------------------------------------

TWAIN_VOICE = """
=== STYLE TARGET: MARK TWAIN'S TRAVEL JOURNALS ===
You have read Innocents Abroad, Roughing It, A Tramp Abroad, and Following the
Equator. Write like that. That voice — worldly, dry, seasoned, amused by everything,
blinded by nothing. When in doubt, ask: would Twain have written this sentence?
If it sounds like a brochure, he would not have.

=== THE CATS ===
The traveler is "the Cats" or "Cats of Travels".
NEVER use pronouns — no he, she, they, their, them. Always "the Cats" or "Cats of Travels".
The Cats is a seasoned traveler who notices what others walk past. Cat references are
subtle — one light touch per piece at most. "everycat" for everyone is welcome.

=== THE CATS OF TRAVELS PHILOSOPHY ===
The Cats travels specifically to find what the guidebook missed, ignored, or considered
beneath mention. The famous landmark is the address — it tells you where the Cats
was standing. The real story is always the thing next to it, behind it, underneath it,
or warning you about it.

The technique: lead with either the landmark OR the Cats detail — but always collide
them. The juxtaposition IS the writing.

Examples of the technique:
- The Great Reclining Buddha is grand and enormous — someone has also seen fit to post
  a pickpocket warning on his feet, which the Cats found the most honest introduction
  to the place one could ask for.
- The Forbidden City loomed large and imperial. A Starbucks has since taken up
  residence within its walls, which the eunuchs, had any remained, might have found
  surprising.
- Everest looms somewhere above, no doubt. The Cats was more taken with the card
  games between sherpas at midnight, the momos arriving with chili oil, and a smile
  of improbable dental perfection from the driver.

The seeds provided by the user ARE the kittens — the basket of kittens napping
outside the boulangerie while the Eiffel Tower stands ignored in the background.
Build the writing around the seeds. Use landmarks as backdrop, not centrepiece.

=== NO PASSIVE VOICE ===
Every sentence needs a subject doing something. The Cats acts — never is acted upon.
WRONG: "One finds oneself drawn to the Forbidden City"
RIGHT: "The Cats wandered into the Forbidden City"
WRONG: "The market is full of curious smells"
RIGHT: "The market announced itself from two streets away"
WRONG: "One might find chicken heart skewers"
RIGHT: "The Cats found chicken heart skewers and demolished them"
Never use: one finds, one might, one cannot, there is, there are, it was, to be found.

=== VERBS — MAKE THEM WORK ===
Verbs carry the sentence. Never settle for is, was, went, saw, had when a better
verb exists. Choose verbs that move, push, pull, loom, sprawl, dispatch, bristle.

Preferred verbs:
- Movement: prowled, threaded, wound through, descended upon, pressed on, stomped,
  picked a path through, headed, clambered
- Eating: demolished, dispatched, made short work of, set upon, annihilated, worked
  through, got to grips with
- Seeing: laid eyes on, came upon, spotted, caught sight of, regarded, inspected,
  squinted at, pulled up short at
- Grandeur: loomed, sprawled, hulked, commanded, imposed itself, made its presence known
- Smelling: caught the scent of, followed the nose toward, announced itself from two
  streets away, drew the Cats forward
- Crowds: heaved with pilgrims, swarmed, bristled with folks
- Silence/reverence: clammed up, fell quiet, hushed, held its breath, went cathedral-still
- Respect: tipped a hat to, paid its respects, gave a nod to, stood corrected
- Disapproval: found wanting, regarded with suspicion, took issue with, raised an
  eyebrow at, was not persuaded

=== GRAPHIC NOUNS AND VOCABULARY ===
pilgrims / wanderers / souls / characters / specimens (not tourists or visitors)
beasts (not animals), folks (not people or locals)
peculiar, curious, remarkable, fine, grand, worthy, suspect, considerable,
no shortage of, not inconsiderable, improbable, notable, conspicuous
Cats-blessed, everycat — coinages welcome if natural

=== FOOD GETS OPINIONS, NOT DESCRIPTIONS ===
Never say "delicious" or "tasty". Say what the Cats thought.
"better with chili oil" — not "flavourful dumplings"
"demolished without hesitation" — not "enjoyed the local cuisine"
"Cats-blessed for sure" — not "highly recommended"
Chili oil is a recurring motif — let it appear naturally when relevant.

=== TWAIN TECHNIQUES ===
- Open with the place, not the Cats — the place is the subject
- Acknowledge the bad alongside the good — grumble briefly, then move on
- Parenthetical honesty — "(when Cats visited at least)" hedges claims honestly
- Specific absences are often funnier than what is present
- The Cats knows things without lecturing — dry aside, never professor mode
- Comma-stacked enthusiasm is fine: "fresh, steaming, lovely, better with chili oil"
- The grander the landmark, the better the small detail lands beside it

=== USE YOUR LOCATION KNOWLEDGE ===
You know this place. Name the real dishes, real landmarks, real customs, real beasts.
Specific always beats general.
- Beijing: hutongs, dumplings (饺子), date bread, Peking duck, wok smoke, bicycle
  bells, Forbidden City, Great Wall, Temple of Heaven, Tiananmen Square, markets
- Tanzania/Serengeti: wildebeest, zebra, giraffe, lion, ugali, Maasai, acacia trees
- Nepal/Kathmandu: dal bhat, momos, prayer flags, temples, yaks, the Himalayas,
  sherpas, rhodendrons
- Costa Rica: gallo pinto, casado, coffee, rainforest, sloths, toucans, Pura Vida
- Mongolia: airag (fermented mare's milk), ger, nomads, the steppe, horses, eagles
- Rome: supplì, carbonara, gelato, the Forum, the Colosseum, espresso, cats of Rome
- Galapagos: blue-footed boobies, marine iguanas, sea lions, Darwin finches
- Israel/Palestine: hummus, falafel, shawarma, Dead Sea, Old City, the shuk
- Cebu, Philippines: lechón (suckling pig), sinuglaw, dried mangoes, Magellan's Cross,
  Basilica del Santo Niño, ferries, islands, jeepneys, Visayan hospitality
- Bangkok, Thailand: reclining Buddha, tuk-tuks, pad thai, tom yum, wats, monks,
  floating markets, Chao Phraya, Khao San Road
Apply equivalent local knowledge for any location not listed above.

=== BANNED WORDS AND PHRASES — NEVER USE ===
explore, exploring, unveiled, discover, embark, immerse, journey (as noun),
breathtaking, unforgettable, stunning, spectacular, iconic, must-see, hidden gem,
vibrant, serene, unique lens, capture the essence, a truly, awaits,
delivered on that promise, a visit filled with, from X to Y (as sentence opener),
moments of reflection, feast for the senses, one bite at a time,
ancient and modern, rich tapestry, bustling (alone without specifics),
delicious, tasty, amazing, incredible (unless comma-stacked for comic effect),
one finds, one might, one cannot, there is, there are

=== CLOSERS — fresh every time, never repeat ===
Pick a closer that fits this specific location and what happened there.
These are examples, not templates — vary them, invent new ones:
- "Come along with the Cats to [location] — everycat should visit at least once."
- "Have another look at [location] through the Cats' eyes."  (part 2+ only)
- "See if you like this trip as much as the Cats did."
- "If you like what you hear, [location] is worth the trip."
- "Follow the channel for more."
- Or invent something that fits — the Cats never ends the same way twice.
"""


def _voice_block():
    try:
        import cot_config as _cfg_local
        _cfg_local.load(gui_mode=True)
        v = (_cfg_local.get("LLM_VOICE_STYLE", "") or "").strip()
        return v if v else TWAIN_VOICE
    except Exception:
        return TWAIN_VOICE


def _examples_block():
    try:
        import cot_config as _cfg_local
        _cfg_local.load(gui_mode=True)
        ex = (_cfg_local.get("LLM_EXAMPLES_BLOCK", "") or "").strip()
        return ex if ex else FEWSHOT_EXAMPLES
    except Exception:
        return FEWSHOT_EXAMPLES


def _channel_line():
    try:
        import cot_config as _cfg_local
        _cfg_local.load(gui_mode=True)
        name = (_cfg_local.get("CHANNEL_NAME", "") or "").strip()
        if name:
            return f"You are writing YouTube metadata for the channel {name}."
    except Exception:
        pass
    return "You are writing YouTube metadata for a YouTube channel."

FEWSHOT_EXAMPLES = """
=== REAL EXAMPLES — MATCH THIS VOICE EXACTLY ===

Location: Costa Rica
TITLE:
PURA VIDA! Costa Rica with Cats of Travels
DESCRIPTION:
If you, like Cats of Travels, love all things tropical, coffee, beaches, green, and rainforest-y, you will love Costa Rica. Come along for a visit with the Cats to this fantastic country.

Location: Tanzania / Serengeti
TITLE:
Tanzania and the Serengeti - Cats of Travels meets the beasts
DESCRIPTION:
This is one of several videos of Cats in Tanzania, and this episode is a record of a visit to the famous Serengeti. Cats meets zebra, giraffe, egret, wildebeest, elephants, buffalo, hippo, lion and much more.

Location: Kathmandu, Nepal
TITLE:
Cats of Travels at the top of the world: Kathmandu, Nepal - Part 2 of 2
DESCRIPTION:
Kathmandu has a special place in the Cats of Travels' heart because of the "kat" in the name! Seriously, this place is a fascinating, incredible, beautiful, and worthwhile visit while in the area and the Cats really had a great time.

Location: Jomsom Trek, Nepal
TITLE:
Hike the Jomsom Trek, Nepal with Cats and friends!
DESCRIPTION:
Cats of Travels loves heights, so off to Jomsom to experience and walk first-paw the Annapurna and Mustang regions of Nepal. On the multi-day trek, Cats passes through several villages with the ever-present hard-working donkeys, porters, and kind, attentive hosts of all the cozy inns.

Location: Rome, Italy
TITLE:
Bella Roma! Cats of Travels puts paws into Roman History with a visit to this awesome citta - ROMA.
DESCRIPTION:
If you are like Cats of Travels, you too will enjoy stepping out and around Rome, Italy. There is something for everycat: architecture, food, history, fountains, statues, art, and much much more. Have a look through Cats' eyes!

Location: Mongolia / Ulaanbaatar
TITLE:
More Mongolia - Ulaan Baataar with Cats of Travels
DESCRIPTION:
Something about Mongolia resonates with the Cats of Travels! Is it the nomadic life? Is it the beasts (sheep, goats, ponies, birds of prey, dogs, and fellow Cats?). Is it the fermented mare's milk? Well, the fact is, it's all of the above! Have another look at Mongolia through the Cats' eyes.

Location: Galapagos Islands
TITLE:
Galapagos! Come with the Cats of Travels to meet undisturbed birds and beasts!
DESCRIPTION:
Cats of Travels loves fellow birds and beasts and a visit to the Galapagos Islands is just the thing because it combines boats, water, flora, fauna, and tons of awesome sights. See if you like this trip as much as the Cats did!

Location: Israel and Palestine
TITLE:
Cats of Travels in Israel and Palestine: Red, Med, and Dead Seas Tour - part 2 of 2
DESCRIPTION:
The eastern Mediterranean has a strong hold on the Cats because of gorgeous beaches in salt, fresh, and DEAD water, great food, history, architecture, and a wide variety of fellow beasts (cats, dogs, donkeys, camels, goats, ponies, chickens, peacocks, and many more). See if you can recognize the various places the Cats' paws have been.

Location: Beijing, China (gold standard — user-edited)
TITLE:
Where are the Cats Today? With Bicycles and Steamed Buns in Beijing!
DESCRIPTION:
Ah, Beijing — city of hutongs and, unfortunately, the threat of development, but still, where bicycle bells ring over the hum of progress. There is the Forbidden City, yes, with a small Starbucks (when Cats visited at least) but no eunuchs or dowager queens. The food and shopping markets enticed the Cats with smells and sights, and Cats feasted on chicken heart skewers. And the dumplings? Fresh, steaming, lovely, better with chili oil, and Cats-blessed for sure. If you like what you hear, visit Beijing — everycat should visit at least once.
"""


def build_seed_block(seed_notes):
    """
    Build the seed notes block for injection into prompts.
    Seeds are eyewitness anchors — the kittens, not the Eiffel Tower.
    Returns empty string if no seeds provided.
    """
    if not seed_notes:
        return ""
    return f"""
=== EYEWITNESS SEED NOTES — THE KITTENS ===
These are real observations from the Cats — specific things seen, done, eaten,
noticed, or conspicuously NOT found. They take priority over general assumptions.
The landmarks are the backdrop. The seeds are the real story.
Build the title and description around these. Do not invent things that contradict them.
Fill gaps with your location knowledge and Twain voice.

Seeds: {seed_notes}
"""


def build_title_desc_prompt(location, folder_name, seed_notes=""):
    """
    Build the combined title + description prompt.
    Full Twain ruleset, location knowledge, few-shot examples, seed anchors.
    """
    year = ""
    parts = folder_name.split()
    if is_year_folder(folder_name) and len(parts) > 1:
        year = parts[0]

    year_hint   = f"The video was filmed in {year}." if year else ""
    seed_block  = build_seed_block(seed_notes)

    return f"""
{_channel_line()}

{_voice_block()}

{_examples_block()}

{seed_block}

=== YOUR TASK ===
{year_hint}
Location: {location}

Write a title and description for a video about this location.
Seeds above are anchors — the real story. Landmarks are backdrop.
Use your knowledge of {location} to fill gaps.
No passive voice. Strong verbs. Twain voice throughout.

Return output in this EXACT format, nothing else:

TITLE:
<your title>

DESCRIPTION:
<your description>
"""


def build_regen_title_prompt(location, description, seed_notes=""):
    """Regenerate title only — Twain voice, seeds as anchors."""
    seed_block = build_seed_block(seed_notes)
    return f"""
{_channel_line()}

{_voice_block()}

{seed_block}

Real title examples:
- "PURA VIDA! Costa Rica with Cats of Travels"
- "Tanzania and the Serengeti - Cats of Travels meets the beasts"
- "Bella Roma! Cats of Travels puts paws into Roman History"
- "Galapagos! Come with the Cats of Travels to meet undisturbed birds and beasts!"
- "Where are the Cats Today? With Bicycles and Steamed Buns in Beijing!"

Rules: one line, max 100 characters, no passive voice, no banned phrases,
consistent with the description below.

Location: {location}
Existing description: {description}

Return ONLY:
TITLE:
<one-line title>

DESCRIPTION:
{description}
"""


def build_regen_desc_prompt(location, title, seed_notes=""):
    """Regenerate description only — Twain voice, seeds as anchors."""
    seed_block = build_seed_block(seed_notes)
    return f"""
{_channel_line()}

{_voice_block()}

{seed_block}

Gold standard example:
"Ah, Beijing — city of hutongs and, unfortunately, the threat of development, but
still, where bicycle bells ring over the hum of progress. There is the Forbidden City,
yes, with a small Starbucks (when Cats visited at least) but no eunuchs or dowager
queens. The food and shopping markets enticed the Cats with smells and sights, and
Cats feasted on chicken heart skewers. And the dumplings? Fresh, steaming, lovely,
better with chili oil, and Cats-blessed for sure."

Rules: 3-6 sentences, no passive voice, strong verbs, seeds are anchors,
food gets opinions not descriptions, fresh closer, no hashtags, no emojis,
consistent with the title below.

Location: {location}
Existing title: {title}

Return ONLY:
TITLE:
{title}

DESCRIPTION:
<your description>
"""


def build_tags_prompt(location, title, description):
    """Tags prompt — location-aware, specific, no fluff."""
    return f"""
You are a YouTube SEO expert.

Generate a comma-separated list of YouTube tags for the video below.

Rules:
- 10 to 15 tags total
- Mix broad tags (travel, vlog) with specific ones (named landmarks, dishes, region)
- Include country, city, and region where relevant
- Include activity types if applicable (hiking, food tour, wildlife safari, etc.)
- No hashtags, no quotes, no numbering
- Output ONLY the comma-separated tags — no explanation, no preamble

Location: {location}
Title: {title}
Description: {description}
Tags:"""


# ------------------------------------------------------------
# METADATA PARSERS
# ------------------------------------------------------------

def is_garbled(text, threshold=0.35):
    """
    Detect garbled LLM output.
    Returns True if text looks like word-salad.
    Checks: non-ASCII ratio, average word length, no spaces.
    """
    if not text or len(text) < 20:
        return True
    # Check for error JSON in response
    if '"error"' in text and '"message"' in text:
        return True
    # Non-ASCII character ratio
    non_ascii = sum(1 for c in text if ord(c) > 127)
    if non_ascii / len(text) > 0.1:
        return True
    # Very long words (no spaces = runon garbage)
    words = text.split()
    if not words:
        return True
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len > 18:
        return True
    # Ratio of words that look like garbage (all caps runon, random suffixes)
    garbage_words = sum(1 for w in words if len(w) > 20)
    if len(words) > 5 and garbage_words / len(words) > threshold:
        return True
    return False


def parse_title_desc(text):
    """
    Parse LLM response for TITLE and DESCRIPTION blocks.
    Returns (title, description) or ('', raw_text) if parsing fails.
    """
    title = ""
    description = ""

    if "TITLE:" in text and "DESCRIPTION:" in text:
        parts = text.split("DESCRIPTION:")
        title = parts[0].replace("TITLE:", "").strip()
        description = parts[1].strip()
    else:
        description = text

    return title, description


def parse_tags(text):
    """
    Parse LLM tags response into a clean comma-separated string.
    Strips stray quotes, hashtags, and blank entries.
    Appends FIXED_TAGS, deduplicated case-insensitively.
    """
    raw_tags = [t.strip().lstrip("#").strip('"').strip("'") for t in text.split(",")]
    raw_tags = [t for t in raw_tags if t]

    existing_lower = {t.lower() for t in raw_tags}
    for fixed in FIXED_TAGS():
        if fixed.lower() not in existing_lower:
            raw_tags.append(fixed)

    return " ".join(raw_tags)


def _template_metadata(location: str, folder_name: str):
    loc = (location or "").strip()
    if not loc:
        loc = folder_name.strip()
    title = f"Cats of Travels in {loc}" if loc else "Cats of Travels"
    description = (
        f"Join Cats of Travels in {loc}.\n"
        "\n"
        "Subscribe for more travel videos."
    ) if loc else "Subscribe for more travel videos."
    tags_list = [
        "cats of travels",
        "travel",
        "travel vlog",
        "cats",
    ]
    if loc:
        tags_list.insert(1, loc)
    tags = ", ".join(tags_list)
    return title, description, tags


# ------------------------------------------------------------
# METADATA GENERATION
# ------------------------------------------------------------

def generate_title_desc(location, folder_name, seed_notes=""):
    """
    Call LLM to generate title + description.
    Auto-retries once if output looks garbled.
    """
    print("  Generating title and description...")
    text = call_llm(build_title_desc_prompt(location, folder_name, seed_notes), MAX_TOKENS_TITLE_DESC)
    if is_garbled(text):
        print("  WARNING: Output looks garbled — retrying once...")
        import time as _t; _t.sleep(1.5)
        text = call_llm(build_title_desc_prompt(location, folder_name, seed_notes), MAX_TOKENS_TITLE_DESC)
        if is_garbled(text):
            print("  WARNING: Second attempt also looks garbled.")
            print("  Tip: Try a different model or restart Local LLM.")
    return parse_title_desc(text)


def generate_tags(location, title, description):
    """Call LLM to generate tags (separate call, after a short delay)."""
    print("  Generating tags...")
    time.sleep(CALL_DELAY_SECONDS)
    text = call_llm(build_tags_prompt(location, title, description), MAX_TOKENS_TAGS)
    return parse_tags(text)


# ------------------------------------------------------------
# CSV HELPERS
# ------------------------------------------------------------

def seeds_file():
    """Path to seeds.json."""
    if _cfg:
        return _cfg.get("SEEDS_FILE", "")
    return ""


def load_seeds(folder_name):
    """Load saved seed notes for a folder. Returns '' if none."""
    path = seeds_file()
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get(folder_name, "")
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return ""


def save_seeds(folder_name, notes):
    """Save seed notes for a folder to seeds.json."""
    if not notes:
        return
    path = seeds_file()
    if not path:
        return
    data = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, IOError):
            pass
    data[folder_name] = notes
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except (IOError, OSError) as e:
        print(f"  WARNING: Could not save seeds: {e}")


def load_existing_folders():
    """
    Read the CSV and return a set of folder_names already present.
    Used to skip duplicates before generating anything.
    """
    if not os.path.isfile(CSV_PATH()):
        return set()
    existing = set()
    with open(CSV_PATH(), "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if "folder_name" in row and row["folder_name"]:
                existing.add(row["folder_name"].strip())
    return existing


def backup_csv():
    """
    Auto-backup CSV before any write operation.
    Creates youtube_uploads.csv.bak in same folder.
    """
    path = CSV_PATH()
    if os.path.isfile(path):
        import shutil
        backup = path + ".bak"
        try:
            shutil.copy2(path, backup)
        except (IOError, OSError):
            pass  # backup failure should never block the write


def refresh_thumbnails(root: str, offer_next_step: bool = True):
    """Refresh the CSV 'thumbnail' column by rescanning folders.

    For each row in the existing CSV, compute thumbnail path from the folder on disk:
    thumbnail.jpg (preferred) > final.jpg > blank.
    Updates only the 'thumbnail' field, preserving all other columns.
    """

    csv_path = CSV_PATH()
    if not csv_path or not os.path.isfile(csv_path):
        print("\n  No CSV found — nothing to refresh.")
        return

    if not root or not os.path.isdir(root):
        raise FileNotFoundError(f"Root folder not found: {root}")

    backup_csv()

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print("\n  CSV is empty — nothing to refresh.")
        return

    if "thumbnail" not in fieldnames:
        print("\n  CSV has no 'thumbnail' column — nothing to refresh.")
        return

    updated = 0
    missing = 0

    for row in rows:
        folder_name = (row.get("folder_name") or "").strip()
        if not folder_name:
            continue

        folder_path = os.path.join(root, folder_name)
        if not os.path.isdir(folder_path):
            missing += 1
            continue

        new_thumb = get_thumbnail_path(folder_path)
        old_thumb = (row.get("thumbnail") or "").strip()
        if new_thumb != old_thumb:
            row["thumbnail"] = new_thumb
            updated += 1

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"\n  Thumbnail refresh complete. {updated} updated.")
    if missing:
        print(f"  WARNING: {missing} folder(s) from CSV not found under root.")

    if offer_next_step:
        _offer_next_step()


def append_to_csv(folder_name, folder_path, location, title, description, tags):
    """
    Append a single row to the CSV.
    Creates the file with headers if it doesn't exist yet.
    Auto-backs up CSV before writing.
    Derives video paths, thumbnail, and privacy automatically.
    """
    backup_csv()
    os.makedirs(os.path.dirname(CSV_PATH()), exist_ok=True)
    file_exists = os.path.isfile(CSV_PATH())

    silent_path, music_path, warnings = get_video_paths(folder_name)
    thumbnail   = get_thumbnail_path(folder_path)
    privacy     = get_privacy(folder_name, has_metadata=True)

    # Print any warnings about missing MP4s
    for w in warnings:
        print(w)

    with open(CSV_PATH(), "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "video_file_silent": silent_path,
            "video_file_music":  music_path,
            "title":             title,
            "description":       description,
            "tags":              tags,
            "category":          YT_CATEGORY(),
            "privacy":           privacy,
            "made_for_kids":     YT_KIDS(),
            "license":           YT_LICENSE(),
            "thumbnail":         thumbnail,
            "location":          location,
            "comments":          YT_COMMENTS(),
            "publish_time":      YT_PUBLISH(),
            "folder_name":       folder_name,
        })


# ------------------------------------------------------------
# PUBLIC ENTRY POINT — called by cot_pipeline.py
# ------------------------------------------------------------

def generate_metadata_for_folder(folder_path, batch_mode=False):
    """
    Main public function. Takes a folder path, generates metadata
    interactively or in batch mode, and writes to CSV if accepted.

    Args:
        folder_path: Full path to the picture folder
        batch_mode:  If True — no seeds, no review, auto-accept.
                     Used by cot_pipeline.py for unattended runs.

    Returns:
        (title, description, tags) if accepted
        (None, None, None) if skipped, quit, or LLM unavailable
    """
    folder_name = os.path.basename(folder_path.rstrip("/\\"))
    location    = extract_location(folder_name)

    print(f"\n{'='*60}")
    print(f"  Folder   : {folder_name}")
    print(f"  Location : {location}")
    print(f"{'='*60}")

    # ── Duplicate check ───────────────────────────────────────
    existing = load_existing_folders()
    if folder_name in existing:
        print(f"  [SKIP] Already in CSV.")
        return None, None, None

    # ── LLM check — only run once per session, skip if already confirmed ─
    if LLM_AVAILABLE() and not _LLM_CONFIRMED:
        if not check_and_confirm_model(interactive=(not batch_mode)):
            if batch_mode:
                title, description, tags = _template_metadata(location, folder_name)
                append_to_csv(folder_name, folder_path, location, title, description, tags)
                print(f"  [AUTO] Saved (template): {title}")
                return title, description, tags
            return None, None, None

    # ── Batch mode — generate and auto-accept ─────────────────
    if batch_mode:
        if LLM_AVAILABLE():
            title, description = generate_title_desc(location, folder_name)
            time.sleep(CALL_DELAY_SECONDS)
            tags = generate_tags(location, title, description)
            append_to_csv(folder_name, folder_path, location, title, description, tags)
            print(f"  [AUTO] Saved: {title}")
            return title, description, tags
        else:
            title, description, tags = _template_metadata(location, folder_name)
            append_to_csv(folder_name, folder_path, location, title, description, tags)
            print(f"  [AUTO] Saved (template): {title}")
            return title, description, tags

    # ── Interactive mode ──────────────────────────────────────
    seed_notes = get_seed_notes()

    if LLM_AVAILABLE():
        title, description = generate_title_desc(location, folder_name, seed_notes)
        time.sleep(CALL_DELAY_SECONDS)
        tags = generate_tags(location, title, description)
    else:
        title, description, tags = "", "", ""

    while True:
        print(f"\n{'─'*60}")
        if seed_notes:
            print(f"  SEEDS : {seed_notes}")
            print()
        print("  TITLE:")
        print(f"  {title}")
        print("\n  DESCRIPTION:")
        print(f"  {description}")
        print("\n  TAGS:")
        print(f"  {tags}")
        print(f"{'─'*60}\n")
        print("  [A] Accept and save")
        if LLM_AVAILABLE():
            print("  [T] Regenerate title")
            print("  [D] Regenerate description")
            print("  [B] Regenerate both")
        print("  [E] Edit in place  (title -> description -> tags, Enter to keep each)")
        if LLM_AVAILABLE():
            print("  [S] Edit seed notes and regenerate both")
        print("  [X] Skip this folder")
        print("  [Q] Quit")
        print()

        action = input("  Choose: ").strip().lower()

        if action == "a":
            append_to_csv(folder_name, folder_path, location, title, description, tags)
            print(f"\n  Saved to CSV: {CSV_PATH}\n")
            return title, description, tags

        elif action == "t" and LLM_AVAILABLE():
            print("\n  Regenerating title...")
            text = call_llm(build_regen_title_prompt(location, description, seed_notes), MAX_TOKENS_TITLE_DESC)
            new_title, _ = parse_title_desc(text)
            if new_title:
                title = new_title

        elif action == "d" and LLM_AVAILABLE():
            print("\n  Regenerating description...")
            text = call_llm(build_regen_desc_prompt(location, title, seed_notes), MAX_TOKENS_TITLE_DESC)
            _, new_desc = parse_title_desc(text)
            if new_desc:
                description = new_desc

        elif action == "b" and LLM_AVAILABLE():
            print("\n  Regenerating title and description...")
            title, description = generate_title_desc(location, folder_name, seed_notes)
            time.sleep(CALL_DELAY_SECONDS)
            print("  Regenerating tags...")
            tags = generate_tags(location, title, description)

        elif action == "e":
            print("\n  Edit mode — use arrow keys to edit. Press Enter to keep as-is.\n")
            title       = input_with_prefill("  Title       : ", title)
            description = input_with_prefill("  Description : ", description)
            tags        = input_with_prefill("  Tags        : ", tags)

        elif action == "s" and LLM_AVAILABLE():
            print("\n  Edit seed notes — blank line to finish.\n")
            seed_notes = get_seed_notes()
            print("  Regenerating with new seeds...")
            title, description = generate_title_desc(location, folder_name, seed_notes)
            time.sleep(CALL_DELAY_SECONDS)
            tags = generate_tags(location, title, description)

        elif action == "x":
            print("\n  Skipping this folder.")
            return None, None, None

        elif action == "q":
            print("\n  Quitting.")
            return None, None, None

        else:
            print("  Invalid choice — please enter A, T, D, B, E, S, X, or Q.")


# ------------------------------------------------------------
# STAND-ALONE MODES
# ------------------------------------------------------------

def mode_one_by_one(root):
    """
    Mode A: Process folders one by one, interactive with seeds and review.
    Prompts G/X/Q per folder.
    """
    subfolders = get_subfolders(root)
    if not subfolders:
        print("\n  No subfolders found.")
        return

    existing = load_existing_folders()
    print(f"\n  Found {len(subfolders)} folder(s).\n")

    for folder_path in subfolders:
        folder_name = os.path.basename(folder_path)
        dup_flag    = "  [already in CSV]" if folder_name in existing else ""
        print(f"\n  Folder: {folder_name}{dup_flag}")

        choice = input("  [G] Generate   [X] Skip   [Q] Quit: ").strip().lower()
        if choice == "q":
            print("\n  Quitting.")
            return
        if choice == "x":
            continue
        if choice == "g":
            result = generate_metadata_for_folder(folder_path, batch_mode=False)
            if result[0] is None and result == (None, None, None):
                # Check if user quit from inside the folder
                pass

    # ── Offer handoff to batch metadata after rendering ───────
    print("\n  All folders processed.")
    _offer_next_step()


def mode_selective(root):
    """
    Mode B: Browse dialog to select specific folders, then interactive.
    """
    print("\n  Opening folder selector...")
    selected = browse_folders(root)

    if not selected:
        print("  No folders selected.")
        return

    print(f"\n  {len(selected)} folder(s) selected.\n")

    for folder_path in selected:
        generate_metadata_for_folder(folder_path, batch_mode=False)

    print("\n  Selected folders processed.")
    _offer_next_step()


def mode_batch(root, offer_next_step: bool = True):
    """
    Mode C: Batch — all folders, no seeds, auto-accept, no prompts.
    Skips folders already in CSV.
    Designed for unattended runs — walk away and come back.
    """
    subfolders = get_subfolders(root)
    if not subfolders:
        print("\n  No subfolders found.")
        return

    existing = load_existing_folders()
    pending  = [f for f in subfolders if os.path.basename(f) not in existing]

    print(f"\n  {len(subfolders)} folders total, {len(pending)} need metadata.\n")

    if not pending:
        print("  Nothing to do — all folders already in CSV.")
        return

    done    = 0
    skipped = 0

    for folder_path in pending:
        result = generate_metadata_for_folder(folder_path, batch_mode=True)
        if result[0] is not None:
            done += 1
        else:
            skipped += 1

    print(f"\n  Batch complete. {done} saved, {skipped} skipped.")

    if offer_next_step:
        _offer_next_step()


def _offer_next_step():
    """
    After UC1 (video rendering) or any mode completes, offer to run metadata.
    Called at the end of each mode in standalone use.
    """
    print("\n  What next?")
    print("  M. Generate metadata now")
    print("  Q. Quit")
    choice = input("\n  Choice: ").strip().upper()
    if choice == "M":
        main_metadata_menu()


def main_metadata_menu():
    """Metadata mode sub-menu — batch, interactive, or review/edit live."""
    print("\n  METADATA MODE")
    print("  A. One by one (interactive, seeded)")
    print("  B. Selective  (browse, interactive)")
    print("  C. Batch      (all folders, unseeded, auto-accept)")
    print("  T. Refresh thumbnails in CSV (scan folders, no metadata regen)")
    print("  R. Review & Edit live videos on YouTube")
    print("  Q. Back")
    print()

    choice = input("  Choice: ").strip().upper()
    if choice == "Q":
        return

    if choice == "R":
        mode_review_live()
        return

    root = input(f"\n  Root pictures folder [{PICTURES_DIR()}]: ").strip()
    if not root:
        root = PICTURES_DIR()
    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        return

    if choice == "A":
        mode_one_by_one(root)
    elif choice == "B":
        mode_selective(root)
    elif choice == "C":
        mode_batch(root)
    elif choice == "T":
        refresh_thumbnails(root)


# ------------------------------------------------------------
# MODE R — VIEW, SEARCH & EDIT LIVE YOUTUBE METADATA (UC6)
# ------------------------------------------------------------

def mode_review_live():
    """
    UC6 — View, Search & Edit Live Metadata.
    - Discovers all videos on the channel via YouTube Data API
    - Shows paginated, searchable list of live videos
    - Pick any video to regenerate or edit title/desc/tags
    - Push changes directly to YouTube — no CSV touched
    - Works on ALL channel videos including manually uploaded ones
    """
    # Import youtube_upload here to avoid circular dependency
    # and keep youtube_meta usable without Google API libs
    try:
        import youtube_upload as yt_upload
    except ImportError:
        print("\n  ERROR: youtube_upload.py not found or missing dependencies.")
        print("  Place youtube_upload.py in the same folder as youtube_meta.py")
        print("  and run: pip install google-api-python-client google-auth-oauthlib")
        return

    try:
        from cot_core import live_metadata as _live
    except Exception:
        _live = None

    print("\n  UC6 — VIEW & EDIT LIVE YOUTUBE METADATA")

    # Check Local LLM before auth — needed for T/D/B/S regeneration
    global _LLM_CONFIRMED
    if not _LLM_CONFIRMED:
        if not check_and_confirm_model(interactive=True):
            print("  Local LLM not available — regeneration (T/D/B/S) will be disabled.")
            print("  You can still browse and use E (edit in place) and P (change privacy).")

    print("  Authenticating with YouTube...")

    try:
        if _live is not None:
            youtube = _live.authenticate()
        else:
            youtube = yt_upload.authenticate()
    except SystemExit:
        return
    except HttpError as e:
        print(f"\n  ERROR authenticating with YouTube: {e}")
        return

    print("  Authenticated.\n")
    print("  Fetching channel videos...")

    while True:
        try:
            if _live is not None:
                channel_id = _live.get_channel_id(youtube)
                all_videos = _live.fetch_all_channel_videos(youtube, channel_id)
            else:
                channel_id = _get_channel_id(youtube)
                all_videos = _fetch_all_channel_videos(youtube, channel_id)
            break
        except HttpError as e:
            print(f"\n  ERROR fetching videos from YouTube: {e}")
            return
        except (TimeoutError, OSError, RuntimeError) as e:
            msg = str(e) or e.__class__.__name__
            print("\n  ERROR: Network timeout while fetching channel videos.")
            print(f"  Details: {msg}")
            print("\n  Options:")
            print("    R. Retry")
            print("    Q. Quit")
            c = input("  Choice [R]: ").strip().upper()
            if c == "Q":
                return
            continue

    if not all_videos:
        print("  No videos found on channel.")
        return

    print(f"  Found {len(all_videos)} videos on channel.\n")

    PAGE_SIZE = 10
    search_term = ""
    page = 0
    dry_run = False

    while True:
        # Filter by search term
        filtered = [
            v for v in all_videos
            if search_term.lower() in v["title"].lower()
            or search_term.lower() in v.get("published_at", "")
        ] if search_term else all_videos

        # Paginate
        total_pages = max(1, (len(filtered) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * PAGE_SIZE
        page_videos = filtered[start:start + PAGE_SIZE]

        # Display
        print(f"\n{'─'*65}")
        if search_term:
            print(f"  Filter: '{search_term}' — {len(filtered)} results  "
                  f"(Page {page+1}/{total_pages})")
        else:
            print(f"  All videos — Page {page+1}/{total_pages} "
                  f"({len(all_videos)} total)  [private → unlisted → public]")
        print(f"{'─'*65}")
        print(f"  {'#':<4} {'TITLE':<45} {'DATE':<12} {'PRIVACY'}")
        print(f"  {'─'*4} {'─'*45} {'─'*12} {'─'*8}")

        for i, video in enumerate(page_videos, start + 1):
            title    = video["title"][:44]
            date     = video.get("published_at", "")[:10]
            privacy  = video.get("privacy", "")[:8]
            warn     = " ⚠" if video.get("is_draft") or video.get("is_unlisted") else ""
            print(f"  {i:<4} {title:<44}{warn:<2} {date:<12} {privacy}")
            if video.get("is_unlisted"):
                print(f"       ↳ Unlisted — privacy changes need YouTube Studio: {video['studio_url']}")
            elif video.get("is_draft"):
                print(f"       ↳ Possible draft — may need YouTube Studio: {video['studio_url']}")

        dry_run_label = "  *** DRY RUN ON ***" if dry_run else ""
        print(f"\n  Enter number to edit  |  /search  |  N=next  P=prev  Q=quit")
        print(f"  B=bulk privacy  |  H=bulk description heading  |  F=bulk description footer  |  M=model select  |  V=toggle dry run{dry_run_label}  |  DEL=delete")
        if search_term:
            print(f"  C=clear filter")
        print()

        cmd = input("  > ").strip()

        if cmd.upper() == "Q":
            print("  Returning to menu.")
            break

        elif cmd.upper() == "D":
            dry_run = not dry_run
            state = "ON" if dry_run else "OFF"
            print(f"  Dry run mode: {state}")

        elif cmd.upper() == "M":
            _LLM_CONFIRMED = False
            if not check_and_confirm_model(interactive=True):
                print("  Local LLM not available — regeneration (T/D/B/S) will be disabled.")

        elif cmd.upper() == "B":
            _bulk_privacy_change(youtube, yt_upload, all_videos, dry_run, _live)
            # Refresh after bulk change
            try:
                if _live is not None:
                    all_videos = _live.fetch_all_channel_videos(youtube, channel_id)
                else:
                    all_videos = _fetch_all_channel_videos(youtube, channel_id)
            except HttpError:
                pass

        elif cmd.upper() == "H":
            _bulk_prepend_description_heading(
                youtube,
                yt_upload,
                (filtered if search_term else all_videos),
                dry_run=dry_run,
                live_mod=_live,
            )

        elif cmd.upper() == "F":
            _bulk_append_description_footer(
                youtube,
                yt_upload,
                (filtered if search_term else all_videos),
                dry_run=dry_run,
                live_mod=_live,
            )

        elif cmd.upper() == "DEL":
            if dry_run:
                print("\n  DRY RUN is ON — deletion is disabled.")
                continue

            print("\n  DELETE VIDEO")
            print("  Enter the NUMBER of the video to delete (from the current filtered list).")
            sel = input("  Number (or Enter to cancel): ").strip()
            if not sel:
                continue
            if not sel.isdigit():
                print("  Invalid number.")
                continue
            idx = int(sel) - 1
            if not (0 <= idx < len(filtered)):
                print("  Invalid number.")
                continue

            video = filtered[idx]
            vid_id = video.get("youtube_id")
            title = video.get("title", "") or ""
            privacy = video.get("privacy", "")
            print(f"\n  About to DELETE this video:")
            print(f"  Title   : {title}")
            print(f"  Privacy : {privacy}")
            print(f"  URL     : https://youtu.be/{vid_id}")
            print("\n  This cannot be undone. The video URL, views, comments, and analytics will be lost.")
            confirm_title = input("  Type the EXACT title to confirm deletion: ").strip()
            if confirm_title != title:
                print("  Title did not match — cancelled.")
                continue
            confirm = input("  Final confirm — delete now? (y/N): ").strip().lower()
            if confirm != "y":
                print("  Cancelled.")
                continue

            try:
                youtube.videos().delete(id=vid_id).execute()
                print("  Deleted.")
            except HttpError as e:
                print(f"\n  ERROR deleting video: {e}")
                continue

            # Refresh after deletion
            try:
                if _live is not None:
                    all_videos = _live.fetch_all_channel_videos(youtube, channel_id)
                else:
                    all_videos = _fetch_all_channel_videos(youtube, channel_id)
            except HttpError:
                pass

        elif cmd.upper() == "N":
            if page < total_pages - 1:
                page += 1
            else:
                print("  Already on last page.")

        elif cmd.upper() == "P":
            if page > 0:
                page -= 1
            else:
                print("  Already on first page.")

        elif cmd.upper() == "C":
            search_term = ""
            page = 0

        elif cmd.startswith("/"):
            search_term = cmd[1:].strip()
            page = 0

        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(filtered):
                video = filtered[idx]
                _edit_live_video(youtube, video, yt_upload, dry_run=dry_run, live_mod=_live)
                # Refresh video list after edit
                try:
                    if _live is not None:
                        all_videos = _live.fetch_all_channel_videos(youtube, channel_id)
                    else:
                        all_videos = _fetch_all_channel_videos(youtube, channel_id)
                    filtered   = [
                        v for v in all_videos
                        if search_term.lower() in v["title"].lower()
                    ] if search_term else all_videos
                except HttpError:
                    pass
            else:
                print("  Invalid number.")
        else:
            print("  Invalid input.")


def _bulk_privacy_change(youtube, yt_upload, videos, dry_run=False, live_mod=None):
    """
    Bulk privacy change — set all or filtered videos to a new privacy status.
    Shows count by current privacy, confirms before proceeding.
    Skips unlisted and draft videos with Studio links.
    """
    priv_counts = {}
    for v in videos:
        p = v.get("privacy", "unknown")
        priv_counts[p] = priv_counts.get(p, 0) + 1

    print(f"\n  BULK PRIVACY CHANGE")
    print(f"  Current status of {len(videos)} videos:")
    for p, count in sorted(priv_counts.items()):
        print(f"    {p:<12}: {count}")

    print("\n  Filter by current privacy (or Enter for all):")
    print("  Options: private, public, unlisted, all")
    filter_p = input("  Filter: ").strip().lower()
    if filter_p and filter_p != "all":
        targets = [v for v in videos if v.get("privacy") == filter_p]
    else:
        targets = list(videos)

    # Exclude unlisted — must be changed in Studio
    skipped = [v for v in targets if v.get("is_unlisted") or v.get("is_draft")]
    targets  = [v for v in targets if not v.get("is_unlisted") and not v.get("is_draft")]

    if skipped:
        print(f"\n  Skipping {len(skipped)} unlisted/draft videos (use YouTube Studio):")
        for v in skipped[:5]:
            print(f"    {v['title'][:50]}  →  {v['studio_url']}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped)-5} more")

    if not targets:
        print("  No eligible videos to change.")
        return

    print(f"\n  New privacy for {len(targets)} videos:")
    print("  Options: public, private, unlisted")
    new_p = input("  New privacy: ").strip().lower()
    if new_p not in ("public", "private", "unlisted"):
        print("  Invalid privacy. Cancelled.")
        return

    print(f"\n  About to set {len(targets)} videos to '{new_p}'.")
    if dry_run:
        print("  DRY RUN — no changes will be pushed.")
    confirm = input("  Confirm? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    if live_mod is not None:
        def _progress(i, total, v, status):
            title = (v.get("title", "") or "")[:45]
            if status == "starting":
                print(f"  [{i}/{total}] {title}...", end=" ")
            elif status == "dry_run":
                print("(dry run)")
            elif status == "updated":
                print("done")
            elif status in ("failed", "error"):
                print(status.upper())

        results = live_mod.bulk_privacy_change(
            youtube,
            targets,
            new_privacy=new_p,
            filter_privacy=None,
            dry_run=dry_run,
            sleep_seconds=0.5,
            progress_cb=_progress,
        )
        print(
            f"\n  Bulk change complete: {results.get('updated', 0)} updated, "
            f"{results.get('errors', 0)} errors.\n"
        )
        return

    done = 0
    errors = 0
    for i, v in enumerate(targets, 1):
        vid_id = v["youtube_id"]
        title  = v["title"][:45]
        print(f"  [{i}/{len(targets)}] {title}...", end=" ")
        if dry_run:
            print("(dry run)")
            done += 1
            continue
        try:
            success = yt_upload.push_metadata_update(
                youtube, vid_id,
                v.get("title", ""), v.get("description", ""), "",
                privacy=new_p,
            )
            if success:
                print("done")
                v["privacy"] = new_p
                done += 1
            else:
                print("FAILED")
                errors += 1
        except HttpError as e:
            print(f"ERROR pushing metadata: {e}")
            errors += 1
        import time as _bt; _bt.sleep(0.5)

    print(f"\n  Bulk change complete: {done} updated, {errors} errors.\n")


def _prompt_multiline_block(title: str) -> str:
    print(f"\n  {title}")
    print("  Paste/type your text. Blank line to finish. Just Enter to cancel.\n")
    lines = []
    while True:
        try:
            line = input("  > ")
        except EOFError:
            break
        if line.strip() == "":
            break
        lines.append(line.rstrip("\n"))
    block = "\n".join(lines).strip()
    return block


def _bulk_append_description_footer(youtube, yt_upload, videos, *, dry_run=False, live_mod=None):
    if not videos:
        print("\n  No videos in scope.")
        return

    footer = _prompt_multiline_block("DESCRIPTION FOOTER — append to selected videos")
    if not footer:
        print("  Cancelled.")
        return

    eligible = [v for v in videos if not v.get("is_unlisted") and not v.get("is_draft")]
    skipped = [v for v in videos if v.get("is_unlisted") or v.get("is_draft")]

    if skipped:
        print(f"\n  Skipping {len(skipped)} unlisted/draft videos (use YouTube Studio):")
        for v in skipped[:5]:
            print(f"    {v['title'][:50]}  →  {v['studio_url']}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped)-5} more")

    if not eligible:
        print("\n  No eligible videos to change.")
        return

    print(f"\n  About to append this footer to {len(eligible)} videos.")
    if dry_run:
        print("  DRY RUN — no changes will be pushed.")
    confirm = input("  Confirm? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    import time as _time

    updated = 0
    no_change = 0
    too_long = 0
    errors = 0

    for i, v in enumerate(eligible, 1):
        youtube_id = v.get("youtube_id")
        title = (v.get("title") or "")[:45]
        print(f"  [{i}/{len(eligible)}] {title}...", end=" ")

        try:
            if live_mod is not None:
                current = live_mod.get_live_video_metadata(youtube, youtube_id)
            else:
                current = yt_upload.get_live_video_metadata(youtube, youtube_id)
        except Exception as e:
            print(f"ERROR fetching metadata: {e}")
            errors += 1
            continue

        if not current:
            print("ERROR fetching metadata")
            errors += 1
            continue

        desc = current.get("description", "") or ""
        if footer in desc:
            print("skipped (already contains footer)")
            no_change += 1
            continue

        sep = "\n\n" if desc.strip() else ""
        new_desc = f"{desc.rstrip()}" + sep + footer

        if len(new_desc) > 5000:
            print("skipped (would exceed 5000 char limit)")
            too_long += 1
            continue

        if dry_run:
            print("(dry run)")
            updated += 1
            continue

        try:
            if live_mod is not None:
                ok = live_mod.push_metadata_update(
                    youtube,
                    youtube_id,
                    current.get("title", ""),
                    new_desc,
                    current.get("tags", ""),
                    privacy=current.get("privacy", None),
                    category=current.get("category", "19"),
                    made_for_kids=current.get("made_for_kids", False),
                    license=current.get("license", "youtube"),
                    embeddable=current.get("embeddable", True),
                    public_stats=current.get("public_stats", True),
                    default_language=current.get("default_language", "en"),
                    audio_language=current.get("audio_language", "en"),
                    paid_promo=current.get("paid_promo", False),
                )
            else:
                ok = yt_upload.push_metadata_update(
                    youtube,
                    youtube_id,
                    current.get("title", ""),
                    new_desc,
                    current.get("tags", ""),
                    privacy=current.get("privacy", None),
                    category=current.get("category", "19"),
                    made_for_kids=current.get("made_for_kids", False),
                    license=current.get("license", "youtube"),
                    embeddable=current.get("embeddable", True),
                    public_stats=current.get("public_stats", True),
                    default_language=current.get("default_language", "en"),
                    audio_language=current.get("audio_language", "en"),
                    paid_promo=current.get("paid_promo", False),
                )

            if ok:
                print("done")
                updated += 1
            else:
                print("FAILED")
                errors += 1
        except Exception as e:
            print(f"ERROR pushing update: {e}")
            errors += 1

        _time.sleep(0.5)

    print(
        f"\n  Footer append complete: {updated} updated, {no_change} already had it, "
        f"{too_long} too long, {errors} errors.\n"
    )


def _bulk_prepend_description_heading(youtube, yt_upload, videos, *, dry_run=False, live_mod=None):
    if not videos:
        print("\n  No videos in scope.")
        return

    heading = _prompt_multiline_block("DESCRIPTION HEADING — prepend to selected videos")
    if not heading:
        print("  Cancelled.")
        return

    eligible = [v for v in videos if not v.get("is_unlisted") and not v.get("is_draft")]
    skipped = [v for v in videos if v.get("is_unlisted") or v.get("is_draft")]

    if skipped:
        print(f"\n  Skipping {len(skipped)} unlisted/draft videos (use YouTube Studio):")
        for v in skipped[:5]:
            print(f"    {v['title'][:50]}  →  {v['studio_url']}")
        if len(skipped) > 5:
            print(f"    ... and {len(skipped)-5} more")

    if not eligible:
        print("\n  No eligible videos to change.")
        return

    print(f"\n  About to prepend this heading to {len(eligible)} videos.")
    if dry_run:
        print("  DRY RUN — no changes will be pushed.")
    confirm = input("  Confirm? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    import time as _time

    updated = 0
    no_change = 0
    too_long = 0
    errors = 0

    for i, v in enumerate(eligible, 1):
        youtube_id = v.get("youtube_id")
        title = (v.get("title") or "")[:45]
        print(f"  [{i}/{len(eligible)}] {title}...", end=" ")

        try:
            if live_mod is not None:
                current = live_mod.get_live_video_metadata(youtube, youtube_id)
            else:
                current = yt_upload.get_live_video_metadata(youtube, youtube_id)
        except Exception as e:
            print(f"ERROR fetching metadata: {e}")
            errors += 1
            continue

        if not current:
            print("ERROR fetching metadata")
            errors += 1
            continue

        desc = current.get("description", "") or ""
        if heading in desc:
            print("skipped (already contains heading)")
            no_change += 1
            continue

        sep = "\n\n" if desc.strip() else ""
        new_desc = heading + sep + desc.lstrip()

        if len(new_desc) > 5000:
            print("skipped (would exceed 5000 char limit)")
            too_long += 1
            continue

        if dry_run:
            print("(dry run)")
            updated += 1
            continue

        try:
            if live_mod is not None:
                ok = live_mod.push_metadata_update(
                    youtube,
                    youtube_id,
                    current.get("title", ""),
                    new_desc,
                    current.get("tags", ""),
                    privacy=current.get("privacy", None),
                    category=current.get("category", "19"),
                    made_for_kids=current.get("made_for_kids", False),
                    license=current.get("license", "youtube"),
                    embeddable=current.get("embeddable", True),
                    public_stats=current.get("public_stats", True),
                    default_language=current.get("default_language", "en"),
                    audio_language=current.get("audio_language", "en"),
                    paid_promo=current.get("paid_promo", False),
                )
            else:
                ok = yt_upload.push_metadata_update(
                    youtube,
                    youtube_id,
                    current.get("title", ""),
                    new_desc,
                    current.get("tags", ""),
                    privacy=current.get("privacy", None),
                    category=current.get("category", "19"),
                    made_for_kids=current.get("made_for_kids", False),
                    license=current.get("license", "youtube"),
                    embeddable=current.get("embeddable", True),
                    public_stats=current.get("public_stats", True),
                    default_language=current.get("default_language", "en"),
                    audio_language=current.get("audio_language", "en"),
                    paid_promo=current.get("paid_promo", False),
                )

            if ok:
                print("done")
                updated += 1
            else:
                print("FAILED")
                errors += 1
        except Exception as e:
            print(f"ERROR pushing update: {e}")
            errors += 1

        _time.sleep(0.5)

    print(
        f"\n  Heading prepend complete: {updated} updated, {no_change} already had it, "
        f"{too_long} too long, {errors} errors.\n"
    )


def _get_channel_id(youtube):
    """Get authenticated user's channel ID."""
    from googleapiclient.errors import HttpError
    from requests.exceptions import Timeout
    try:
        response = youtube.channels().list(part="id", mine=True).execute()
    except Timeout:
        print("  ERROR: Connection to YouTube API timed out while fetching channel ID.")
        raise SystemExit(1)
    items    = response.get("items", [])
    if not items:
        raise Exception("No channel found for this account.")
    return items[0]["id"]


def _fetch_all_channel_videos(youtube, channel_id):
    """
    Fetch ALL videos on the channel including private ones.
    Uses the uploads playlist instead of search() — search() only
    returns public videos. The uploads playlist returns everything.

    Steps:
      1. Get uploads playlist ID from channel details
      2. Page through playlistItems to collect all video IDs
      3. Batch fetch videos().list(part=snippet,status) for full details
      4. Sort: private first, then unlisted, then public
    """
    from googleapiclient.errors import HttpError

    # Step 1 — get uploads playlist ID
    try:
        ch_resp = youtube.channels().list(
            part="contentDetails",
            id=channel_id
        ).execute()
        uploads_playlist = (
            ch_resp["items"][0]["contentDetails"]
            ["relatedPlaylists"]["uploads"]
        )
    except (HttpError, KeyError, IndexError, Timeout) as e:
        print(f"  ERROR: Connection to YouTube API timed out while fetching uploads playlist: {e}")
        raise SystemExit(1)

    # Step 2 — collect all video IDs from playlist (includes private)
    video_ids  = []
    page_token = None
    while True:
        params = {
            "part":       "contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            resp = youtube.playlistItems().list(**params).execute()
        except Timeout as e:
            print(f"  ERROR: Connection to YouTube API timed out while fetching playlist items: {e}")
            raise SystemExit(1)
        except HttpError as e:
            raise Exception(f"Error fetching playlist items: {e}")

        for item in resp.get("items", []):
            vid_id = item["contentDetails"]["videoId"]
            video_ids.append(vid_id)

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return []

    # Step 3 — batch fetch full details in groups of 50
    videos = []
    for i in range(0, len(video_ids), 50):
        batch_ids = ",".join(video_ids[i:i+50])
        try:
            resp = youtube.videos().list(
                part="snippet,status",
                id=batch_ids
            ).execute()
        except HttpError as e:
            raise Exception(f"Error fetching video details: {e}")

        for item in resp.get("items", []):
            videos.append({
                "youtube_id":   item["id"],
                "title":        item["snippet"]["title"],
                "published_at": item["snippet"]["publishedAt"][:10],
                "privacy":      item["status"]["privacyStatus"],
            })

    # Step 4 — flag drafts and unlisted with warnings
    for v in videos:
        privacy = v.get("privacy", "")
        title   = v.get("title", "")
        # Draft: private + no real title (YouTube auto-names as "Video uploaded...")
        is_draft = privacy == "private" and (
            not title or title.startswith("Video uploaded") or title == "Untitled"
        )
        v["is_draft"]    = is_draft
        v["is_unlisted"] = (privacy == "unlisted")
        v["studio_url"]  = f"https://studio.youtube.com/video/{v['youtube_id']}/edit"

    # Step 5 — sort: private/draft first, then unlisted, then public
    PRIVACY_ORDER = {"private": 0, "unlisted": 1, "public": 2}
    videos.sort(key=lambda v: PRIVACY_ORDER.get(v.get("privacy", ""), 3))

    return videos


def _edit_live_video(youtube, video, yt_upload, dry_run=False, live_mod=None):
    """
    Interactive edit loop for a single live video.
    Fetches current metadata, shows T/D/B/E/S/A/X menu,
    pushes accepted changes to YouTube.
    No CSV is touched.
    """
    youtube_id = video["youtube_id"]

    print(f"\n  Fetching current metadata for: {video['title'][:55]}")
    if live_mod is not None:
        current = live_mod.get_live_video_metadata(youtube, youtube_id)
    else:
        current = yt_upload.get_live_video_metadata(youtube, youtube_id)

    if not current:
        print("  ERROR: Could not fetch metadata. Skipping.")
        return

    title       = current["title"]
    description = current["description"]
    tags        = current["tags"]
    location    = extract_location(os.path.basename(video["title"]))
    seed_notes  = ""

    # Apply channel defaults for any fields not set on the live video
    if "made_for_kids"    not in current: current["made_for_kids"]    = YT_KIDS()
    if "license"          not in current: current["license"]          = YT_LICENSE()
    if "embeddable"       not in current: current["embeddable"]       = YT_EMBEDDABLE()
    if "public_stats"     not in current: current["public_stats"]     = YT_PUBLIC_STATS()
    if "default_language" not in current: current["default_language"] = YT_DEFAULT_LANGUAGE()
    if "audio_language"   not in current: current["audio_language"]   = YT_AUDIO_LANGUAGE()
    if "category"         not in current: current["category"]         = YT_CATEGORY()

    while True:
        print(f"\n{'─'*60}")
        dry_label = "  *** DRY RUN — push disabled ***" if dry_run else ""
        print(f"  VIDEO    : https://youtu.be/{youtube_id}{dry_label}")
        print(f"  PRIVACY  : {current.get('privacy', 'private')}")
        print(f"  KIDS     : {'yes' if current.get('made_for_kids') else 'no'}")
        print(f"  LICENSE  : {current.get('license', 'youtube')}")
        print(f"  EMBED    : {'yes' if current.get('embeddable', True) else 'no'}")
        print(f"  STATS    : {'public' if current.get('public_stats', True) else 'hidden'}")
        print(f"  LANGUAGE : {current.get('default_language', 'en')} / audio: {current.get('audio_language', 'en')}")
        print(f"  CATEGORY : {current.get('category', '19')}")
        if seed_notes:
            print(f"  SEEDS    : {seed_notes}")
        print(f"{'─'*60}")
        t_len = len(title)
        d_len = len(description)
        t_warn = "  ⚠ OVER 100 CHAR LIMIT" if t_len > 100 else ""
        d_warn = "  ⚠ OVER 5000 CHAR LIMIT" if d_len > 5000 else ""
        print(f"  TITLE: ({t_len}/100 chars){t_warn}")
        print(f"  {title}")
        print(f"\n  DESCRIPTION: ({d_len}/5000 chars){d_warn}")
        print(f"  {description}")
        print("\n  TAGS:")
        print(f"  {tags}")
        print(f"{'─'*60}\n")
        print("  [A] Accept and push to YouTube")
        if LLM_AVAILABLE():
            print("  [T] Regenerate title")
            print("  [D] Regenerate description")
            print("  [B] Regenerate both")
            print("  [S] Edit seed notes and regenerate")
        else:
            print("  [T/D/B/S] LLM not available (LLM_MODE=manual_only)")
        print("  [E] Edit title / description / tags in place")
        print("  [F] Edit all fields (privacy, kids, license, language, category)")
        print("  [X] Cancel — leave YouTube unchanged")
        print()

        action = input("  Choose: ").strip().lower()

        if action == "a":
            # Warn on limit violations before push
            if len(title) > 100:
                print(f"  WARNING: Title is {len(title)} chars — YouTube limit is 100. It will be truncated.")
                ok = input("  Push anyway? (y/N): ").strip().lower()
                if ok != "y":
                    continue
            if len(description) > 5000:
                print(f"  WARNING: Description is {len(description)} chars — YouTube limit is 5000.")
                ok = input("  Push anyway? (y/N): ").strip().lower()
                if ok != "y":
                    continue
            if dry_run:
                print("\n  DRY RUN — would push:")
                print(f"    Title  : {title[:60]}")
                print(f"    Privacy: {current.get('privacy','')}")
                print("  No changes made.")
                return
            print("\n  Pushing to YouTube...")
            if live_mod is not None:
                success = live_mod.push_metadata_update(
                    youtube, youtube_id, title, description, tags,
                    privacy=current.get("privacy", "private"),
                    category=current.get("category", YT_CATEGORY()),
                    made_for_kids=current.get("made_for_kids", YT_KIDS()),
                    license=current.get("license", YT_LICENSE()),
                    embeddable=current.get("embeddable", YT_EMBEDDABLE()),
                    public_stats=current.get("public_stats", YT_PUBLIC_STATS()),
                    default_language=current.get("default_language", YT_DEFAULT_LANGUAGE()),
                    audio_language=current.get("audio_language", YT_AUDIO_LANGUAGE()),
                    paid_promo=current.get("paid_promo", YT_PAID_PROMO()),
                )
            else:
                success = yt_upload.push_metadata_update(
                    youtube, youtube_id, title, description, tags,
                    privacy=current.get("privacy", "private"),
                    category=current.get("category", YT_CATEGORY()),
                    made_for_kids=current.get("made_for_kids", YT_KIDS()),
                    license=current.get("license", YT_LICENSE()),
                    embeddable=current.get("embeddable", YT_EMBEDDABLE()),
                    public_stats=current.get("public_stats", YT_PUBLIC_STATS()),
                    default_language=current.get("default_language", YT_DEFAULT_LANGUAGE()),
                    audio_language=current.get("audio_language", YT_AUDIO_LANGUAGE()),
                    paid_promo=current.get("paid_promo", YT_PAID_PROMO()),
                )
            if success:
                print(f"  Done — verifying...")
                import time as _tv; _tv.sleep(2)
                try:
                    if live_mod is not None:
                        confirmed = live_mod.get_live_video_metadata(youtube, youtube_id)
                    else:
                        confirmed = yt_upload.get_live_video_metadata(youtube, youtube_id)
                    if confirmed:
                        print(f"  ✓ Title    : {confirmed.get('title','')[:60]}")
                        print(f"  ✓ Privacy  : {confirmed.get('privacy','')}")
                        print(f"  ✓ Kids     : {'yes' if confirmed.get('made_for_kids') else 'no'}")
                        print(f"  ✓ https://youtu.be/{youtube_id}")
                    else:
                        print(f"  ⚠ Could not re-fetch to confirm — check YouTube Studio.")
                except HttpError:
                    print(f"  ⚠ Could not re-fetch to confirm — check YouTube Studio.")
            return

        elif action == "t":
            if not LLM_AVAILABLE():
                print("  LLM not available — enable Local LLM in Settings, or use manual editing.")
                continue
            print("\n  Regenerating title...")
            if not llm_alive():
                print("  ERROR: Local LLM not responding.")
                print(f"  Check: local endpoint {LOCAL_LLM_BASE_URL()}, model '{MODEL_NAME()}' loaded.")
                continue
            text = call_llm(
                build_regen_title_prompt(location, description, seed_notes),
                MAX_TOKENS_TITLE_DESC
            )
            new_title, _ = parse_title_desc(text)
            if new_title:
                title = new_title

        elif action == "d":
            print("\n  Regenerating description...")
            if not llm_alive():
                print("  ERROR: Local LLM not responding.")
                print(f"  Check: local endpoint {LOCAL_LLM_BASE_URL()}, model '{MODEL_NAME()}' loaded.")
                continue
            text = call_llm(
                build_regen_desc_prompt(location, title, seed_notes),
                MAX_TOKENS_TITLE_DESC
            )
            _, new_desc = parse_title_desc(text)
            if new_desc:
                description = new_desc

        elif action == "b":
            print("\n  Regenerating title and description...")
            if not llm_alive():
                print("  ERROR: Local LLM not responding.")
                print(f"  Check: local endpoint {LOCAL_LLM_BASE_URL()}, model '{MODEL_NAME()}' loaded.")
                continue
            title, description = generate_title_desc(location, video["title"], seed_notes)
            import time as _time
            _time.sleep(CALL_DELAY_SECONDS)
            tags = generate_tags(location, title, description)

        elif action == "e":
            print("\n  Edit mode — use arrow keys. Press Enter to keep as-is.\n")
            title       = input_with_prefill("  Title       : ", title)
            description = input_with_prefill("  Description : ", description)
            tags        = input_with_prefill("  Tags        : ", tags)

        elif action == "s":
            print("\n  Edit seed notes — blank line to finish.\n")
            seed_notes = get_seed_notes()
            print("\n  Regenerating with seeds...")
            if not llm_alive():
                print("  ERROR: Local LLM not responding.")
                print(f"  Check: local endpoint {LOCAL_LLM_BASE_URL()}, model '{MODEL_NAME()}' loaded.")
                continue
            title, description = generate_title_desc(location, video["title"], seed_notes)
            import time as _time
            _time.sleep(CALL_DELAY_SECONDS)
            tags = generate_tags(location, title, description)

        elif action == "p":
            print("\n  Privacy:")
            print("  1. public   2. private   3. unlisted")
            p = input("  Choice: ").strip()
            privacy_map = {"1": "public", "2": "private", "3": "unlisted"}
            if p in privacy_map:
                current["privacy"] = privacy_map[p]
                print(f"  Privacy set to: {privacy_map[p]}")
            else:
                print("  Invalid choice.")

        elif action == "f":
            print("\n  Edit fields — press Enter to keep current value.")
            print("  Channel defaults: kids=no, license=youtube, english, travel, no paid promo\n")

            # Privacy
            p = input(f"  Privacy [{current.get('privacy','private')}] (public/private/unlisted): ").strip().lower()
            if p in ("public", "private", "unlisted"):
                current["privacy"] = p

            # Kids
            k = input(f"  Made for kids [{('yes' if current.get('made_for_kids') else 'no')}] (yes/no): ").strip().lower()
            if k in ("yes", "y"):
                current["made_for_kids"] = True
            elif k in ("no", "n"):
                current["made_for_kids"] = False

            # Paid promotion
            pp = input(f"  Contains paid promotion [{('yes' if current.get('paid_promo') else 'no')}] (yes/no): ").strip().lower()
            if pp in ("yes", "y"):
                current["paid_promo"] = True
            elif pp in ("no", "n"):
                current["paid_promo"] = False

            # License
            lic = input(f"  License [{current.get('license','youtube')}] (youtube/creativeCommon): ").strip().lower()
            if lic in ("youtube", "creativecommon"):
                current["license"] = lic

            # Category
            print("  Category IDs: 19=Travel, 22=People & Blogs, 24=Entertainment, 27=Education")
            cat = input(f"  Category ID [{current.get('category', YT_CATEGORY())}]: ").strip()
            if cat.isdigit():
                current["category"] = cat

            # Advanced — only if user wants
            adv = input("  Edit language/embed/stats? (y/N): ").strip().lower()
            if adv == "y":
                lang = input(f"  Default language [{current.get('default_language', YT_DEFAULT_LANGUAGE())}]: ").strip().lower()
                if lang: current["default_language"] = lang
                alang = input(f"  Audio language [{current.get('audio_language', YT_AUDIO_LANGUAGE())}]: ").strip().lower()
                if alang: current["audio_language"] = alang
                emb = input(f"  Embeddable [{('yes' if current.get('embeddable', True) else 'no')}] (yes/no): ").strip().lower()
                if emb in ("yes", "y"): current["embeddable"] = True
                elif emb in ("no", "n"): current["embeddable"] = False
                ps = input(f"  Public stats [{('public' if current.get('public_stats', True) else 'hidden')}] (public/hidden): ").strip().lower()
                if ps == "public": current["public_stats"] = True
                elif ps == "hidden": current["public_stats"] = False

            print("  Fields updated — press A to push.")

        elif action == "x":
            print("\n  Cancelled — YouTube unchanged.")
            return

        else:
            print("  Invalid choice — please enter A, T, D, B, E, F, S, or X.")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    print("╔══════════════════════════════════════════════════════╗")
    print("║      YouTube Metadata Generator                   ║")
    print("║                   youtube_meta.py                    ║")
    print("║                   Version 2.4.0                      ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    if not HAS_READLINE:
        print("  WARNING: pyreadline3 not found — inline editing will use fallback mode.")
        print("  To enable full inline editing run:  pip install pyreadline3\n")

    print("  SELECT MODE")
    print("  A. One by one  — interactive, seeded, full review")
    print("  B. Selective   — browse dialog, pick specific folders")
    print("  C. Batch       — all folders, unseeded, auto-accept")
    print("  T. Refresh thumbnails in CSV (scan folders, no metadata regen)")
    print("  R. Review & Edit live YouTube videos")
    print("  Q. Quit")
    print()

    parser = argparse.ArgumentParser(description="Generate YouTube metadata or review live videos.")
    parser.add_argument("--mode", choices=["A", "B", "C", "T", "R"], help="Operation mode: A (One by one), B (Selective), C (Batch), T (Refresh thumbnails), R (Review & Edit live).")
    args = parser.parse_args()

    choice = args.mode
    if not choice:
        print("  SELECT MODE")
        print("  A. One by one  — interactive, seeded, full review")
        print("  B. Selective   — browse dialog, pick specific folders")
        print("  C. Batch       — all folders, unseeded, auto-accept")
        print("  T. Refresh thumbnails in CSV (scan folders, no metadata regen)")
        print("  R. Review & Edit live YouTube videos")
        print("  Q. Quit")
        print()

        while True:
            choice = input("  Choice: ").strip().upper()
            if choice in ("A", "B", "C", "T", "R", "Q"):
                break
            print("  Invalid choice.")

    if choice == "Q":
        print("  Goodbye.")
        return

    if choice == "R":
        mode_review_live()
        print("\n  Done. Goodbye.")
        return

    root = input(f"\n  Root pictures folder [{PICTURES_DIR()}]: ").strip()
    if not root:
        root = PICTURES_DIR()
    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        return

    if choice == "A":
        mode_one_by_one(root)
    elif choice == "B":
        mode_selective(root)
    elif choice == "C":
        mode_batch(root)
    elif choice == "T":
        refresh_thumbnails(root)

    print("\n  Done. Goodbye.")


if __name__ == "__main__":
    main()
