# Refactor Notes — YouTube Video Toolkit

## This increment

- Removed user-facing legacy channel-specific branding from the launcher, docs, and major CLI banners.
- Replaced LM-Studio-specific UI language with provider-neutral **Local LLM** language.
- Added `cot_core/local_llm.py` for discovery/checking of llama-server, LM Studio, Ollama, and saved custom OpenAI-compatible endpoints.
- Updated metadata and preflight checks to use Local LLM discovery while preserving legacy config compatibility.
- Reworked Audio Prep Suite integration so the top band launches the separate app only when a runnable app-level launcher is found.
- Removed the right-side Audio Prep panel and old internal Audio Prep tool cards from the YouTube Toolkit UI to prevent dependency errors such as missing `librosa` from surfacing in the wrong app.
- Added stale-path repair behavior for saved Audio Prep Suite paths.
- Added Admin **Diagnostics** output.
- Sanitized private config paths while preserving private OAuth files in the private build.
- Removed accidental personal-machine folder entries and runtime/generated files from packaged zips.

## Audio Prep Suite detection

A folder is considered Audio Prep Suite if it has an app-level launcher such as:

- `audio_prep_suite.py`
- `main.py`
- `launcher.py`
- `Audio Prep Suite.exe`
- `AudioPrepSuite.exe`
- `audio_prep_suite.bat`
- `launch_audio_prep.bat`

or audio-specific project markers such as:

- `pipeline/full_prep_gui.py`
- `bpm_tool/bpm_gui.py`
- `converters/wav_to_mp3.py`
- `trimmers/trim_silence.py`
- `key_detection/key_gui.py`

Only app-level launchers are used for launching.

## Known limitation

This environment does not have the GUI dependency `customtkinter` or Google API packages installed, so I validated by static compilation and import tests for modules whose dependencies are available. A Windows GUI smoke test is still required.

## Current UI / workflow increment

- Added top-band **Audio Prep Suite** / **Find Audio Prep** button.
- Removed the duplicate right-side Audio Prep Suite panel.
- Promoted priority video workflows in the launcher: create movie from media folder, batch-create movies from folders, add sound to one existing movie, and batch-add sound to movies.
- Added `cot_gui/add_sound_gui.py` and `cot_core/video_audio_core.py` for non-destructive audio-to-existing-video workflows.
- Added auto-detect audio behavior for media-folder movie creation and per-folder batch creation.
- Added `cot_core/audio_prep_bridge.py` as the dependency-safe external bridge for future Audio Prep CLI commands.
