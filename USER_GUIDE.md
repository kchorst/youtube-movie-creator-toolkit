# User Guide — YouTube Video Toolkit

## Main launcher

The main launcher is centered on video workflows. The top band includes **Audio Prep Suite**, **Admin**, and **Settings**. The old right-side Audio Prep panel is intentionally removed so there is one clean launcher path.

Primary video workflows:

- **Create Movie from Media Folder**
- **Batch Create Movies from Folders**
- **Add Sound to Existing Movie**
- **Batch Add Sound to Movies**

Additional YouTube Toolkit tools are listed below those priority workflows.

## Settings

Configure these before normal use:

- Source pictures folder
- Output videos folder
- Audio/music folder
- FFmpeg executable
- FFprobe executable
- YouTube `client_secrets.json`
- YouTube `token.json`
- Optional Audio Prep Suite folder
- Optional Local LLM endpoint/model

Settings are persisted locally.

## Create movies and add sound

For a new movie, choose **Create Movie from Media Folder**. The tool can auto-detect audio in the selected folder, or you can manually select one audio file.

For batch creation, choose **Batch Create Movies from Folders**. Each immediate subfolder becomes a separate movie.

For an existing movie, choose **Add Sound to Existing Movie**. The original video is not modified; the output is saved as `*_with_audio.mp4`. Batch mode can use matching/same-folder audio for each movie or one selected audio file for all.

## Audio Prep Suite

Audio Prep Suite is not bundled into this toolkit. The launcher searches nearby sibling folders. If not found, choose the app folder manually.

The launcher looks for app-level launchers such as `main.py`, `launcher.py`, `audio_prep_suite.py`, `launch.bat`, or an executable. It does not launch old internal audio modules directly.

If Audio Prep Suite itself reports `librosa` missing, fix that in Audio Prep Suite by installing its requirements into the same Python environment used to launch it.

## Local LLM

Local LLM is optional. Use **Settings → Find** or **Admin → LLM Check** to discover a running local endpoint. Supported common endpoints include llama-server, LM Studio, Ollama, and custom OpenAI-compatible URLs.

If Local LLM is not available, use manual metadata editing.

## Admin checks

Use:

- **Toolkit Check** for overall readiness
- **LLM Check** for local endpoint/model status
- **Diagnostics** to copy useful debugging information
- **Check Google Auth** to verify OAuth files
- **Check Dependencies** for Python package imports

## Audio Prep / YouTube Toolkit bridge

The two apps are separate but discover each other. YouTube Toolkit launches Audio Prep Suite from the top band. Audio Prep Suite should provide the equivalent **YouTube Toolkit** launcher on its side. Audio processing dependencies stay with Audio Prep Suite.
