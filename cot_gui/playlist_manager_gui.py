import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

SCRIPTS_DIR = os.environ.get(
    "COT_SCRIPTS_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, SCRIPTS_DIR)

import customtkinter as ctk
from tkinter import filedialog, messagebox

from cot_gui.cot_base_gui import CotBaseWindow
from cot_core.crash_utils import install_global_crash_handler
from cot_core.last_run_utils import LastRunArtifact
from cot_core.run_utils import run_with_artifact
from cot_core.logging_utils import log_exception

from cot_core.playlist_core import (
    ChannelVideo,
    Suggestion,
    load_queue_csv,
    suggest_from_channel,
    suggest_from_queue_csv,
)


class PlaylistManagerGui(CotBaseWindow):
    def __init__(self):
        super().__init__(
            title="Playlist Manager",
            subtitle="Create playlists + suggest videos from your channel and upload queue",
            width=820,
            height=640,
        )
        self._youtube = None
        self._playlists: List[Dict[str, str]] = []
        self._uploads: List[ChannelVideo] = []
        self._queue_rows: List[Dict[str, str]] = []
        self._queue_fields: List[str] = []

        self._selected_playlist_id: Optional[str] = None

        self._build_options()
        self._build_action_buttons()

        self.after(300, self._load_playlists_async)

    def _build_options(self):
        ctk.CTkLabel(
            self.options_frame,
            text="Playlist",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 6))

        ctk.CTkLabel(self.options_frame, text="Playlist:", anchor="w").grid(
            row=1, column=0, sticky="w", padx=12, pady=6
        )
        self._playlist_var = ctk.StringVar(value="")
        self._playlist_menu = ctk.CTkOptionMenu(
            self.options_frame,
            values=[""],
            variable=self._playlist_var,
            width=360,
            command=self._on_select_playlist,
        )
        self._playlist_menu.grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=6)

        ctk.CTkButton(self.options_frame, text="Refresh", width=90, command=self._load_playlists_async).grid(
            row=1, column=3, padx=6, pady=6
        )

        ctk.CTkLabel(self.options_frame, text="Create new:", anchor="w").grid(
            row=2, column=0, sticky="w", padx=12, pady=6
        )
        self._new_title_var = ctk.StringVar(value="")
        ctk.CTkEntry(self.options_frame, textvariable=self._new_title_var, width=360, placeholder_text="e.g. East Asia").grid(
            row=2, column=1, columnspan=2, sticky="w", padx=8, pady=6
        )
        ctk.CTkButton(self.options_frame, text="Create", width=90, command=self._create_playlist_async).grid(
            row=2, column=3, padx=6, pady=6
        )

        ctk.CTkLabel(
            self.options_frame,
            text="Suggestions",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=3, column=0, columnspan=4, sticky="w", padx=12, pady=(12, 6))

        ctk.CTkLabel(self.options_frame, text="Channel scan:", anchor="w").grid(
            row=4, column=0, sticky="w", padx=12, pady=6
        )
        self._scan_limit_var = ctk.StringVar(value="500")
        ctk.CTkOptionMenu(
            self.options_frame,
            values=["200", "500", "1000"],
            variable=self._scan_limit_var,
            width=100,
        ).grid(row=4, column=1, sticky="w", padx=8, pady=6)

        self._expand_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self.options_frame,
            text="Expand (paged fetch)",
            variable=self._expand_var,
        ).grid(row=4, column=2, sticky="w", padx=8, pady=6)

        self._queue_csv_var = ctk.StringVar(value="")
        ctk.CTkLabel(self.options_frame, text="Upload queue CSV:", anchor="w").grid(
            row=5, column=0, sticky="w", padx=12, pady=6
        )
        ctk.CTkEntry(self.options_frame, textvariable=self._queue_csv_var, width=360).grid(
            row=5, column=1, columnspan=2, sticky="w", padx=8, pady=6
        )
        ctk.CTkButton(self.options_frame, text="Browse", width=90, command=self._browse_queue_csv).grid(
            row=5, column=3, padx=6, pady=6
        )

        ctk.CTkLabel(self.options_frame, text="Queue title field:", anchor="w").grid(
            row=6, column=0, sticky="w", padx=12, pady=6
        )
        self._queue_title_field_var = ctk.StringVar(value="")
        self._queue_title_field_menu = ctk.CTkOptionMenu(
            self.options_frame,
            values=[""],
            variable=self._queue_title_field_var,
            width=240,
        )
        self._queue_title_field_menu.grid(row=6, column=1, sticky="w", padx=8, pady=6)

        self.options_frame.grid_columnconfigure(2, weight=1)

    def _build_action_buttons(self):
        self.suggest_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Suggest Videos",
            command=self._suggest_async,
            font=ctk.CTkFont(size=13, weight="bold"),
            height=38,
        )
        self.suggest_btn.grid(row=0, column=0, padx=(0, 6), pady=8, sticky="ew")

        self.add_selected_btn = ctk.CTkButton(
            self.buttons_frame,
            text="Add Selected (Uploaded)",
            command=self._add_selected_async,
            height=38,
        )
        self.add_selected_btn.grid(row=0, column=1, padx=6, pady=8, sticky="ew")

        ctk.CTkButton(
            self.buttons_frame,
            text="Clear Log",
            fg_color="transparent",
            border_width=1,
            command=self.clear_log,
        ).grid(row=0, column=2, padx=(6, 0), pady=8, sticky="ew")

        # suggestion list
        self._list_frame = ctk.CTkFrame(self)
        self._list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 10))
        self.grid_rowconfigure(2, weight=1)
        self._list_frame.grid_rowconfigure(1, weight=1)
        self._list_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self._list_frame,
            text="Suggestions (check to include)",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

        self._scroll = ctk.CTkScrollableFrame(self._list_frame)
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self._checks: List[Tuple[Suggestion, ctk.BooleanVar]] = []

    def _browse_queue_csv(self):
        path = filedialog.askopenfilename(
            title="Select CSV",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self._queue_csv_var.set(path)
        self._load_queue_csv(path)

    def _load_queue_csv(self, path: str):
        rows, fields = load_queue_csv(path)
        self._queue_rows = rows
        self._queue_fields = fields
        values = fields if fields else [""]
        try:
            self._queue_title_field_menu.configure(values=values)
        except Exception:
            pass
        # heuristic default
        for candidate in ("title", "video_title", "Title", "youtube_title"):
            if candidate in fields:
                self._queue_title_field_var.set(candidate)
                break
        if not self._queue_title_field_var.get() and values:
            self._queue_title_field_var.set(values[0])

    def _ensure_youtube(self):
        if self._youtube is not None:
            return self._youtube
        # Reuse existing auth flow to avoid duplicating fork setup.
        from youtube_upload import authenticate
        self._youtube = authenticate()
        return self._youtube

    def _load_playlists_async(self):
        self.clear_log()
        self.set_status("Loading playlists…")
        threading.Thread(target=self._load_playlists_thread, daemon=True).start()

    def _load_playlists_thread(self):
        try:
            yt = self._ensure_youtube()
            playlists = []
            page_token = None
            while True:
                req = yt.playlists().list(
                    part="snippet,contentDetails",
                    mine=True,
                    maxResults=50,
                    pageToken=page_token,
                )
                resp = req.execute()
                for it in resp.get("items", []):
                    playlists.append(
                        {
                            "id": it.get("id", ""),
                            "title": (it.get("snippet", {}) or {}).get("title", ""),
                        }
                    )
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            self._playlists = playlists
            titles = [p["title"] for p in playlists if p.get("title")]
            if not titles:
                titles = [""]
            self.after(0, lambda: self._playlist_menu.configure(values=titles))
            self.after(0, lambda: self.set_status("Ready"))
            self.log(f"Playlists loaded: {len(playlists)}", "info")
        except Exception as e:
            try:
                log_exception("playlist_manager.load_playlists", e, log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"))
            except Exception:
                pass
            self.after(0, lambda: self.set_status("Failed to load playlists"))
            self.log(f"Error loading playlists: {e}", "error")

    def _on_select_playlist(self, title: str):
        title = (title or "").strip()
        pid = None
        for p in self._playlists:
            if (p.get("title") or "").strip() == title:
                pid = p.get("id")
                break
        self._selected_playlist_id = pid

    def _create_playlist_async(self):
        title = (self._new_title_var.get() or "").strip()
        if not title:
            messagebox.showerror("Title", "Please enter a playlist title.")
            return
        threading.Thread(target=self._create_playlist_thread, args=(title,), daemon=True).start()

    def _create_playlist_thread(self, title: str):
        def _do(_artifact: LastRunArtifact):
            yt = self._ensure_youtube()
            body = {
                "snippet": {"title": title},
                "status": {"privacyStatus": "private"},
            }
            resp = yt.playlists().insert(part="snippet,status", body=body).execute()
            pid = resp.get("id")
            _artifact.set_output("playlist_id", pid)
            return pid

        try:
            pid = run_with_artifact(
                artifact_path=os.path.join(SCRIPTS_DIR, "last_run_playlist_manager.json"),
                tool="playlist_create",
                inputs={"title": title},
                log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
                preflight=False,
                fn=_do,
            )
            self.after(0, lambda: self.log(f"Created playlist: {title} ({pid})", "success"))
            self.after(0, self._load_playlists_async)
        except Exception as e:
            self.after(0, lambda: self.log(f"Create failed: {e}", "error"))

    def _suggest_async(self):
        if not self._selected_playlist_id:
            messagebox.showerror("Playlist", "Please select a playlist first.")
            return
        self.clear_log()
        self.show_progress()
        self.set_status("Suggesting…")
        self._clear_suggestions()
        threading.Thread(target=self._suggest_thread, daemon=True).start()

    def _suggest_thread(self):
        try:
            # find playlist title
            playlist_title = ""
            sel_title = (self._playlist_var.get() or "").strip()
            playlist_title = sel_title

            yt = self._ensure_youtube()

            limit = int((self._scan_limit_var.get() or "500").strip() or 500)
            expand = bool(self._expand_var.get())
            videos = self._fetch_recent_uploads(yt, limit=limit, expand=expand)
            self._uploads = videos

            ch_sugs = suggest_from_channel(playlist_title=playlist_title, videos=videos)

            q_sugs: List[Suggestion] = []
            csv_path = (self._queue_csv_var.get() or "").strip()
            if csv_path and os.path.isfile(csv_path):
                if not self._queue_rows:
                    self._load_queue_csv(csv_path)
                field = (self._queue_title_field_var.get() or "").strip()
                if field:
                    q_sugs = suggest_from_queue_csv(playlist_title=playlist_title, rows=self._queue_rows, title_field=field)

            # merge, prefer do_fit, stable order
            all_sugs = sorted(ch_sugs + q_sugs, key=lambda s: (0 if s.confidence == "do_fit" else 1, s.source, s.title.lower()))

            self.after(0, lambda: self._render_suggestions(all_sugs))
            self.after(0, lambda: self.set_status(f"Suggestions: {len(all_sugs)}"))
            self.after(0, self.hide_progress)
        except Exception as e:
            self.after(0, self.hide_progress)
            self.after(0, lambda: self.set_status("Suggest failed"))
            self.after(0, lambda: self.log(f"Suggest failed: {e}", "error"))

    def _fetch_recent_uploads(self, yt, *, limit: int, expand: bool) -> List[ChannelVideo]:
        # quota-frugal approach: use uploads playlist
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        items = ch.get("items", [])
        if not items:
            return []
        uploads_pl = (((items[0] or {}).get("contentDetails", {}) or {}).get("relatedPlaylists", {}) or {}).get("uploads")
        if not uploads_pl:
            return []

        out: List[ChannelVideo] = []
        page_token = None
        while True:
            req = yt.playlistItems().list(
                part="snippet",
                playlistId=uploads_pl,
                maxResults=50,
                pageToken=page_token,
            )
            resp = req.execute()
            for it in resp.get("items", []):
                sn = it.get("snippet", {}) or {}
                rid = sn.get("resourceId", {}) or {}
                vid = rid.get("videoId", "")
                title = sn.get("title", "")
                pub = sn.get("publishedAt", "")
                if vid and title and title != "Private video" and title != "Deleted video":
                    out.append(ChannelVideo(video_id=vid, title=title, published_at=pub))
                    if len(out) >= limit:
                        break
            if len(out) >= limit:
                break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
            if not expand and len(out) >= limit:
                break
        return out

    def _clear_suggestions(self):
        for w in self._scroll.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        self._checks = []

    def _render_suggestions(self, sugs: List[Suggestion]):
        self._clear_suggestions()
        if not sugs:
            self.log("No suggestions found.", "info")
            return

        for s in sugs:
            v = ctk.BooleanVar(value=(s.source == "channel" and s.confidence == "do_fit"))
            fr = ctk.CTkFrame(self._scroll)
            fr.pack(fill="x", padx=6, pady=4)
            cb = ctk.CTkCheckBox(fr, text="", variable=v)
            cb.pack(side="left", padx=(8, 6), pady=6)
            title = ctk.CTkLabel(fr, text=s.title, anchor="w", justify="left")
            title.pack(side="left", fill="x", expand=True, padx=6)
            meta = f"{s.source.upper()}  |  {s.confidence.upper()}  |  {s.reason}"
            ctk.CTkLabel(fr, text=meta, text_color="gray", anchor="e").pack(side="right", padx=8)
            self._checks.append((s, v))

        self.log(f"Rendered {len(sugs)} suggestions.", "info")

    def _add_selected_async(self):
        if not self._selected_playlist_id:
            messagebox.showerror("Playlist", "Please select a playlist first.")
            return
        selected = [s for (s, v) in self._checks if v.get() and s.source == "channel"]
        if not selected:
            messagebox.showerror("Nothing selected", "Select at least one CHANNEL suggestion to add.")
            return
        if not messagebox.askyesno("Confirm", f"Add {len(selected)} video(s) to playlist?"):
            return
        self.show_progress()
        self.set_status("Adding…")
        threading.Thread(target=self._add_selected_thread, args=(selected,), daemon=True).start()

    def _add_selected_thread(self, selected: List[Suggestion]):
        pid = self._selected_playlist_id
        assert pid is not None

        def _do(_artifact: LastRunArtifact):
            yt = self._ensure_youtube()
            added = 0
            errors = []
            for s in selected:
                try:
                    body = {
                        "snippet": {
                            "playlistId": pid,
                            "resourceId": {"kind": "youtube#video", "videoId": s.key},
                        }
                    }
                    yt.playlistItems().insert(part="snippet", body=body).execute()
                    added += 1
                except Exception as e:
                    errors.append({"video_id": s.key, "title": s.title, "error": str(e)})
            _artifact.set_output("added", added)
            _artifact.set_output("errors", errors)
            return added, errors

        try:
            added, errors = run_with_artifact(
                artifact_path=os.path.join(SCRIPTS_DIR, "last_run_playlist_manager.json"),
                tool="playlist_add_selected",
                inputs={"playlist_id": pid, "selected": [s.key for s in selected]},
                log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
                preflight=False,
                fn=_do,
            )
            self.after(0, self.hide_progress)
            self.after(0, lambda: self.set_status(f"Added {added}"))
            self.after(0, lambda: self.log(f"Added to playlist: {added}", "success"))
            if errors:
                self.after(0, lambda: self.log(f"Errors: {len(errors)} (see last_run_playlist_manager.json)", "error"))
        except Exception as e:
            self.after(0, self.hide_progress)
            self.after(0, lambda: self.set_status("Add failed"))
            self.after(0, lambda: self.log(f"Add failed: {e}", "error"))


if __name__ == "__main__":
    try:
        install_global_crash_handler(
            tool="playlist_manager_gui",
            out_dir=SCRIPTS_DIR,
            log_path=os.path.join(SCRIPTS_DIR, "launcher_log.txt"),
        )
    except Exception:
        pass

    app = PlaylistManagerGui()
    app.mainloop()
