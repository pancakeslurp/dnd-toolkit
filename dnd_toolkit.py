"""
dnd_toolkit.py
==============

Shared boilerplate for David's forensic drag-and-drop tools
(EvilImageConverter, BadHashPy, ExifExposer, and future ones).

WHY THIS FILE LOOKS THE WAY IT DOES
------------------------------------
Raw tkinter looks like "shovelware" mainly because nothing about it is
*consistent* by default: every widget picks its own padding, its own
font, its own gray. This module fixes that once, centrally, so every
tool built on it inherits the same look automatically.

The rules baked in here (change them in one place -> every tool updates):

  1. Use ttk widgets everywhere, never raw tk widgets, so the OS-native
     theme engine renders them instead of tkinter's 1990s Motif style.
  2. One spacing unit (PAD) used for every gap. No widget is ever placed
     with an arbitrary padx/pady value.
  3. One font family/size for body text, one (slightly larger, bold)
     for headers. Nothing else.
  4. One accent color, used only for the primary action button and the
     drop-zone highlight. Everything else stays neutral.
  5. Grid layout with sticky="nsew" and consistent column weights, so
     edges line up instead of drifting.
  6. A Treeview (not a Listbox) for any file list, because Treeview
     gives you real aligned columns for free.

HOW TO BUILD A NEW TOOL ON TOP OF THIS
----------------------------------------
Subclass DragDropToolApp and implement `process_file(self, path)`.
That's the only method you need to write. Everything else (window
chrome, drop zone, file list, status bar, clipboard button) is
handled by the base class.

    class HashGeneratorApp(DragDropToolApp):
        def __init__(self):
            super().__init__(
                title="BadHashPy",
                instructions="Drop files here to hash them (SHA-256)",
                columns=("File", "SHA-256"),
            )

        def process_file(self, path):
            import hashlib
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
            return (path.name, digest)

    if __name__ == "__main__":
        HashGeneratorApp().run()

`process_file` returns a tuple matching `columns` (minus the first
column, which is always the filename) -- the base class inserts it
into the Treeview and keeps the last row's values available for the
"Copy" button.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Callable, Optional, Sequence

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False

try:
    import pyperclip
    _HAS_CLIPBOARD = True
except ImportError:
    _HAS_CLIPBOARD = False


# ---------------------------------------------------------------------------
# STYLE GUIDE -- the single source of truth for how every tool looks.
# Change a value here, not in the widgets below.
# ---------------------------------------------------------------------------

PAD = 8                      # the only spacing unit used anywhere
PAD_SMALL = PAD // 2

FONT_FAMILY = "Segoe UI"     # falls back gracefully on non-Windows
FONT_SIZE = 10
FONT_BODY = (FONT_FAMILY, FONT_SIZE)
FONT_HEADER = (FONT_FAMILY, FONT_SIZE + 3, "bold")
FONT_MONO = ("Consolas", FONT_SIZE)   # for hashes / hex / file paths

COLOR_BG = "#f3f3f3"          # neutral window background (classic gray, not white)
COLOR_SURFACE = "#ffffff"     # drop zone / list background
COLOR_BORDER = "#c9c9c9"
COLOR_ACCENT = "#0067c0"      # one accent color, used sparingly
COLOR_ACCENT_HOVER = "#0857a3"
COLOR_TEXT = "#1a1a1a"
COLOR_TEXT_MUTED = "#666666"
COLOR_DROP_ACTIVE = "#e6f0fa"  # drop zone while a drag is hovering over it

THEME_BASE = "clam"           # 'clam' respects custom colors better than 'vista'/'default'

WINDOW_MIN_SIZE = (480, 360)


def apply_style(root: tk.Misc) -> ttk.Style:
    """Configure ttk once, for the whole app. Call this exactly once per root."""
    style = ttk.Style(root)
    style.theme_use(THEME_BASE)

    root.configure(bg=COLOR_BG)

    style.configure("TFrame", background=COLOR_BG)
    style.configure("Surface.TFrame", background=COLOR_SURFACE, relief="solid",
                     borderwidth=1)
    style.configure("DropActive.TFrame", background=COLOR_DROP_ACTIVE, relief="solid",
                     borderwidth=1)

    style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                     font=FONT_BODY)
    style.configure("Header.TLabel", background=COLOR_BG, foreground=COLOR_TEXT,
                     font=FONT_HEADER)
    style.configure("Muted.TLabel", background=COLOR_BG, foreground=COLOR_TEXT_MUTED,
                     font=FONT_BODY)
    style.configure("DropZone.TLabel", background=COLOR_SURFACE,
                     foreground=COLOR_TEXT_MUTED, font=FONT_BODY)

    style.configure("TButton", font=FONT_BODY, padding=(PAD, PAD_SMALL))
    style.configure("Accent.TButton", font=FONT_BODY, padding=(PAD, PAD_SMALL),
                     background=COLOR_ACCENT, foreground="white")
    style.map("Accent.TButton",
              background=[("active", COLOR_ACCENT_HOVER), ("disabled", COLOR_BORDER)])

    style.configure("Treeview", font=FONT_BODY, rowheight=24,
                     background=COLOR_SURFACE, fieldbackground=COLOR_SURFACE)
    style.configure("Treeview.Heading", font=(FONT_FAMILY, FONT_SIZE, "bold"))

    style.configure("Status.TLabel", background=COLOR_BORDER, foreground=COLOR_TEXT,
                     font=(FONT_FAMILY, FONT_SIZE - 1), padding=(PAD_SMALL, PAD_SMALL // 2))

    return style


# ---------------------------------------------------------------------------
# BASE APP
# ---------------------------------------------------------------------------

class DragDropToolApp:
    """
    Base class for every drag-and-drop forensic tool.

    Subclass and implement `process_file(self, path: Path) -> tuple`.
    The tuple's values are inserted as the remaining Treeview columns
    (the first column is always the filename, added automatically).
    """

    def __init__(
        self,
        title: str,
        instructions: str,
        columns: Sequence[str] = ("File", "Result"),
        window_size: tuple[int, int] = (720, 480),
    ):
        self.columns = columns
        self.root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
        self.root.title(title)
        self.root.geometry(f"{window_size[0]}x{window_size[1]}")
        self.root.minsize(*WINDOW_MIN_SIZE)

        self.style = apply_style(self.root)

        self._build_layout(title, instructions)

        if not _HAS_DND:
            self._show_dnd_warning()

    # -- layout ------------------------------------------------------------

    def _build_layout(self, title: str, instructions: str) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)  # the file list row grows

        # Header
        header = ttk.Label(root, text=title, style="Header.TLabel")
        header.grid(row=0, column=0, sticky="w", padx=PAD, pady=(PAD, PAD_SMALL))

        # Drop zone
        self.drop_zone = ttk.Frame(root, style="Surface.TFrame", height=90)
        self.drop_zone.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD_SMALL)
        self.drop_zone.grid_propagate(False)
        self.drop_zone.columnconfigure(0, weight=1)
        self.drop_zone.rowconfigure(0, weight=1)

        self.drop_label = ttk.Label(
            self.drop_zone, text=f"{instructions}\n(or click to browse)",
            style="DropZone.TLabel", justify="center", anchor="center",
        )
        self.drop_label.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        self.drop_label.bind("<Button-1>", lambda e: self._browse_files())

        # File list
        list_frame = ttk.Frame(root)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=PAD, pady=PAD_SMALL)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            list_frame, columns=self.columns, show="headings", selectmode="browse",
        )
        for col in self.columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor="w", width=200, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

        # Action row (buttons anchored right, consistent order: secondary -> primary)
        action_row = ttk.Frame(root)
        action_row.grid(row=3, column=0, sticky="ew", padx=PAD, pady=PAD_SMALL)
        action_row.columnconfigure(0, weight=1)  # spacer pushes buttons right

        ttk.Button(action_row, text="Clear", command=self._clear_results).grid(
            row=0, column=1, padx=(0, PAD_SMALL)
        )
        self.copy_button = ttk.Button(
            action_row, text="Copy Selected", command=self._copy_selected,
            style="Accent.TButton",
        )
        self.copy_button.grid(row=0, column=2)

        # Status bar
        self.status_var = tk.StringVar(value="Ready.")
        status = ttk.Label(root, textvariable=self.status_var, style="Status.TLabel",
                            anchor="w")
        status.grid(row=4, column=0, sticky="ew")

        # Wire up drag-and-drop if available
        if _HAS_DND:
            self.drop_zone.drop_target_register(DND_FILES)
            self.drop_zone.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_zone.dnd_bind("<<DragEnter>>", self._on_drag_enter)
            self.drop_zone.dnd_bind("<<DragLeave>>", self._on_drag_leave)

    def _show_dnd_warning(self) -> None:
        self.set_status(
            "tkinterdnd2 not installed — drag-and-drop disabled, click the box to browse."
        )

    # -- drag & drop handlers ------------------------------------------------

    def _on_drag_enter(self, event) -> None:
        self.drop_zone.configure(style="DropActive.TFrame")

    def _on_drag_leave(self, event) -> None:
        self.drop_zone.configure(style="Surface.TFrame")

    def _on_drop(self, event) -> None:
        self.drop_zone.configure(style="Surface.TFrame")
        paths = self.root.tk.splitlist(event.data)
        self._handle_paths(paths)

    def _browse_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Select files")
        if paths:
            self._handle_paths(paths)

    # -- core processing loop -------------------------------------------------

    def _handle_paths(self, paths: Sequence[str]) -> None:
        succeeded, failed = 0, 0
        for raw in paths:
            path = Path(raw)
            try:
                result = self.process_file(path)
                self.tree.insert("", "end", values=(path.name, *result))
                succeeded += 1
            except Exception as exc:  # noqa: BLE001 -- surface any tool-specific error
                self.tree.insert("", "end", values=(path.name, f"ERROR: {exc}"))
                failed += 1
        summary = f"Processed {succeeded} file(s)"
        if failed:
            summary += f", {failed} failed"
        self.set_status(summary + ".")

    def process_file(self, path: Path) -> tuple:
        """
        Override in subclasses. Return a tuple of values matching
        `self.columns[1:]` (the filename column is filled in automatically).
        """
        raise NotImplementedError("Subclasses must implement process_file()")

    # -- toolbar actions -------------------------------------------------

    def _clear_results(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.set_status("Cleared.")

    def _copy_selected(self) -> None:
        selection = self.tree.selection()
        if not selection:
            self.set_status("Nothing selected to copy.")
            return
        rows = [self.tree.item(item, "values") for item in selection]
        text = "\n".join("\t".join(str(v) for v in row) for row in rows)
        if _HAS_CLIPBOARD:
            pyperclip.copy(text)
        else:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        self.set_status(f"Copied {len(rows)} row(s) to clipboard.")

    # -- helpers -----------------------------------------------------------

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Minimal runnable example (kept here so this file can be smoke-tested on
# its own with `python dnd_toolkit.py`; real tools import DragDropToolApp
# and apply_style from this module instead of running this block).
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    class _DemoApp(DragDropToolApp):
        def __init__(self):
            super().__init__(
                title="Toolkit Demo",
                instructions="Drop any file here",
                columns=("File", "Size (bytes)"),
            )

        def process_file(self, path: Path) -> tuple:
            return (path.stat().st_size,)

    _DemoApp().run()