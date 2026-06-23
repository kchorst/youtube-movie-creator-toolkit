YouTube Video Toolkit setup
===========================

1. Install Python dependencies:
   python -m pip install -r requirements.txt

2. Start the launcher:
   python master_launcher.py

3. Open Settings and configure:
   - source pictures folder
   - output videos folder
   - audio/music folder
   - FFmpeg / FFprobe
   - YouTube OAuth files
   - optional Audio Prep Suite folder
   - optional Local LLM endpoint/model

4. Run Admin -> Toolkit Check.

5. Run a small Make Show test before large batch work.

Private files such as token.json, client_secrets.json, cot_config.json, master_config.json, logs, and generated runners should not be committed or shared.
