# YouTube Movie Creator Toolkit

A GUI-first desktop toolkit for turning media folders into YouTube-ready videos, adding sound to existing movies, preparing metadata, uploading to YouTube, managing playlists, reviewing analytics, and editing live metadata.

This project is designed to work alongside **Audio Prep Suite** without bundling or importing its audio-processing dependencies. YouTube video creation lives here; deeper audio preparation lives in Audio Prep Suite.

## Core features

- **Create Movie from Media Folder** — select one folder of images/clips, optionally auto-detect audio, and render one MP4.
- **Batch Create Movies from Folders** — each immediate subfolder becomes its own video.
- **Add Sound to Existing Movie** — select an existing video and audio file, then save a non-destructive `*_with_audio.mp4` output.
- **Batch Add Sound to Movies** — process multiple videos with matching/same-folder audio or one selected audio file.
- **YouTube metadata tools** — prepare, review, and edit titles, descriptions, tags, and related metadata.
- **YouTube upload tools** — upload prepared videos using your own OAuth credentials.
- **Playlist and analytics tools** — manage uploaded content and review performance.
- **Audio Prep Suite launcher** — discover and launch Audio Prep Suite as a separate app.
- **Local LLM support** — optional provider-neutral endpoint discovery for llama-server, LM Studio, Ollama, or a custom OpenAI-compatible endpoint.

## What changed in the refactor

- Removed old channel-specific UI/docs language.
- Removed hardcoded personal Windows paths from runtime settings.
- Added persistent user-configurable paths for media folders, output folders, audio folders, FFmpeg/FFprobe, YouTube OAuth files, Audio Prep Suite, and Local LLM settings.
- Replaced the old LM Studio-only check with a general **LLM Check**.
- Replaced old internal audio-tool launching with a clean external **Audio Prep Suite** bridge.
- Promoted the main user workflows into the launcher UI.
- Added non-destructive audio-to-video workflows.
- Added release-clean packaging rules so OAuth tokens, private configs, logs, caches, and generated files are not published.

## Install

1. Clone or download the repository.
2. Install Python dependencies:

```bat
python -m pip install -r requirements.txt
```

3. Install FFmpeg and FFprobe, or use **Settings** to browse to their executables.
4. Run the launcher:

```bat
python master_launcher.py
```

## First-run setup

Open **Settings** and configure:

- Source pictures/media folder
- Output videos folder
- Audio/music folder
- FFmpeg executable
- FFprobe executable
- YouTube `client_secrets.json`
- YouTube `token.json`, if already authorized
- Optional Audio Prep Suite folder
- Optional Local LLM endpoint/model

Then run **Admin → Toolkit Check**.

## Audio Prep Suite integration

Audio Prep Suite is optional and installed separately. The YouTube Movie Creator Toolkit searches sibling folders and saved settings for a runnable Audio Prep Suite launcher.

A valid Audio Prep Suite folder may contain one of these app markers:

- `main.py`
- `launcher.py`
- `audio_prep_suite.py`
- `launch.bat`
- an Audio Prep Suite executable

The YouTube toolkit should not require `librosa`. If Audio Prep Suite reports `librosa` missing, repair Audio Prep Suite by installing its own requirements into the same Python environment used to launch Audio Prep Suite.

## Local LLM integration

Local LLM is optional. The toolkit can check common local endpoints:

- llama-server / llama.cpp: `http://127.0.0.1:8080`
- LM Studio: `http://127.0.0.1:1234`
- Ollama: `http://127.0.0.1:11434`
- Custom OpenAI-compatible endpoint

If no Local LLM is running, metadata tools should still support manual editing.

## Privacy and GitHub safety

Do not commit or publish private runtime files:

- `client_secrets.json`
- `token.json`
- `cot_config.json`
- `master_config.json`
- logs
- caches
- generated launchers
- last-run JSON files

Use the `.example.json` files as templates for new users.

Before pushing to GitHub, check:

```bat
git ls-files | findstr /i "token.json client_secrets.json cot_config.json master_config.json"
```

That command should return nothing.

## Recommended workflow

1. Prepare or collect images/clips in a folder.
2. Add one audio file to the same folder, or choose an audio file manually.
3. Use **Create Movie from Media Folder**.
4. Review the created MP4.
5. Use metadata/upload tools when ready.

For existing videos, use **Add Sound to Existing Movie** or **Batch Add Sound to Movies**.


## License

This project is source-available for noncommercial use under the **PolyForm Noncommercial License 1.0.0**. See [`LICENSE.md`](LICENSE.md) for the full terms.

Commercial use, resale, paid client deployment, or use inside a commercial product or service is not granted by this license unless the copyright holder gives separate written permission.

Required Notice: Copyright © 2026 Kevin Horst.

## Project status

This is a refactored portable build focused on preserving existing functionality while improving safety, settings, workflow clarity, and GitHub-readiness.
