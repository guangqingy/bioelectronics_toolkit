from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from services.histology import find_histology_cases, load_histology_preview_pair, sanitize_name


class HistologyNamingGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Histology Naming GUI")
        self.geometry("1320x860")
        self.minsize(1120, 720)

        self.root_path_var = tk.StringVar(value=str(Path.cwd()))
        self.new_name_var = tk.StringVar()
        self.case_name_var = tk.StringVar(value="No case selected")
        self.status_var = tk.StringVar(value="Load a histology folder to begin.")

        self._cases: list[dict] = []
        self._selected_case: dict | None = None
        self._main_photo = None
        self._label_photo = None

        self._build_ui()

    def _build_ui(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=(12, 10))
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Source folder", width=14).grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.root_path_var).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="Browse", command=self._browse_root).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(top, text="Load", command=self._load_project).grid(row=0, column=3)

        left = ttk.Frame(self, padding=(12, 0, 8, 12))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        ttk.Label(left, text="Histology cases").grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.case_tree = ttk.Treeview(left, columns=("folder", "overview"), show="headings", height=24)
        self.case_tree.heading("folder", text="Folder")
        self.case_tree.heading("overview", text="Overview.vsi")
        self.case_tree.column("folder", width=220, anchor="w")
        self.case_tree.column("overview", width=260, anchor="w")
        self.case_tree.grid(row=1, column=0, sticky="nsew")
        self.case_tree.bind("<<TreeviewSelect>>", self._on_case_select)

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.case_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.case_tree.configure(yscrollcommand=scroll.set)

        actions = ttk.Frame(left)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text="New folder name").grid(row=0, column=0, sticky="w")
        ttk.Entry(actions, textvariable=self.new_name_var).grid(row=1, column=0, sticky="ew", pady=(4, 8))

        row = ttk.Frame(actions)
        row.grid(row=2, column=0, sticky="ew")
        row.columnconfigure((0, 1), weight=1)
        ttk.Button(row, text="Rename Folder", command=self._apply_rename).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(row, text="Reload", command=self._load_project).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        right = ttk.Frame(self, padding=(8, 0, 12, 12))
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure((0, 1), weight=1)

        header = ttk.Frame(right)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, textvariable=self.case_name_var, font=("Arial", 18, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.main_card = self._make_preview_card(right, "Main / Histology")
        self.main_card.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.label_card = self._make_preview_card(right, "Label / Associated image")
        self.label_card.grid(row=1, column=1, sticky="nsew", padx=(8, 0))

        notes = ttk.LabelFrame(right, text="Notes", padding=10)
        notes.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        notes.columnconfigure(0, weight=1)
        self.notes_text = tk.Text(notes, height=8, wrap="word")
        self.notes_text.grid(row=0, column=0, sticky="nsew")
        note_scroll = ttk.Scrollbar(notes, orient="vertical", command=self.notes_text.yview)
        note_scroll.grid(row=0, column=1, sticky="ns")
        self.notes_text.configure(yscrollcommand=note_scroll.set, state="disabled")

    def _make_preview_card(self, parent: ttk.Frame, title: str) -> ttk.LabelFrame:
        card = ttk.LabelFrame(parent, text=title, padding=10)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        panel = tk.Label(card, text="No preview loaded", bg="#f5f6f8", fg="#555", relief="groove", bd=1)
        panel.grid(row=0, column=0, sticky="nsew")
        panel.configure(width=52, height=24, justify="center")
        card.preview_panel = panel  # type: ignore[attr-defined]
        return card

    def _browse_root(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.root_path_var.get() or str(Path.cwd()))
        if folder:
            self.root_path_var.set(folder)

    def _set_notes(self, notes: list[str]) -> None:
        self.notes_text.configure(state="normal")
        self.notes_text.delete("1.0", tk.END)
        for note in notes or []:
            self.notes_text.insert(tk.END, f"- {note}\n")
        self.notes_text.configure(state="disabled")

    def _load_project(self) -> None:
        root = Path(self.root_path_var.get()).expanduser()
        if not root.exists():
            messagebox.showerror("Folder not found", f"Folder does not exist:\n{root}")
            return
        self._cases = find_histology_cases(root)
        self.case_tree.delete(*self.case_tree.get_children())
        for idx, case in enumerate(self._cases):
            self.case_tree.insert("", "end", iid=str(idx), values=(case["case_name"], case["overview_name"]))
        self.case_name_var.set(f"{len(self._cases)} histology case(s) found")
        self.status_var.set(f"Loaded source folder: {root}")
        self._selected_case = None
        self._set_notes([])
        self._show_placeholder("main")
        self._show_placeholder("label")

    def _show_placeholder(self, which: str, text: str = "No preview loaded") -> None:
        card = self.main_card if which == "main" else self.label_card
        panel = card.preview_panel  # type: ignore[attr-defined]
        panel.configure(image="", text=text)
        if which == "main":
            self._main_photo = None
        else:
            self._label_photo = None

    def _on_case_select(self, _event=None) -> None:
        sel = self.case_tree.selection()
        if not sel:
            return
        case = self._cases[int(sel[0])]
        self._selected_case = case
        self.case_name_var.set(case["case_name"])
        self.status_var.set(f"Loading preview: {case['overview_name']}")
        self.new_name_var.set("")
        preview = load_histology_preview_pair(case["overview_path"])
        if preview.get("error"):
            messagebox.showerror("Preview error", str(preview["error"]))
            return
        self._update_preview(self.main_card.preview_panel, preview.get("main_b64", ""), which="main")
        self._update_preview(self.label_card.preview_panel, preview.get("label_b64", ""), which="label")
        self._set_notes(preview.get("notes", []))
        self.status_var.set(f"Preview loaded from {Path(preview['overview_path']).name}")

    def _update_preview(self, panel: tk.Label, b64_text: str, which: str) -> None:
        if not b64_text:
            panel.configure(image="", text="Preview not available")
            if which == "main":
                self._main_photo = None
            else:
                self._label_photo = None
            return
        from base64 import b64decode
        from io import BytesIO
        from PIL import Image

        img = Image.open(BytesIO(b64decode(b64_text))).convert("RGB")
        img.thumbnail((680, 680), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        panel.configure(image=photo, text="")
        panel.image = photo  # type: ignore[attr-defined]
        if which == "main":
            self._main_photo = photo
        else:
            self._label_photo = photo

    def _apply_rename(self) -> None:
        if self._selected_case is None:
            messagebox.showwarning("No case selected", "Select a histology case first.")
            return
        new_name = sanitize_name(self.new_name_var.get().strip(), fallback="")
        if not new_name:
            messagebox.showwarning("Missing name", "Enter a new folder name before renaming.")
            return
        old_path = Path(self._selected_case["case_dir"]).expanduser().resolve()
        new_path = old_path.with_name(new_name)
        if new_path.exists():
            messagebox.showerror("Rename failed", f"Target already exists:\n{new_path}")
            return
        try:
            old_path.rename(new_path)
            rename_map = new_path / ".dataprocess_rename_map.json"
            rename_map.write_text(
                json.dumps({"old_path": str(old_path), "new_path": str(new_path)}, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            messagebox.showerror("Rename failed", str(exc))
            return

        self.status_var.set(f"Renamed to {new_path.name}")
        messagebox.showinfo("Rename complete", f"Folder renamed to:\n{new_path}")
        self.root_path_var.set(str(new_path.parent))
        self._load_project()


def main() -> None:
    app = HistologyNamingGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
