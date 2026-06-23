import os
import sys
import json
import shutil
import argparse
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


try:
    import cot_config as cfg
    cfg.load(gui_mode=True)
except Exception:
    cfg = None


try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None


try:
    from PIL import Image
except Exception as e:  # pragma: no cover
    raise RuntimeError("Pillow is required. Install dependencies with: pip install -r requirements.txt") from e


try:
    import numpy as np
except Exception as e:  # pragma: no cover
    raise RuntimeError("numpy is required. Install dependencies with: pip install -r requirements.txt") from e


try:
    import imagehash  # type: ignore
except Exception:
    imagehash = None


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_SKIP_DUPES = False
_SKIP_FACES = False
_NO_EYE_VERIFY = False
_ANALYSIS_MAX_SIZE: Optional[int] = None
EXCLUDE_DIR_NAME = "Exclude"
COT_DIR_NAME = ".cot"


_DRY_RUN = False
_SUPERBATCH = False
_BATCH = False


@dataclass
class CurateThresholds:
    face_exclude_mode: str = "any"  # 'any' or 'major'
    face_verify_eyes: bool = True
    face_major_ratio: float = 0.05
    blur_laplacian_var_min: float = 100.0
    dark_luma_mean_max: float = 40.0
    bright_luma_mean_min: float = 215.0
    dup_phash_distance_max: int = 6


def _apply_keep_mode(th: CurateThresholds, keep_mode: str) -> CurateThresholds:
    mode = (keep_mode or "").strip().lower()
    if mode not in ("keep_more", "balanced", "keep_less"):
        return th

    if mode == "keep_more":
        th.face_exclude_mode = "major"
        th.face_verify_eyes = True
        th.face_major_ratio = 0.08
        th.blur_laplacian_var_min = 25.0
        th.dark_luma_mean_max = 25.0
        th.bright_luma_mean_min = 235.0
        th.dup_phash_distance_max = 4
        return th

    if mode == "balanced":
        # Leave as-is (defaults or prior project thresholds)
        return th

    # keep_less
    th.face_exclude_mode = "any"
    th.face_verify_eyes = False
    th.face_major_ratio = 0.02
    th.blur_laplacian_var_min = 110.0
    th.dark_luma_mean_max = 55.0
    th.bright_luma_mean_min = 205.0
    th.dup_phash_distance_max = 8
    return th


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_relpath(path: str, root: str) -> str:
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def _iter_media_dirs(project_root: str) -> List[str]:
    dirs: List[str] = []
    for cur, dirnames, filenames in os.walk(project_root):
        # Skip Exclude and .cot subtrees
        parts = {p.lower() for p in cur.split(os.sep) if p}
        if EXCLUDE_DIR_NAME.lower() in parts or COT_DIR_NAME.lower() in parts:
            dirnames[:] = []
            continue

        # Avoid descending into Exclude/.cot from here
        dirnames[:] = [d for d in dirnames if d.lower() not in (EXCLUDE_DIR_NAME.lower(), COT_DIR_NAME.lower())]

        has_images = any(os.path.splitext(f)[1].lower() in IMAGE_EXTS for f in filenames)
        if has_images:
            dirs.append(cur)

    return dirs


def _list_images_in_dir(d: str) -> List[str]:
    imgs: List[str] = []
    try:
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if not os.path.isfile(p):
                continue
            if os.path.splitext(f)[1].lower() in IMAGE_EXTS:
                imgs.append(p)
    except Exception:
        return []

    imgs.sort(key=lambda p: os.path.basename(p).lower())
    return imgs


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def _move_file(src: str, dest_dir: str) -> str:
    _ensure_dir(dest_dir)
    base = os.path.basename(src)
    name, ext = os.path.splitext(base)
    dest = os.path.join(dest_dir, base)
    i = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{name}_{i}{ext}")
        i += 1
    shutil.move(src, dest)
    return dest


def _load_image_rgb(path: str, *, error_out: Optional[Dict[str, str]] = None) -> Optional[np.ndarray]:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if _ANALYSIS_MAX_SIZE and _ANALYSIS_MAX_SIZE > 0:
                w, h = im.size
                m = max(w, h)
                if m > _ANALYSIS_MAX_SIZE:
                    scale = float(_ANALYSIS_MAX_SIZE) / float(m)
                    nw = max(1, int(round(w * scale)))
                    nh = max(1, int(round(h * scale)))
                    im = im.resize((nw, nh), Image.BILINEAR)
            return np.array(im)
    except Exception as e:
        if error_out is not None:
            try:
                error_out[path] = str(e)
            except Exception:
                pass
        return None


def _luma_mean(rgb: np.ndarray) -> float:
    # Approx Rec.709
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return float(np.mean(y))


def _blur_score(rgb: np.ndarray) -> Optional[float]:
    if cv2 is None:
        return None
    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        v = cv2.Laplacian(gray, cv2.CV_64F).var()
        return float(v)
    except Exception:
        return None


def _face_major_impl(
    rgb: np.ndarray,
    *,
    ratio_threshold: float,
    verify_eyes: bool,
) -> Optional[Tuple[bool, float, int]]:
    if cv2 is None:
        return None
    try:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        h, w = gray.shape[:2]
        area = float(h * w)
        if area <= 0:
            return (False, 0.0, 0)

        cascade_path = os.path.join(os.path.dirname(__file__), "cot_core", "haarcascade_frontalface_default.xml")
        if not os.path.isfile(cascade_path):
            # Fallback to OpenCV built-in if present
            try:
                cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")  # type: ignore[attr-defined]
            except Exception:
                cascade_path = ""

        if not cascade_path or not os.path.isfile(cascade_path):
            return (False, 0.0, 0)

        face_cascade = cv2.CascadeClassifier(cascade_path)

        eye_cascade = None
        if verify_eyes:
            # Optional human verification: require detected eyes inside the face box.
            eye_path = ""
            try:
                eye_path = os.path.join(cv2.data.haarcascades, "haarcascade_eye.xml")  # type: ignore[attr-defined]
            except Exception:
                eye_path = ""
            if eye_path and os.path.isfile(eye_path):
                eye_cascade = cv2.CascadeClassifier(eye_path)

        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if faces is None or len(faces) == 0:
            return (False, 0.0, 0)

        max_ratio = 0.0
        human_faces = 0
        for (x, y, fw, fh) in faces:
            # Compute ratio for every candidate face box.
            r = float(fw * fh) / area

            # If eye cascade is available, use it to filter out many animal false-positives.
            if eye_cascade is not None:
                roi = gray[y : y + fh, x : x + fw]
                try:
                    eyes = eye_cascade.detectMultiScale(roi, scaleFactor=1.1, minNeighbors=4, minSize=(10, 10))
                except Exception:
                    eyes = ()
                if eyes is None or len(eyes) == 0:
                    continue

            human_faces += 1
            if r > max_ratio:
                max_ratio = r

        if human_faces <= 0:
            return (False, 0.0, 0)

        return (max_ratio >= ratio_threshold, max_ratio, int(human_faces))
    except Exception:
        return (False, 0.0, 0)


def _face_major(rgb: np.ndarray, *, ratio_threshold: float) -> Optional[Tuple[bool, float, int]]:
    return _face_major_impl(rgb, ratio_threshold=ratio_threshold, verify_eyes=True)


def _face_major_noverify(rgb: np.ndarray, *, ratio_threshold: float) -> Optional[Tuple[bool, float, int]]:
    return _face_major_impl(rgb, ratio_threshold=ratio_threshold, verify_eyes=False)


def _phash(path: str) -> Optional["imagehash.ImageHash"]:
    if imagehash is None:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            return imagehash.phash(im)
    except Exception:
        return None


def _cluster_duplicates(imgs: List[str], *, max_dist: int) -> List[List[str]]:
    # Simple O(n^2) clustering; intended for typical folder sizes.
    hashes: Dict[str, "imagehash.ImageHash"] = {}
    n_imgs = len(imgs)
    for i, p in enumerate(imgs, 1):
        if _BATCH and (i == 1 or i % 50 == 0 or i == n_imgs):
            print(f"    [dupe] hashing {i}/{n_imgs}...", flush=True)
        h = _phash(p)
        if h is not None:
            hashes[p] = h

    remaining = set(hashes.keys())
    clusters: List[List[str]] = []

    n_rem_start = len(remaining)
    while remaining:
        seed = next(iter(remaining))
        remaining.remove(seed)
        cluster = [seed]
        seed_h = hashes[seed]

        if _BATCH:
            done = max(0, n_rem_start - len(remaining))
            if done == 1 or done % 50 == 0 or not remaining:
                print(f"    [dupe] clustering {done}/{n_rem_start}...", flush=True)

        to_check = list(remaining)
        for p in to_check:
            try:
                dist = seed_h - hashes[p]
            except Exception:
                continue
            if dist <= max_dist:
                remaining.remove(p)
                cluster.append(p)

        if len(cluster) > 1:
            cluster.sort(key=lambda x: os.path.basename(x).lower())
            clusters.append(cluster)

    clusters.sort(key=lambda c: len(c), reverse=True)
    return clusters


def _prompt_path(title: str, default: str) -> str:
    val = input(f"{title} [{default}]: ").strip()
    return val if val else default


def _choose_project(root: str) -> Optional[str]:
    try:
        entries = [
            d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
            and not d.startswith(".")
        ]
    except Exception as e:
        print(f"\n  ERROR: Could not list root folder: {e}\n")
        return None

    entries.sort(key=lambda s: s.lower())

    print("\n  Select a project folder:")
    for i, name in enumerate(entries[:60], 1):
        print(f"    {i:>2}. {name}")
    if len(entries) > 60:
        print(f"    ... ({len(entries) - 60} more)")

    while True:
        sel = input("\n  Enter number, or paste a folder name/path (Enter to cancel): ").strip()
        if not sel:
            return None

        if sel.isdigit():
            idx = int(sel)
            if 1 <= idx <= len(entries):
                return os.path.join(root, entries[idx - 1])
            print("  Invalid number.")
            continue

        # allow full path
        if os.path.isabs(sel) and os.path.isdir(sel):
            return sel

        p = os.path.join(root, sel)
        if os.path.isdir(p):
            return p

        print("  Not found. Try again.")


def _load_project_state(project_root: str) -> Tuple[Dict, CurateThresholds]:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    state_path = os.path.join(cot_dir, "curation.json")

    state: Dict = {}
    th = CurateThresholds()

    if os.path.isfile(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f) or {}
        except Exception:
            state = {}

    th_state = (state.get("thresholds") or {}) if isinstance(state, dict) else {}
    if isinstance(th_state, dict):
        th.face_exclude_mode = str(th_state.get("face_exclude_mode", th.face_exclude_mode) or th.face_exclude_mode).strip().lower()
        th.face_verify_eyes = bool(th_state.get("face_verify_eyes", th.face_verify_eyes))
        th.face_major_ratio = float(th_state.get("face_major_ratio", th.face_major_ratio))
        th.blur_laplacian_var_min = float(th_state.get("blur_laplacian_var_min", th.blur_laplacian_var_min))
        th.dark_luma_mean_max = float(th_state.get("dark_luma_mean_max", th.dark_luma_mean_max))
        th.bright_luma_mean_min = float(th_state.get("bright_luma_mean_min", th.bright_luma_mean_min))
        th.dup_phash_distance_max = int(th_state.get("dup_phash_distance_max", th.dup_phash_distance_max))

    # Defensive clamps in case a previous run saved zeros/invalids.
    if th.face_exclude_mode not in ("any", "major"):
        th.face_exclude_mode = "any"
    if not (0.001 <= th.face_major_ratio <= 0.5):
        th.face_major_ratio = 0.05
    if th.blur_laplacian_var_min <= 0:
        th.blur_laplacian_var_min = 100.0
    if not (0 <= th.dark_luma_mean_max <= 255):
        th.dark_luma_mean_max = 40.0
    if not (0 <= th.bright_luma_mean_min <= 255):
        th.bright_luma_mean_min = 215.0
    if not (0 <= th.dup_phash_distance_max <= 32):
        th.dup_phash_distance_max = 6

    return state, th


def _save_project_state(project_root: str, state: Dict) -> None:
    cot_dir = os.path.join(project_root, COT_DIR_NAME)
    _ensure_dir(cot_dir)
    state_path = os.path.join(cot_dir, "curation.json")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _prompt_thresholds(th: CurateThresholds) -> CurateThresholds:
    print("\n  CURATION SETTINGS (Enter to accept default)")

    print("\n  Face handling")
    print("    Mode 'any'   = exclude if any face is detected (privacy-first)")
    print("    Mode 'major' = exclude only if the largest face is a major part of the image")
    val = input(f"  Face exclude mode (any/major) [{th.face_exclude_mode}]: ").strip().lower()
    if val in ("any", "major"):
        th.face_exclude_mode = val

    print("\n  Human-face verification")
    print("    Y = require eyes inside each detected face box (filters many pet/texture false positives)")
    print("    N = count any detected face box (more aggressive privacy, but may flag pets)")
    print("    If you have lots of animal photos, choose Y.")
    yn_hint = "Y/n" if th.face_verify_eyes else "y/N"
    yn_default = "Y" if th.face_verify_eyes else "N"
    val = input(
        f"  Require eyes for a face to count? ({yn_hint}) [default {yn_default} — Enter keeps default]: "
    ).strip().lower()
    if val == "y":
        th.face_verify_eyes = True
    elif val == "n":
        th.face_verify_eyes = False

    if not (0.001 <= th.face_major_ratio <= 0.5):
        th.face_major_ratio = 0.05

    print("\n  Face threshold")
    print("    Largest-face area / image area. Lower = more sensitive.")
    print("    Typical range: 0.005 (tiny faces) → 0.08 (close-up)")
    print(f"    Default: {th.face_major_ratio:.3f}")
    val = input(f"  Major face ratio threshold [{th.face_major_ratio:.3f}]: ").strip()
    if val:
        try:
            th.face_major_ratio = float(val)
        except Exception:
            pass

    print("\n  Blur threshold")
    print("    Flags images that are too blurry / out of focus (camera shake, motion blur).")
    print("    Lower = more lenient (keeps more blur — often OK for 0.5s slides).")
    print("    Typical range: 30 (lenient) → 150 (strict)")
    print(f"    Default: {th.blur_laplacian_var_min:.1f}")
    val = input(f"  Blur threshold (Laplacian variance min) [{th.blur_laplacian_var_min:.1f}]: ").strip()
    if val:
        try:
            th.blur_laplacian_var_min = float(val)
        except Exception:
            pass

    print("\n  Dark/bright thresholds")
    print("    Flags images that are too dark to see much (underexposed).")
    print("    Luma mean is 0..255. Dark max lower = fewer dark rejects.")
    print("    Typical dark max: 20..50")
    print(f"    Default dark max: {th.dark_luma_mean_max:.1f}")
    val = input(f"  Dark threshold (luma mean max) [{th.dark_luma_mean_max:.1f}]: ").strip()
    if val:
        try:
            th.dark_luma_mean_max = float(val)
        except Exception:
            pass

    print("    Flags images that are too bright / blown out (washed highlights, light burn).")
    print("    Typical bright min: 210..245")
    print(f"    Default bright min: {th.bright_luma_mean_min:.1f}")
    val = input(f"  Bright threshold (luma mean min) [{th.bright_luma_mean_min:.1f}]: ").strip()
    if val:
        try:
            th.bright_luma_mean_min = float(val)
        except Exception:
            pass

    print("\n  Duplicate threshold")
    print("    pHash detects images that are likely duplicates or near-duplicates")
    print("    (bursts, same scene with tiny differences, resized/compressed copies).")
    print("    Lower = stricter (fewer false dupes).")
    print("    Typical range: 3 (strict) → 8 (lenient)")
    print(f"    Default: {th.dup_phash_distance_max:d}")
    val = input(f"  Duplicate pHash distance max [{th.dup_phash_distance_max:d}]: ").strip()
    if val:
        try:
            th.dup_phash_distance_max = int(val)
        except Exception:
            pass

    return th


def curate_project(project_root: str, *, keep_mode: str = "balanced", apply_moves: bool = False) -> None:
    print("\n  Media Curate — Backlog Crusher (Phase 1)")
    print(f"  Project: {project_root}")

    if cv2 is None:
        print("\n  WARNING: OpenCV not available — face detection and blur scoring will be skipped.")
        print("  Install dependencies: pip install -r requirements.txt")

    if imagehash is None:
        print("\n  WARNING: ImageHash not available — duplicate detection will be skipped.")
        print("  Install dependencies: pip install -r requirements.txt")

    state, th = _load_project_state(project_root)
    has_saved_thresholds = bool(isinstance(state, dict) and isinstance(state.get("thresholds"), dict) and state.get("thresholds"))
    if _BATCH:
        th = _apply_keep_mode(th, keep_mode)
    else:
        th = _prompt_thresholds(th)

    effective_eye_verify = (not _SKIP_FACES) and bool(th.face_verify_eyes) and (not _NO_EYE_VERIFY)
    threshold_source = "saved project thresholds" if has_saved_thresholds else "defaults"
    if _BATCH and str(keep_mode).strip().lower() in ("keep_more", "keep_less"):
        threshold_source = f"preset override ({keep_mode})"

    print("\n  Effective settings")
    print(f"    Keep mode: {keep_mode}")
    print(f"    Threshold source: {threshold_source}")
    print(f"    Downscale analysis max px: {_ANALYSIS_MAX_SIZE if _ANALYSIS_MAX_SIZE else 'full'}")
    print(f"    Skip duplicates: {'YES' if _SKIP_DUPES else 'no'}")
    print(f"    Skip faces: {'YES' if _SKIP_FACES else 'no'}")
    if not _SKIP_FACES:
        print(f"    Eye verification: {'on' if effective_eye_verify else 'off'}")

    print("\n  Thresholds")
    print(
        "    Faces: "
        f"mode={th.face_exclude_mode}, major_ratio={th.face_major_ratio:.3f}, verify_eyes={'on' if effective_eye_verify else 'off'}"
    )
    print(
        "    Quality: "
        f"blur_min={th.blur_laplacian_var_min:.1f}, dark_max={th.dark_luma_mean_max:.1f}, bright_min={th.bright_luma_mean_min:.1f}"
    )
    print(f"    Dupes: phash_dist_max={th.dup_phash_distance_max:d}")

    media_dirs = _iter_media_dirs(project_root)
    if not media_dirs:
        print("\n  No images found under this project folder.")
        return

    # Collected suggestions (global and per-folder)
    faces_to_move: List[str] = []
    faces_detected_any: List[str] = []
    lowq_to_move: List[str] = []
    broken_to_move: List[str] = []
    dupes_to_move: List[str] = []

    faces_by_dir: Dict[str, List[str]] = {}
    lowq_by_dir: Dict[str, List[str]] = {}
    dup_clusters_by_dir: Dict[str, List[List[str]]] = {}

    face_meta: Dict[str, Dict] = {}
    quality_meta: Dict[str, Dict] = {}
    load_errors: Dict[str, str] = {}
    dup_clusters: List[List[str]] = []

    print(f"\n  Scanning {len(media_dirs)} folders...")

    for idx_dir, d in enumerate(media_dirs, 1):
        rel_dir = _safe_relpath(d, project_root)
        imgs = _list_images_in_dir(d)
        if _BATCH:
            print(f"\n  [{idx_dir}/{len(media_dirs)}] Scanning folder: {rel_dir} ({len(imgs)} image(s))", flush=True)
        if not imgs:
            continue

        faces_by_dir.setdefault(d, [])
        lowq_by_dir.setdefault(d, [])
        dup_clusters_by_dir.setdefault(d, [])

        # duplicates (cluster now; decide keep/exclude later in review step)
        if (not _SKIP_DUPES) and imagehash is not None and len(imgs) >= 2:
            clusters = _cluster_duplicates(imgs, max_dist=th.dup_phash_distance_max)
            for c in clusters:
                dup_clusters.append(c)
                dup_clusters_by_dir[d].append(c)

        for idx_img, p in enumerate(imgs, 1):
            if _BATCH and (idx_img == 1 or idx_img % 25 == 0 or idx_img == len(imgs)):
                print(f"    [{idx_img}/{len(imgs)}] Scanning images...", flush=True)
            rgb = _load_image_rgb(p, error_out=load_errors)
            if rgb is None:
                err = load_errors.get(p, "")
                if err:
                    broken_to_move.append(p)
                    lowq_to_move.append(p)
                    lowq_by_dir[d].append(p)
                    quality_meta[p] = {
                        "broken": True,
                        "error": err,
                    }
                continue

            # quality
            luma = _luma_mean(rgb)
            blur = _blur_score(rgb)
            is_dark = luma <= th.dark_luma_mean_max
            is_bright = luma >= th.bright_luma_mean_min
            is_blur = (blur is not None) and (blur < th.blur_laplacian_var_min)

            quality_meta[p] = {
                "luma_mean": round(luma, 2),
                "blur": None if blur is None else round(float(blur), 2),
                "dark": bool(is_dark),
                "bright": bool(is_bright),
                "blurry": bool(is_blur),
            }

            if is_dark or is_bright or is_blur:
                lowq_to_move.append(p)
                lowq_by_dir[d].append(p)

            # faces-major
            if not _SKIP_FACES:
                verify = th.face_verify_eyes and (not _NO_EYE_VERIFY)
                fm = _face_major(rgb, ratio_threshold=th.face_major_ratio) if verify else _face_major_noverify(rgb, ratio_threshold=th.face_major_ratio)
                if fm is not None:
                    is_major, max_ratio, face_count = fm
                    face_meta[p] = {
                        "face_count": face_count,
                        "max_ratio": round(max_ratio, 5),
                        "major": bool(is_major),
                    }
                    if face_count and face_count > 0:
                        faces_detected_any.append(p)
                    if th.face_exclude_mode == "any":
                        if face_count and face_count > 0:
                            faces_to_move.append(p)
                            faces_by_dir[d].append(p)
                    else:
                        if is_major:
                            faces_to_move.append(p)
                            faces_by_dir[d].append(p)

        if _BATCH:
            print(f"    [{len(imgs)}/{len(imgs)}] Scanning images...", flush=True)

    # Deduplicate lists (keep stable-ish ordering)
    def _uniq(seq: List[str]) -> List[str]:
        seen = set()
        out = []
        for x in seq:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    faces_to_move = _uniq(faces_to_move)
    faces_detected_any = _uniq(faces_detected_any)
    lowq_to_move = _uniq(lowq_to_move)
    broken_to_move = _uniq(broken_to_move)
    dupes_to_move = _uniq(dupes_to_move)

    for d in list(faces_by_dir.keys()):
        faces_by_dir[d] = _uniq(faces_by_dir[d])
    for d in list(lowq_by_dir.keys()):
        lowq_by_dir[d] = _uniq(lowq_by_dir[d])

    broken_set = set(broken_to_move)

    print("\n  Scan summary")
    print(f"    Faces detected (any)   : {len(faces_detected_any)}")
    print(f"    Major-face suggestions : {len(faces_to_move)}")
    print(
        f"    Low-quality suggestions: {len(lowq_to_move)}"
        + (f" ({len(broken_to_move)} broken/unreadable)" if broken_to_move else "")
    )
    print(f"    Duplicate suggestions  : {len(dupes_to_move)}")

    if not _SUPERBATCH:
        print("\n  Per-folder summary (candidates)")
        print("  folder                                   faces  lowq  dup_clusters")
        print("  ---------------------------------------  -----  ----  -----------")
        for d in sorted(media_dirs, key=lambda x: _safe_relpath(x, project_root).lower()):
            rel = _safe_relpath(d, project_root)
            fcnt = len(faces_by_dir.get(d, []))
            lcnt = len(lowq_by_dir.get(d, []))
            dcnt = len(dup_clusters_by_dir.get(d, []))
            if fcnt or lcnt or dcnt:
                print(f"  {rel[:39]:<39}  {fcnt:>5}  {lcnt:>4}  {dcnt:>11}")

    def _print_samples(title: str, items: List[str]) -> None:
        if not items:
            return
        print(f"\n  {title} (showing up to 12):")
        for p in items[:12]:
            rel = _safe_relpath(p, project_root)
            print(f"    {rel}")
        if len(items) > 12:
            print(f"    ... and {len(items) - 12} more")

    _print_samples("Major faces", faces_to_move)
    if not faces_to_move and faces_detected_any:
        _print_samples("Faces detected (not major)", faces_detected_any)
    _print_samples("Broken/unreadable (moved to LowQuality)", broken_to_move)
    lowq_quality_only = [p for p in lowq_to_move if p not in broken_set]
    _print_samples("Low quality", lowq_quality_only)
    _print_samples("Duplicates", dupes_to_move)

    # Write state before any moves
    state = state if isinstance(state, dict) else {}
    state["updated_at"] = _now_iso()
    state["thresholds"] = {
        "face_exclude_mode": th.face_exclude_mode,
        "face_verify_eyes": th.face_verify_eyes,
        "face_major_ratio": th.face_major_ratio,
        "blur_laplacian_var_min": th.blur_laplacian_var_min,
        "dark_luma_mean_max": th.dark_luma_mean_max,
        "bright_luma_mean_min": th.bright_luma_mean_min,
        "dup_phash_distance_max": th.dup_phash_distance_max,
    }

    state["scan"] = {
        "project_root": project_root,
        "media_dirs": len(media_dirs),
        "faces_detected_any": len(faces_detected_any),
        "major_faces": len(faces_to_move),
        "low_quality": len(lowq_to_move),
        "broken_unreadable": len(broken_to_move),
        "duplicates": len(dupes_to_move),
    }

    state["items"] = {
        "faces": { _safe_relpath(k, project_root): v for k, v in face_meta.items() },
        "quality": { _safe_relpath(k, project_root): v for k, v in quality_meta.items() },
    }

    # Store duplicate clusters (relative paths) for traceability.
    if dup_clusters:
        state["items"]["duplicate_clusters"] = [
            [_safe_relpath(p, project_root) for p in cluster]
            for cluster in dup_clusters
        ]

    state.setdefault("moves", {})
    state["moves"].setdefault("faces", [])
    state["moves"].setdefault("low_quality", [])
    state["moves"].setdefault("duplicates", [])

    state.setdefault("planned_moves", {})
    state["planned_moves"].setdefault("faces", [])
    state["planned_moves"].setdefault("low_quality", [])
    state["planned_moves"].setdefault("duplicates", [])

    _save_project_state(project_root, state)

    if _BATCH and (not apply_moves):
        total = len(faces_to_move) + len(lowq_to_move)
        dup_move_ct = 0
        if dup_clusters:
            for c in dup_clusters:
                if c and len(c) > 1:
                    dup_move_ct += (len(c) - 1)
        total += dup_move_ct

        pct = (100.0 * total / max(1, len(faces_detected_any) + len(lowq_to_move) + (len(dupes_to_move) or 0)))
        print("\n  ANALYSIS complete — no files were moved.")
        print(f"  Keep mode: {keep_mode}")
        print(f"  Face candidates: {len(faces_to_move)}")
        print(
            f"  Low-quality candidates: {len(lowq_to_move)}"
            + (f" ({len(broken_to_move)} broken/unreadable)" if broken_to_move else "")
        )
        print(f"  Duplicate candidates: {dup_move_ct} (from {len(dup_clusters)} clusters)")
        print("\n  Next steps:")
        print("  - If this is excluding too much, re-run with keep_more.")
        print("  - If this is not excluding enough, re-run with keep_less.")
        print("  - When the report looks right, re-run with --apply to move files into Exclude/.")
        return

    if _BATCH and apply_moves:
        # Batch apply: move everything suggested with no prompts.
        state.setdefault("moves", {})
        state["moves"].setdefault("faces", [])
        state["moves"].setdefault("low_quality", [])
        state["moves"].setdefault("duplicates", [])

        moved_any = False

        def _batch_move(items: List[str], label: str, key: str) -> None:
            nonlocal moved_any
            if not items:
                return
            dest_dir = os.path.join(project_root, EXCLUDE_DIR_NAME, label)
            for src in items:
                if _DRY_RUN:
                    state.setdefault("planned_moves", {})
                    state["planned_moves"].setdefault(key, [])
                    rec = {
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                        "planned_at": _now_iso(),
                    }
                    if key == "low_quality":
                        if src in broken_set:
                            rec["reason"] = "broken"
                            err = load_errors.get(src, "")
                            if err:
                                rec["error"] = err
                        else:
                            rec["reason"] = "low_quality"
                    state["planned_moves"][key].append(rec)
                    continue
                if not os.path.isfile(src):
                    continue
                dest = _move_file(src, dest_dir)
                rec = {
                    "src": _safe_relpath(src, project_root),
                    "dest": _safe_relpath(dest, project_root),
                    "moved_at": _now_iso(),
                }
                if key == "low_quality":
                    if src in broken_set:
                        rec["reason"] = "broken"
                        err = load_errors.get(src, "")
                        if err:
                            rec["error"] = err
                    else:
                        rec["reason"] = "low_quality"
                state["moves"][key].append(rec)
            if not _DRY_RUN:
                moved_any = True

        dupes_batch: List[str] = []
        for c in dup_clusters:
            if c and len(c) > 1:
                dupes_batch.extend(c[1:])

        _batch_move(faces_to_move, "Faces", "faces")
        _batch_move(lowq_to_move, "LowQuality", "low_quality")
        _batch_move(dupes_batch, "Duplicates", "duplicates")

        state["updated_at"] = _now_iso()
        _save_project_state(project_root, state)
        if _DRY_RUN:
            print("\n  DRY RUN complete — no files were moved.")
            print("  Planned moves recorded in .cot/curation.json under planned_moves")
        elif moved_any:
            print("\n  Done. Moves recorded in .cot/curation.json")
        else:
            print("\n  No moves performed.")
        return

    def _confirm_move(label: str, n: int, *, folder_rel: Optional[str] = None) -> bool:
        if n <= 0:
            return False
        prefix = f"\n  [{folder_rel}] " if folder_rel else "\n  "
        resp = input(f"{prefix}Move {n} files to Exclude/{label}? (y/N): ").strip().lower()
        return resp == "y"

    def _review_and_filter_face_moves(items: List[str]) -> List[str]:
        if not items:
            return items

        resp = input("\n  Review face list before moving? (Y/n): ").strip().lower()
        if resp == "n":
            return items

        print("\n  Face move candidates (type numbers to REMOVE from move list)")
        print("  #   faces  ratio     file")
        print("  --- -----  --------  ----------------------------------------")

        max_show = 80
        shown = items[:max_show]
        for i, p in enumerate(shown, 1):
            meta = face_meta.get(p) or {}
            fc = meta.get("face_count", "?")
            rr = meta.get("max_ratio", "?")
            rel = _safe_relpath(p, project_root)
            print(f"  {i:>3} {str(fc):>5}  {str(rr):>8}  {rel}")
        if len(items) > max_show:
            print(f"\n  ... showing first {max_show} of {len(items)}")

        def _parse_num_ranges(text: str) -> List[int]:
            # Supports: "4", "1 3 7", "1-5", "1-3 8 10-12", commas allowed.
            nums: List[int] = []
            for tok in text.replace(",", " ").split():
                tok = tok.strip()
                if not tok:
                    continue
                if "-" in tok:
                    a, b = tok.split("-", 1)
                    if a.strip().isdigit() and b.strip().isdigit():
                        start = int(a)
                        end = int(b)
                        if start > end:
                            start, end = end, start
                        nums.extend(list(range(start, end + 1)))
                    continue
                if tok.isdigit():
                    nums.append(int(tok))
            # Dedup while preserving order
            seen = set()
            out: List[int] = []
            for n in nums:
                if n in seen:
                    continue
                seen.add(n)
                out.append(n)
            return out

        remove = set()  # items to KEEP (remove from move list)

        print("\n  Review controls:")
        print("    Enter numbers to OPEN images (e.g. 4 or 1-5 or 1 3 7-9)")
        print("    k <nums>    KEEP these (remove from move list)")
        print("    Enter       continue")
        print("    q           cancel ALL face moves")

        while True:
            s = input("\n  > ").strip()
            if not s:
                break

            if s.lower() == "q":
                return []

            parts = s.split(maxsplit=1)
            cmd = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""

            if cmd == "k":
                nums = _parse_num_ranges(rest)
                if not nums:
                    print("  No valid numbers.")
                    continue
                for n in nums:
                    if not (1 <= n <= len(shown)):
                        continue
                    remove.add(shown[n - 1])
                print(f"  Marked {len(remove)} to KEEP (will not be moved).")
                continue

            # Default action: treat input as number/range list and OPEN.
            nums = _parse_num_ranges(s)
            if not nums:
                print("  Unknown command. Enter numbers/ranges to open, 'k <nums>' to keep, 'q' to cancel.")
                continue

            for n in nums:
                if not (1 <= n <= len(shown)):
                    continue
                p = shown[n - 1]
                try:
                    os.startfile(p)  # type: ignore[attr-defined]
                except Exception as e:
                    print(f"  Could not open {os.path.basename(p)}: {e}")

        if not remove:
            return items

        filtered = [p for p in items if p not in remove]
        print(f"\n  Keeping {len(filtered)} face moves (removed {len(remove)}).")
        return filtered

    def _review_duplicate_clusters(clusters: List[List[str]]) -> List[str]:
        if not clusters:
            return []

        resp = input(f"\n  Review {len(clusters)} duplicate clusters before moving? (Y/n): ").strip().lower()
        if resp == "n":
            # Default: keep the first item, move the rest
            out: List[str] = []
            for c in clusters:
                out.extend(c[1:])
            return out

        def _parse_num_ranges(text: str) -> List[int]:
            nums: List[int] = []
            for tok in text.replace(",", " ").split():
                tok = tok.strip()
                if not tok:
                    continue
                if "-" in tok:
                    a, b = tok.split("-", 1)
                    if a.strip().isdigit() and b.strip().isdigit():
                        start = int(a)
                        end = int(b)
                        if start > end:
                            start, end = end, start
                        nums.extend(list(range(start, end + 1)))
                    continue
                if tok.isdigit():
                    nums.append(int(tok))
            seen = set()
            out: List[int] = []
            for n in nums:
                if n in seen:
                    continue
                seen.add(n)
                out.append(n)
            return out

        out_moves: List[str] = []

        print("\n  Duplicate cluster review")
        print("  For each cluster: open some candidates, choose which to KEEP, then continue.")
        print("  Commands inside a cluster:")
        print("    <nums>      open image(s) (e.g. 1 or 1-3)")
        print("    k <n>       keep candidate #n (default is 1)")
        print("    s           skip this cluster (move none)")
        print("    q           stop reviewing; apply defaults for remaining clusters")

        apply_defaults_rest = False
        for ci, cluster in enumerate(clusters, 1):
            if not cluster or len(cluster) < 2:
                continue

            if apply_defaults_rest:
                out_moves.extend(cluster[1:])
                continue

            keep_idx = 1
            print("\n" + "─" * 65)
            print(f"  Cluster {ci}/{len(clusters)} — {len(cluster)} images")
            for i, p in enumerate(cluster, 1):
                rel = _safe_relpath(p, project_root)
                print(f"    {i}. {rel}")
            print(f"  Current KEEP choice: {keep_idx}")

            while True:
                s = input("  > ").strip()
                if not s:
                    break
                sl = s.lower()
                if sl == "s":
                    keep_idx = 0
                    break
                if sl == "q":
                    apply_defaults_rest = True
                    break

                parts = s.split(maxsplit=1)
                cmd = parts[0].lower()
                rest = parts[1] if len(parts) > 1 else ""
                if cmd == "k":
                    if rest.strip().isdigit():
                        ki = int(rest.strip())
                        if 1 <= ki <= len(cluster):
                            keep_idx = ki
                            print(f"  KEEP choice set to {keep_idx}")
                        else:
                            print("  Invalid keep index.")
                    else:
                        print("  Usage: k <n>")
                    continue

                nums = _parse_num_ranges(s)
                if not nums:
                    print("  Unknown. Use numbers to open, 'k <n>' to keep, 's' skip, 'q' quit.")
                    continue

                for n in nums:
                    if not (1 <= n <= len(cluster)):
                        continue
                    p = cluster[n - 1]
                    try:
                        os.startfile(p)  # type: ignore[attr-defined]
                    except Exception as e:
                        print(f"  Could not open {os.path.basename(p)}: {e}")

            if apply_defaults_rest:
                out_moves.extend(cluster[1:])
                continue

            if keep_idx <= 0:
                # skipped
                continue

            for i, p in enumerate(cluster, 1):
                if i == keep_idx:
                    continue
                out_moves.append(p)

        return out_moves

    moved_any = False

    def _apply_moves_for_folder(folder: str) -> None:
        nonlocal moved_any

        folder_rel = _safe_relpath(folder, project_root)
        faces_local = faces_by_dir.get(folder, [])
        lowq_local = lowq_by_dir.get(folder, [])
        dup_clusters_local = dup_clusters_by_dir.get(folder, [])

        if faces_local:
            print("\n" + "─" * 65)
            print(f"  Folder: {folder_rel}")
            faces_local = _review_and_filter_face_moves(faces_local)
            if _confirm_move("Faces", len(faces_local), folder_rel=folder_rel):
                for src in faces_local:
                    dest_dir = os.path.join(folder, EXCLUDE_DIR_NAME, "Faces")
                    if _DRY_RUN:
                        state["planned_moves"]["faces"].append({
                            "src": _safe_relpath(src, project_root),
                            "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                            "planned_at": _now_iso(),
                        })
                        continue
                    dest = _move_file(src, dest_dir)
                    state["moves"]["faces"].append({
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(dest, project_root),
                        "moved_at": _now_iso(),
                    })
                moved_any = moved_any or (len(faces_local) > 0 and not _DRY_RUN)

        if lowq_local:
            print("\n" + "─" * 65)
            print(f"  Folder: {folder_rel}")
            if _confirm_move("LowQuality", len(lowq_local), folder_rel=folder_rel):
                for src in lowq_local:
                    if not os.path.isfile(src):
                        continue
                    dest_dir = os.path.join(folder, EXCLUDE_DIR_NAME, "LowQuality")
                    if _DRY_RUN:
                        rec = {
                            "src": _safe_relpath(src, project_root),
                            "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                            "planned_at": _now_iso(),
                        }
                        if src in broken_set:
                            rec["reason"] = "broken"
                            err = load_errors.get(src, "")
                            if err:
                                rec["error"] = err
                        else:
                            rec["reason"] = "low_quality"
                        state["planned_moves"]["low_quality"].append(rec)
                        continue
                    dest = _move_file(src, dest_dir)
                    rec = {
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(dest, project_root),
                        "moved_at": _now_iso(),
                    }
                    if src in broken_set:
                        rec["reason"] = "broken"
                        err = load_errors.get(src, "")
                        if err:
                            rec["error"] = err
                    else:
                        rec["reason"] = "low_quality"
                    state["moves"]["low_quality"].append(rec)
                moved_any = moved_any or (len(lowq_local) > 0 and not _DRY_RUN)

        if dup_clusters_local:
            print("\n" + "─" * 65)
            print(f"  Folder: {folder_rel}")
            dupes_local = _review_duplicate_clusters(dup_clusters_local)
            dupes_local = _uniq(dupes_local)
            if _confirm_move("Duplicates", len(dupes_local), folder_rel=folder_rel):
                for src in dupes_local:
                    if not os.path.isfile(src):
                        continue
                    dest_dir = os.path.join(folder, EXCLUDE_DIR_NAME, "Duplicates")
                    if _DRY_RUN:
                        state["planned_moves"]["duplicates"].append({
                            "src": _safe_relpath(src, project_root),
                            "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                            "planned_at": _now_iso(),
                        })
                        continue
                    dest = _move_file(src, dest_dir)
                    state["moves"]["duplicates"].append({
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(dest, project_root),
                        "moved_at": _now_iso(),
                    })
                moved_any = moved_any or (len(dupes_local) > 0 and not _DRY_RUN)

    if _SUPERBATCH:
        # Old behavior: review once for entire project.
        if faces_to_move:
            faces_to_move = _review_and_filter_face_moves(faces_to_move)

        if dup_clusters:
            dupes_to_move = _review_duplicate_clusters(dup_clusters)
            dupes_to_move = _uniq(dupes_to_move)

        if _confirm_move("Faces", len(faces_to_move)):
            for src in faces_to_move:
                d = os.path.dirname(src)
                dest_dir = os.path.join(d, EXCLUDE_DIR_NAME, "Faces")
                if _DRY_RUN:
                    state["planned_moves"]["faces"].append({
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                        "planned_at": _now_iso(),
                    })
                    continue
                dest = _move_file(src, dest_dir)
                state["moves"]["faces"].append({
                    "src": _safe_relpath(src, project_root),
                    "dest": _safe_relpath(dest, project_root),
                    "moved_at": _now_iso(),
                })
            moved_any = moved_any or (len(faces_to_move) > 0 and not _DRY_RUN)

        if _confirm_move("LowQuality", len(lowq_to_move)):
            for src in lowq_to_move:
                if not os.path.isfile(src):
                    continue
                d = os.path.dirname(src)
                dest_dir = os.path.join(d, EXCLUDE_DIR_NAME, "LowQuality")
                if _DRY_RUN:
                    rec = {
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                        "planned_at": _now_iso(),
                    }
                    if src in broken_set:
                        rec["reason"] = "broken"
                        err = load_errors.get(src, "")
                        if err:
                            rec["error"] = err
                    else:
                        rec["reason"] = "low_quality"
                    state["planned_moves"]["low_quality"].append(rec)
                    continue
                dest = _move_file(src, dest_dir)
                rec = {
                    "src": _safe_relpath(src, project_root),
                    "dest": _safe_relpath(dest, project_root),
                    "moved_at": _now_iso(),
                }
                if src in broken_set:
                    rec["reason"] = "broken"
                    err = load_errors.get(src, "")
                    if err:
                        rec["error"] = err
                else:
                    rec["reason"] = "low_quality"
                state["moves"]["low_quality"].append(rec)
            moved_any = moved_any or (len(lowq_to_move) > 0 and not _DRY_RUN)

        if _confirm_move("Duplicates", len(dupes_to_move)):
            for src in dupes_to_move:
                if not os.path.isfile(src):
                    continue
                d = os.path.dirname(src)
                dest_dir = os.path.join(d, EXCLUDE_DIR_NAME, "Duplicates")
                if _DRY_RUN:
                    state["planned_moves"]["duplicates"].append({
                        "src": _safe_relpath(src, project_root),
                        "dest": _safe_relpath(os.path.join(dest_dir, os.path.basename(src)), project_root),
                        "planned_at": _now_iso(),
                    })
                    continue
                dest = _move_file(src, dest_dir)
                state["moves"]["duplicates"].append({
                    "src": _safe_relpath(src, project_root),
                    "dest": _safe_relpath(dest, project_root),
                    "moved_at": _now_iso(),
                })
            moved_any = moved_any or (len(dupes_to_move) > 0 and not _DRY_RUN)
    else:
        for d in sorted(media_dirs, key=lambda x: _safe_relpath(x, project_root).lower()):
            if faces_by_dir.get(d) or lowq_by_dir.get(d) or dup_clusters_by_dir.get(d):
                _apply_moves_for_folder(d)

    if _DRY_RUN:
        state["updated_at"] = _now_iso()
        _save_project_state(project_root, state)
        print("\n  DRY RUN complete — no files were moved.")
        print("  Planned moves recorded in .cot/curation.json under planned_moves")
        return

    if moved_any:
        state["updated_at"] = _now_iso()
        _save_project_state(project_root, state)
        print("\n  Done. Moves recorded in .cot/curation.json")
    else:
        print("\n  No moves performed.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Media Curate — scan a single project folder for face/duplicate/low-quality suggestions and optionally move them into Exclude/*.",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Root pictures folder (defaults to PICTURES_DIR from cot_config.json, else ~/Pictures)",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Project folder name under --root, or an absolute path to a project folder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report and record planned moves, but do not move any files.",
    )
    parser.add_argument(
        "--superbatch",
        action="store_true",
        help="Superbatch mode: confirm once per project instead of per-subfolder.",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: no interactive prompts/review. Use presets and run analysis/apply.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="In batch mode, actually move files. Without this flag, batch mode runs analysis only.",
    )
    parser.add_argument(
        "--keep-mode",
        default="balanced",
        choices=["keep_more", "balanced", "keep_less"],
        help="Preset strictness: keep_more keeps more images; keep_less excludes more.",
    )
    parser.add_argument(
        "--skip-dupes",
        action="store_true",
        help="Skip duplicate detection (faster for very large folders).",
    )
    parser.add_argument(
        "--skip-faces",
        action="store_true",
        help="Skip face detection entirely (faster).",
    )
    parser.add_argument(
        "--no-eye-verify",
        action="store_true",
        help="Disable eye verification (faster; may increase false positives on animals/textures).",
    )
    parser.add_argument(
        "--analysis-max-size",
        type=int,
        default=None,
        help="Downscale images so max(width,height) <= this value for analysis (faster).",
    )
    args = parser.parse_args()

    global _BATCH, _DRY_RUN, _SKIP_DUPES, _SKIP_FACES, _NO_EYE_VERIFY, _ANALYSIS_MAX_SIZE
    _SKIP_DUPES = bool(args.skip_dupes)
    _SKIP_FACES = bool(args.skip_faces)
    _NO_EYE_VERIFY = bool(args.no_eye_verify)
    _ANALYSIS_MAX_SIZE = args.analysis_max_size

    global _DRY_RUN
    _DRY_RUN = bool(args.dry_run)

    global _SUPERBATCH
    _SUPERBATCH = bool(args.superbatch)

    global _BATCH
    _BATCH = bool(args.batch)

    if _BATCH and (not bool(args.apply)):
        _DRY_RUN = True

    default_root = ""
    if cfg is not None:
        try:
            default_root = cfg.get("PICTURES_DIR", "")
        except Exception:
            default_root = ""

    if not default_root:
        default_root = os.path.join(os.path.expanduser("~"), "Pictures")

    print("\n  Media Curate (Backlog Crusher)")
    if _DRY_RUN:
        print("  *** DRY RUN ON — no files will be moved ***")
    if _SUPERBATCH:
        print("  *** SUPERBATCH ON — confirm once per project ***")

    root = args.root or default_root

    # If non-interactive, require explicit args to avoid EOFError.
    is_tty = False
    try:
        is_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    except Exception:
        is_tty = False

    if is_tty and not args.root:
        root = _prompt_path("\n  Root pictures folder", root)

    if not os.path.isdir(root):
        print(f"\n  ERROR: Folder not found: {root}")
        raise SystemExit(1)

    if args.project:
        project = args.project
        if not (os.path.isabs(project) and os.path.isdir(project)):
            project = os.path.join(root, project)
        if not os.path.isdir(project):
            print(f"\n  ERROR: Project folder not found: {project}")
            raise SystemExit(1)
        curate_project(project, keep_mode=str(args.keep_mode), apply_moves=bool(args.apply))
        return

    if not is_tty:
        print("\n  ERROR: No TTY available for interactive prompts.")
        print("  Re-run with --project and optionally --root.")
        print("  Example:")
        print("    python cot_curate.py --root C:\\Users\\You\\Pictures --project \"2025 Armenia\"")
        raise SystemExit(2)

    while True:
        project = _choose_project(root)
        if not project:
            print("\n  Cancelled.")
            return

        curate_project(project, keep_mode=str(args.keep_mode), apply_moves=bool(args.apply))

        again = input("\n  Run another project? (y/N): ").strip().lower()
        if again != "y":
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        raise SystemExit(1)
