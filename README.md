# YouTube Video Toolkit

A GUI-first toolkit for creating slideshow-style YouTube videos, adding sound to existing movies, preparing metadata, uploading, managing playlists, reviewing analytics, and editing live metadata.

## What this build fixes

- Portable settings for source folders, output folders, audio folders, FFmpeg, FFprobe, YouTube OAuth files, Audio Prep Suite, and Local LLM endpoints.
- Audio Prep Suite is treated as a separate optional app. A top-band launcher opens it when found; the old right-side duplicate panel and internal audio-tool buttons were removed.
- Priority video workflows are exposed directly: create one movie from a media folder, batch-create movies from folders, add sound to an existing movie, and batch-add sound to movies.
- Adding sound to existing movies is non-destructive and saves new `*_with_audio.mp4` outputs.
- Local LLM support is provider-neutral. The toolkit can auto-detect common local endpoints for llama-server, LM Studio, Ollama, or a saved custom OpenAI-compatible endpoint.
- Legacy channel-specific labels were removed from user-facing UI/docs.
- Private config, OAuth token files, logs, generated runners, caches, and last-run artifacts are excluded from the release-clean package.

## Start

Run:

```bat
python master_launcher.py
```

Use **Settings** to configure folders, YouTube OAuth files, Audio Prep Suite location, and optional Local LLM settings.

Use **Admin → Toolkit Check** to validate paths, FFmpeg/FFprobe, YouTube auth files, Local LLM status, disk space, and memory.


## Priority video workflows

The launcher exposes four first-class video workflows:

- **Create Movie from Media Folder** — choose one folder of images/clips, auto-detect or select audio, and render one MP4.
- **Batch Create Movies from Folders** — each immediate subfolder becomes one MP4. Audio can be detected per folder or selected once for all.
- **Add Sound to Existing Movie** — choose an existing movie and audio file; output is saved as `*_with_audio.mp4`.
- **Batch Add Sound to Movies** — scan a folder of movies and add matching/same-folder audio or one selected audio file.

Original media files are not modified.

## Audio Prep Suite integration

Audio Prep Suite is optional and installed separately. The launcher looks for it in sibling folders first. If it is not found, use **Settings → Find** or **Browse** to select the Audio Prep Suite app folder.

A valid Audio Prep Suite folder should contain a runnable app marker such as `main.py`, `launcher.py`, `audio_prep_suite.py`, `launch.bat`, or an app executable. The YouTube Toolkit should not require `librosa`; Audio Prep Suite should install its own dependencies.

## Local LLM integration

Local LLM is optional. If enabled, the toolkit checks:

- llama-server / llama.cpp at `http://127.0.0.1:8080`
- LM Studio at `http://127.0.0.1:1234`
- Ollama at `http://127.0.0.1:11434`
- any saved custom endpoint

If no endpoint is running, metadata tools should still support manual editing.

## Private files

Do not commit or share:

- `client_secrets.json`
- `token.json`
- `cot_config.json`
- `master_config.json`
- logs, caches, generated runners, and last-run JSON files

Use the `.example.json` files as templates for new users.

## Audio Prep bridge design

YouTube Video Toolkit can launch Audio Prep Suite and passes `YT_TOOLKIT_PATH` so Audio Prep can discover the toolkit. Audio Prep dependencies such as `librosa` stay inside Audio Prep Suite. Future Audio Prep CLI commands can be called through `cot_core/audio_prep_bridge.py` without importing Audio Prep internals.
