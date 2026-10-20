"""soundbank.py - Soundbank-Tab fuer den Synth.

Hierarchie: WAV -> PATCHES -> SOUNDBANK -> SEQUENCER (alle relativ zum Programmordner).
Eine Bank ist eine JSON-Datei mit einer geordneten Liste von Patch-Verweisen.
Patch-Dateien werden hier nie veraendert oder geloescht.
"""
import json
import ntpath
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from tkinter import font as tkfont

import patch as patchlib
from engine import Engine
from patchload import load_patch_into
from config import BASE_DIR as BASE, sub_dir

# ---- Konstanten
BANK_FORMAT = "synth-bank"
BANK_VERSION = 1
SUBDIRS = ("WAV", "PATCHES", "SOUNDBANK", "SEQUENCER")

PREVIEW_KEY = "_soundbank_preview"

# Start-Oktave pro Patch (gleiche Nummerierung wie F1-F5; Oktave 3 = Taste Q spielt C4)
OCT_MIN, OCT_MAX, OCT_DEFAULT = 1, 5, 2
DEFAULT_CATEGORIES = ("Bass", "Lead", "Pad", "Keys", "Strings", "Perkussion", "FX", "Sonstige")

ALL_LABEL = "Alle"
NONE_LABEL = "(ohne Kategorie)"
SORT_MODES = ("Bankreihenfolge", "Name", "Kategorie")

TILE_W, TILE_H, TILE_GAP = 240, 130, 6

WAVE_LABELS = {"sine": "Sinus", "square": "Rechteck", "saw": "Sägezahn", "noise": "Rauschen"}
KIND_COLORS = {
    "sine": "#2f6fb0",
    "square": "#c0701a",
    "saw": "#2e8b57",
    "noise": "#6b6b6b",
    "sample": "#8a3fa0",
    "missing": "#3a3a3a",
}
ACTIVE_COLOR = "#ffe14d"
IDLE_OUTLINE = "#202020"

# Effektname -> (Kürzel, Farbe)
FX_CHIPS = {
    "Vocoder": ("VOC", "#ff7bd5"),
    "Filter": ("FIL", "#8be9fd"),
    "Offset-LFO": ("LFO", "#b0b0ff"),
    "Overdrive": ("OVD", "#ff9966"),
    "Fuzz": ("FUZ", "#ff6666"),
    "Distortion": ("DIS", "#ff4d4d"),
    "Bitcrusher": ("BIT", "#d6d65c"),
    "Flanger": ("FLA", "#5cf0a0"),
    "Chorus": ("CHO", "#66d9ff"),
    "Phaser": ("PHA", "#c2a0ff"),
    "Tremolo": ("TRE", "#ffd166"),
    "Echo": ("ECH", "#a0e0a0"),
    "Reverb": ("REV", "#f0f0f0"),
}


# ---- Pfade
def ensure_dirs():
    for n in SUBDIRS:
        try:
            os.makedirs(sub_dir(n), exist_ok=True)
        except OSError:
            pass


def to_rel(path):
    """Absoluter Pfad -> relativ zum Programmordner (None, wenn ausserhalb)."""
    try:
        rel = os.path.relpath(os.path.abspath(path), BASE)
    except ValueError:
        return None
    if rel == ".." or rel.startswith(".." + os.sep):
        return None
    return os.path.normpath(rel)


def to_abs(rel):
    return os.path.normpath(os.path.join(BASE, os.path.normpath(rel)))


def norm_key(rel):
    return os.path.normcase(os.path.normpath(rel))

# ---- Einstellungen (settings.json neben dem Programm)
SETTINGS_FILE = os.path.join(BASE, "settings.json")


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(d):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except OSError:
        pass

# ---- Bank-Datei
def clamp_oct(v):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = OCT_DEFAULT
    return max(OCT_MIN, min(OCT_MAX, v))


def read_bank(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if d.get("format") != BANK_FORMAT:
        raise ValueError("Das ist keine Soundbank-Datei.")
    if int(d.get("version", 0)) > BANK_VERSION:
        raise ValueError("Soundbank stammt aus einer neueren Programmversion.")
    entries = []
    seen = set()
    for e in d.get("patches", []):
        rel = e.get("file")
        if not rel:
            continue
        k = norm_key(rel)
        if k in seen:
            continue
        seen.add(k)

        entries.append({"name": e.get("name") or os.path.splitext(ntpath.basename(rel))[0],
                "file": os.path.normpath(rel),
                "octave": clamp_oct(e.get("octave", OCT_DEFAULT)),
                "category": str(e.get("category") or "").strip()})

    return d.get("name", ""), entries


def write_bank(path, name, entries):
    d = {
        "format": BANK_FORMAT,
        "version": BANK_VERSION,
        "name": name,
        "patches": [{"name": e["name"], "file": e["file"],
                     "octave": clamp_oct(e.get("octave", OCT_DEFAULT)),
                     "category": e.get("category", "")} for e in entries],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


# ---- Kacheldaten aus dem Patch
def build_info(rel):
    p = to_abs(rel)
    if not os.path.isfile(p):
        return {"missing": True, "reason": "Patch fehlt"}
    try:
        d = patchlib.read_patch(p)
    except Exception:
        return {"missing": True, "reason": "Patch ungültig"}
    src = d.get("source", {}) or {}
    smp = src.get("sample") or {}
    is_sample = src.get("type") == "sample" and bool(smp)
    extras = []
    if is_sample:
        wav = ntpath.basename(smp.get("file") or smp.get("file_abs") or "?")
        line2 = "Sample: " + wav
        kind = "sample"
        extras.append("Root " + str(src.get("root", "C4")))
        if src.get("loop"):
            extras.append("Loop")
        if patchlib.resolve_sample(smp, p) is None:
            extras.append("! WAV fehlt")
    else:
        wave = src.get("wave", "sine")
        kind = wave if wave in WAVE_LABELS else "sine"
        line2 = "Generator: " + WAVE_LABELS[kind]
        try:
            u = int(d.get("unison", 1))
        except (TypeError, ValueError):
            u = 1
        if u > 1:
            extras.append("Unisono %d" % u)
    mod = d.get("modulator")
    if mod:
        extras.append("Mod: " + ntpath.basename(mod.get("file") or mod.get("file_abs") or "?"))
    fxs = [e.get("type", "?") for e in d.get("effects", []) if e.get("enabled")]
    return {"missing": False, "kind": kind, "line2": line2,
            "line3": " · ".join(extras), "effects": fxs}


# ---- Kachel
class Tile(tk.Canvas):
    def __init__(self, parent, tab, entry):
        super().__init__(parent, width=TILE_W, height=TILE_H, highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=0)
        self.tab = tab
        self.entry = entry
        entry["octave"] = clamp_oct(entry.get("octave", OCT_DEFAULT))
        entry["category"] = str(entry.get("category") or "")
        self.info = build_info(entry["file"])
        self.active = False
        self.oct_var = tk.IntVar(value=entry["octave"])
        self.spin = tk.Spinbox(self, from_=OCT_MIN, to=OCT_MAX, width=2, justify="center",
                               textvariable=self.oct_var, state="readonly",
                               readonlybackground="white", command=self.on_spin,
                               takefocus=0, font=("Segoe UI", 9, "bold"))
        self.bind("<ButtonPress-1>", lambda e: tab.on_tile_press(self))
        self.bind("<ButtonRelease-1>", lambda e: tab.on_tile_release())
        self.bind("<Button-3>", lambda e: tab.on_tile_menu(self, e))
        self.bind("<Double-Button-1>", lambda e: tab.on_tile_edit(self))
        self.draw()

    def on_spin(self):
        v = clamp_oct(self.oct_var.get())
        if v != self.entry["octave"]:
            self.entry["octave"] = v
            self.tab.set_dirty()
        if self.active:
            self.tab.app.set_octave(v)
        else:
            self.after_idle(lambda: self.tab.activate(self))
        self.tab.root.focus_set()

    def set_octave(self, v):
        v = clamp_oct(v)
        if v != self.entry["octave"]:
            self.entry["octave"] = v
            self.oct_var.set(v)
            self.tab.set_dirty()

    def set_active(self, flag):
        self.active = flag
        self.draw()

    def fit(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def draw(self):
        self.delete("all")
        info = self.info
        kind = "missing" if info["missing"] else info["kind"]
        self.configure(bg=KIND_COLORS[kind])
        bw = 4 if self.active else 1
        self.create_rectangle(bw // 2 + 1, bw // 2 + 1, TILE_W - bw // 2 - 1, TILE_H - bw // 2 - 1,
                              outline=ACTIVE_COLOR if self.active else IDLE_OUTLINE, width=bw)
        f_title, f_text, f_chip = self.tab.f_title, self.tab.f_text, self.tab.f_chip
        title = ("▶ " if self.active else "") + self.entry["name"]
        self.create_text(10, 9, anchor="nw", fill="white", font=f_title,
                         text=self.fit(title, f_title, TILE_W - 95))
        self.create_text(TILE_W - 52, 11, anchor="ne", fill="white", font=f_chip, text="Okt.")
        self.create_window(TILE_W - 8, 8, anchor="ne", window=self.spin)

        if self.entry["category"]:
            self.create_text(10, 84 if info["missing"] else 68, anchor="nw",
                             fill="#ffe9a8", font=f_chip, text="# " + self.entry["category"])
        if info["missing"]:
            self.create_text(10, 38, anchor="nw", fill="#ff9090", font=f_text, text=info["reason"])
            self.create_text(10, 58, anchor="nw", fill="#bbbbbb", font=f_text,
                             text=self.fit(self.entry["file"], f_text, TILE_W - 20))
            return
        self.create_text(10, 34, anchor="nw", fill="white", font=f_text,
                         text=self.fit(info["line2"], f_text, TILE_W - 20))
        if info["line3"]:
            self.create_text(10, 52, anchor="nw", fill="#f0f0f0", font=f_text,
                             text=self.fit(info["line3"], f_text, TILE_W - 20))
        x0, y0, rowh = 10, TILE_H - 46, 19
        x, y, row = x0, y0, 0
        fxs = info["effects"]
        for i, name in enumerate(fxs):
            abbr, col = FX_CHIPS.get(name, (name[:3].upper(), "#cccccc"))
            w = f_chip.measure(abbr) + 10
            if x + w > TILE_W - 10:
                row += 1
                x, y = x0, y0 + rowh
            if row >= 2:
                self.create_text(x, y + 9, anchor="w", fill="white", font=f_chip,
                                 text="+%d" % (len(fxs) - i))
                break
            self.create_rectangle(x, y, x + w, y + 16, fill=col, outline="")
            self.create_text(x + w / 2, y + 8, fill="black", font=f_chip, text=abbr)
            x += w + 4


# ---- Tab
class SoundbankTab(ttk.Frame):
    def __init__(self, nb, app):
        super().__init__(nb)
        self.app = app
        self.root = app.root
        ensure_dirs()
        self.f_title = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.f_text = tkfont.Font(family="Segoe UI", size=9)
        self.f_chip = tkfont.Font(family="Segoe UI", size=8, weight="bold")

        self.tiles = []
        self.active = None
        self.bank_path = None
        self.bank_name = ""
        self.dirty = False
        self.playing = False
        self._cols = 0

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        for text, cmd in (("Neue Bank", self.on_new), ("Bank laden…", self.on_load),
                          ("Bank speichern", self.on_save), ("Speichern unter…", self.on_save_as)):
            ttk.Button(bar, text=text, command=lambda c=cmd: self._run(c), takefocus=0
                       ).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        for text, cmd in (("Patch hinzufügen…", self.on_add), ("Patch entfernen", self.on_remove),
                          ("◀", lambda: self.on_move(-1)), ("▶", lambda: self.on_move(1))):
            b = ttk.Button(bar, text=text, command=lambda c=cmd: self._run(c), takefocus=0)
            if len(text) == 1:
                b.configure(width=3)
            b.pack(side="left", padx=2)

        flt = ttk.Frame(self, padding=(8, 0, 8, 4))
        flt.pack(fill="x")
        ttk.Label(flt, text="Kategorie:").pack(side="left")
        self.cat_var = tk.StringVar(value=ALL_LABEL)
        self.cb_cat = ttk.Combobox(flt, textvariable=self.cat_var, width=18, state="readonly",
                                   values=[ALL_LABEL, NONE_LABEL])
        self.cb_cat.pack(side="left", padx=(4, 12))
        self.cb_cat.bind("<<ComboboxSelected>>", self.on_combo_change)
        ttk.Label(flt, text="Suche:").pack(side="left")
        self.search_var = tk.StringVar()
        ent = ttk.Entry(flt, textvariable=self.search_var, width=22)
        ent.pack(side="left", padx=(4, 12))
        ent.bind("<Return>", lambda e: self.root.focus_set())
        ent.bind("<Escape>", lambda e: self.root.focus_set())
        ttk.Label(flt, text="Sortierung:").pack(side="left")
        self.sort_var = tk.StringVar(value=SORT_MODES[0])
        cb_sort = ttk.Combobox(flt, textvariable=self.sort_var, width=16, state="readonly",
                               values=SORT_MODES)
        cb_sort.pack(side="left", padx=(4, 12))
        cb_sort.bind("<<ComboboxSelected>>", self.on_combo_change)
        ttk.Button(flt, text="Zurücksetzen", takefocus=0,
                   command=lambda: self._run(self.reset_view)).pack(side="left")
        self.lbl_count = ttk.Label(flt, text="")
        self.lbl_count.pack(side="left", padx=12)
        self.search_var.trace_add("write", lambda *_: self.on_view_change())

        info = ttk.Frame(self, padding=(8, 0))
        info.pack(fill="x")
        ttk.Label(info, text="Bank:").pack(side="left")
        self.lbl_bank = ttk.Label(info, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_bank.pack(side="left", padx=4)
        self.lbl_status = ttk.Label(info, text="Bereit")
        self.lbl_status.pack(side="left", padx=16)

        self.area = ttk.Frame(self)
        self.area.pack(fill="both", expand=True, padx=8, pady=6)
        self.cv = tk.Canvas(self.area, highlightthickness=0, bg="#262626", takefocus=0)
        sb = ttk.Scrollbar(self.area, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.cv, bg="#262626")
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>",
                        lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.bind("<Configure>", self.on_canvas_resize)
        self.root.bind_all("<MouseWheel>", self.on_wheel, add="+")
        self.update_title()
        self.app.octave.trace_add("write", self._octave_changed)
        self.autoload()

    # ---- Hilfen
    def _run(self, cmd):
        try:
            cmd()
        finally:
            self.root.focus_set()

    def status(self, text):
        self.lbl_status.config(text=text)

    def update_title(self):
        name = self.bank_name or "(neue Bank)"
        self.lbl_bank.config(text=name + (" *" if self.dirty else ""))

    def set_dirty(self, flag=True):
        self.dirty = flag
        self.update_title()

    def entries(self):
        return [t.entry for t in self.tiles]

    # ---- Layout
    def on_canvas_resize(self, e):
        self.cv.itemconfigure(self._win, width=e.width)
        self.regrid()

    def regrid(self, force=False):
        if force:
            self._update_filter_values()
        w = self.cv.winfo_width()
        cols = max(1, w // (TILE_W + 2 * TILE_GAP))
        if cols == self._cols and not force:
            return
        self._cols = cols
        vis = self.visible_tiles()
        shown = {id(t) for t in vis}
        for t in self.tiles:
            if id(t) not in shown:
                t.grid_remove()
        for i, t in enumerate(vis):
            t.grid(row=i // cols, column=i % cols, padx=TILE_GAP, pady=TILE_GAP)
        self.lbl_count.config(text="%d von %d" % (len(vis), len(self.tiles)))

    def _update_filter_values(self):
        cats = sorted({t.entry.get("category", "") for t in self.tiles} - {""},
                      key=str.lower)
        vals = [ALL_LABEL, NONE_LABEL] + cats
        self.cb_cat.configure(values=vals)
        if self.cat_var.get() not in vals:
            self.cat_var.set(ALL_LABEL)

    def visible_tiles(self):
        ts = list(self.tiles)
        cat = self.cat_var.get()
        if cat == NONE_LABEL:
            ts = [t for t in ts if not t.entry.get("category", "")]
        elif cat != ALL_LABEL:
            ts = [t for t in ts if t.entry.get("category", "") == cat]
        q = self.search_var.get().strip().lower()
        if q:
            ts = [t for t in ts if q in t.entry["name"].lower()
                  or q in t.entry.get("category", "").lower()]
        mode = self.sort_var.get()
        if mode == "Name":
            ts.sort(key=lambda t: t.entry["name"].lower())
        elif mode == "Kategorie":
            ts.sort(key=lambda t: (not t.entry.get("category", ""),
                                   t.entry.get("category", "").lower(),
                                   t.entry["name"].lower()))
        return ts

    def view_is_default(self):
        return (self.cat_var.get() == ALL_LABEL and not self.search_var.get().strip()
                and self.sort_var.get() == SORT_MODES[0])

    def on_view_change(self):
        self.regrid(force=True)
        self.cv.yview_moveto(0)

    def on_combo_change(self, event=None):
        self.on_view_change()
        self.root.focus_set()

    def reset_view(self):
        self.cat_var.set(ALL_LABEL)
        self.search_var.set("")
        self.sort_var.set(SORT_MODES[0])
        self.on_view_change()

    def on_wheel(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        while w is not None:
            if w is self.area:
                self.cv.yview_scroll(int(-e.delta / 120), "units")
                return
            w = w.master

    # ---- Oktave: F-Tasten / Oktav-Auswahl folgen der aktiven Kachel
    def _octave_changed(self, *_):
        t = self.active
        if t is None:
            return
        try:
            if self.app.nb.select() != str(self):
                return
            t.set_octave(int(self.app.octave.get()))
        except (tk.TclError, ValueError):
            pass

    # ---- Tab-Wechsel
    def on_tab_changed(self, event=None):
        self.release_note()
        if self.app.nb.select() == str(self):
            self.refresh_tiles()
        self.root.focus_set()

    def refresh_tiles(self):
        for t in self.tiles:
            t.info = build_info(t.entry["file"])
            t.draw()

    # ---- Klang
    def release_note(self):
        if self.playing:
            self.playing = False
            self.app.eng.note_off(PREVIEW_KEY)

    def select(self, tile):
        if self.active is tile:
            return
        if self.active is not None:
            self.active.set_active(False)
        self.active = tile
        if tile is not None:
            tile.set_active(True)

    def activate(self, tile):
        """Kachel auswählen und Patch laden (ohne Ton). True bei Erfolg."""
        self.release_note()
        self.select(tile)
        tile.info = build_info(tile.entry["file"])
        tile.draw()
        if tile.info["missing"]:
            self.status("%s: %s" % (tile.entry["name"], tile.info["reason"]))
            return False
        path = to_abs(tile.entry["file"])
        try:
            d = patchlib.read_patch(path)
            self.app.reset_defaults()
            self.app.apply_patch(d, path)
        except Exception as e:
            messagebox.showerror("Soundbank", "Patch konnte nicht geladen werden:\n%s" % e)
            return False
        self.status("Aktiv: " + tile.entry["name"])
        self.app.set_octave(tile.entry["octave"])
        return True

    def on_tile_press(self, tile):
        self.root.focus_set()
        if not self.activate(tile):
            return
        self.app.eng.note_on(PREVIEW_KEY, (tile.entry["octave"] - 3) * 12)
        self.playing = True

    def on_tile_release(self):
        self.release_note()
        self.root.focus_set()

    def on_tile_edit(self, tile):
        self.release_note()
        if not self.activate(tile):
            return
        self.app.set_edit_target(to_abs(tile.entry["file"]))
        self.app.status.config(
            text="Bearbeite: %s - mit 'Zurück in die Bank' speichern" % tile.entry["name"])
        self.app.nb.select(0)
        self.root.focus_set()

    def test_parallel(self, tile):
        path = to_abs(tile.entry["file"])
        try:
            d = patchlib.read_patch(path)
            eng = Engine()
            warns = load_patch_into(eng, d, path)
        except Exception as e:
            messagebox.showerror("Test", str(e))
            return
        if warns:
            self.status("Hinweis: " + "; ".join(warns))
        else:
            self.status("Parallel: " + tile.entry["name"])
        mixer = self.app.mixer
        mixer.add_part(eng)
        eng.note_on("_test_part", (tile.entry["octave"] - 3) * 12)
        self.root.after(3000, lambda: eng.note_off("_test_part"))
        self.root.after(3000 + int(eng.R * 1000) + 2500, lambda: mixer.remove_part(eng))

    # ---- Kacheln verwalten
    def clear_tiles(self):
        self.release_note()
        for t in self.tiles:
            t.destroy()
        self.tiles = []
        self.active = None
        self._cols = 0

    def add_entry(self, entry):
        t = Tile(self.inner, self, entry)
        self.tiles.append(t)
        return t

    def on_add(self):
        paths = filedialog.askopenfilenames(
            initialdir=sub_dir("PATCHES"),
            filetypes=[("Synth-Patch (JSON)", "*.json")])
        if not paths:
            return
        have = {norm_key(t.entry["file"]) for t in self.tiles}
        added, dup, outside, bad = 0, 0, [], []
        for p in paths:
            rel = to_rel(p)
            pdir = os.path.normcase(sub_dir("PATCHES")) + os.sep
            if rel is None or not os.path.normcase(os.path.abspath(p)).startswith(pdir):
                outside.append(os.path.basename(p))
                continue
            if norm_key(rel) in have:
                dup += 1
                continue
            try:
                patchlib.read_patch(p)
            except Exception:
                bad.append(os.path.basename(p))
                continue
            have.add(norm_key(rel))
            name = os.path.splitext(os.path.basename(p))[0]
            self.add_entry({"name": name, "file": rel, "octave": OCT_DEFAULT})
            added += 1
        if added:
            self.regrid(force=True)
            self.set_dirty()
        msg = "%d hinzugefügt" % added
        if dup:
            msg += ", %d schon vorhanden" % dup
        self.status(msg)
        problems = []
        if outside:
            problems.append("Nicht im Ordner PATCHES:\n  " + "\n  ".join(outside))
        if bad:
            problems.append("Keine gültigen Patches:\n  " + "\n  ".join(bad))
        if problems:
            messagebox.showwarning("Patch hinzufügen", "\n\n".join(problems))

    def remove_tile(self, tile):
        self.release_note()
        self.tiles.remove(tile)
        if self.active is tile:
            self.active = None
        tile.destroy()
        self.regrid(force=True)
        self.set_dirty()
        self.status("Aus der Bank entfernt (Patch-Datei bleibt erhalten)")

    def on_remove(self):
        t = self.active
        if t is None:
            self.status("Keine Kachel ausgewählt")
            return
        self.remove_tile(t)

    def on_tile_menu(self, tile, event):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Umbenennen…",
                      command=lambda: self._run(lambda: self.rename_tile(tile)))

        if not tile.info["missing"]:
            m.add_command(label="Im Synth bearbeiten",
                          command=lambda: self._run(lambda: self.on_tile_edit(tile)))
            m.add_command(label="Test: parallel spielen (3 s)",
                          command=lambda: self._run(lambda: self.test_parallel(tile)))

        sub = tk.Menu(m, tearoff=0)
        cur = tile.entry.get("category", "")
        for c in self.category_choices():
            sub.add_command(label=("✓ " if c == cur else "    ") + c,
                            command=lambda c=c: self._run(lambda: self.set_category(tile, c)))
        sub.add_separator()
        sub.add_command(label="Neue Kategorie…",
                        command=lambda: self._run(lambda: self.ask_new_category(tile)))
        sub.add_command(label="Keine Kategorie",
                        command=lambda: self._run(lambda: self.set_category(tile, "")))
        m.add_cascade(label="Kategorie", menu=sub)

        if tile.info["missing"]:
            m.add_command(label="Patch neu verknüpfen…",
                          command=lambda: self._run(lambda: self.relink_tile(tile)))
        m.add_separator()
        m.add_command(label="Aus Bank entfernen",
                      command=lambda: self._run(lambda: self.remove_tile(tile)))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def rename_tile(self, tile):
        new = simpledialog.askstring("Umbenennen", "Neuer Anzeigename:",
                                     initialvalue=tile.entry["name"], parent=self.root)
        if new is None:
            return
        new = new.strip()
        if not new or new == tile.entry["name"]:
            return
        tile.entry["name"] = new
        tile.draw()
        self.set_dirty()
        self.regrid(force=True)
        self.status("Umbenannt: " + new)

    def category_choices(self):
        extra = sorted({t.entry.get("category", "") for t in self.tiles}
                       - set(DEFAULT_CATEGORIES) - {""}, key=str.lower)
        return list(DEFAULT_CATEGORIES) + extra

    def set_category(self, tile, cat):
        cat = (cat or "").strip()
        if cat == tile.entry.get("category", ""):
            return
        tile.entry["category"] = cat
        tile.draw()
        self.set_dirty()
        self.regrid(force=True)
        self.status("Kategorie: %s" % (cat or "keine"))

    def ask_new_category(self, tile):
        new = simpledialog.askstring("Kategorie", "Name der neuen Kategorie:", parent=self.root)
        if new and new.strip():
            self.set_category(tile, new.strip())

    def _patch_rel(self, p):
        rel = to_rel(p)
        pdir = os.path.normcase(sub_dir("PATCHES")) + os.sep
        if rel is None or not os.path.normcase(os.path.abspath(p)).startswith(pdir):
            return None
        return rel

    def relink_tile(self, tile):
        p = filedialog.askopenfilename(
            initialdir=sub_dir("PATCHES"),
            filetypes=[("Synth-Patch (JSON)", "*.json")])
        if not p:
            return
        rel = self._patch_rel(p)
        if rel is None:
            messagebox.showwarning("Neu verknüpfen", "Der Patch muss im Ordner PATCHES liegen.")
            return
        others = {norm_key(t.entry["file"]) for t in self.tiles if t is not tile}
        if norm_key(rel) in others:
            messagebox.showwarning("Neu verknüpfen", "Dieser Patch ist schon in der Bank.")
            return
        try:
            patchlib.read_patch(p)
        except Exception as e:
            messagebox.showwarning("Neu verknüpfen", "Kein gültiger Patch:\n%s" % e)
            return
        tile.entry["file"] = rel
        tile.info = build_info(rel)
        tile.draw()
        self.set_dirty()
        self.status("Neu verknüpft: " + tile.entry["name"])

    def on_move(self, delta):
        t = self.active
        if t is None:
            self.status("Keine Kachel ausgewählt")
            return

        if not self.view_is_default():
            self.status("Verschieben geht nur in der Bankreihenfolge ohne Filter")
            return

        i = self.tiles.index(t)
        j = i + delta
        if j < 0 or j >= len(self.tiles):
            return
        self.tiles[i], self.tiles[j] = self.tiles[j], self.tiles[i]
        self.regrid(force=True)
        self.set_dirty()

    # ---- Bank laden / speichern
    def confirm_discard(self):
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel(
            "Soundbank", "Die Bank '%s' wurde geändert.\nVorher speichern?"
            % (self.bank_name or "(neue Bank)"))
        if r is None:
            return False
        if r:
            return self.on_save()
        return True

    def on_new(self):
        if not self.confirm_discard():
            return
        self.clear_tiles()
        self.bank_path = None
        self.bank_name = ""
        self.set_dirty(False)
        self.status("Neue Bank")

    def load_bank_file(self, path, silent=False):
        try:
            _name, entries = read_bank(path)
        except Exception as e:
            if not silent:
                messagebox.showerror("Bank laden", str(e))
            return False
        self.clear_tiles()
        for e in entries:
            self.add_entry(e)
        self.regrid(force=True)
        self.bank_path = path
        self.bank_name = os.path.splitext(os.path.basename(path))[0]
        self.set_dirty(False)
        missing = sum(1 for t in self.tiles if t.info["missing"])
        self.status("Geladen: %d Patches%s" % (len(entries),
                    ", %d fehlen" % missing if missing else ""))
        return True

    def on_load(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(
            initialdir=sub_dir("SOUNDBANK"),
            filetypes=[("Soundbank (JSON)", "*.json")])
        if not path:
            return
        if self.load_bank_file(path):
            self.remember_bank(path)

    def remember_bank(self, path):
        rel = to_rel(path)
        if rel is None:
            return
        s = load_settings()
        s["last_bank"] = rel
        save_settings(s)

    def autoload(self):
        rel = load_settings().get("last_bank")
        if not rel:
            return
        path = to_abs(rel)
        if os.path.isfile(path):
            self.load_bank_file(path, silent=True)

    def on_save(self):
        if not self.bank_path:
            return self.on_save_as()
        return self._write(self.bank_path)

    def on_save_as(self):
        path = filedialog.asksaveasfilename(
            initialdir=sub_dir("SOUNDBANK"), defaultextension=".json",
            initialfile=(self.bank_name or "bank") + ".json",
            filetypes=[("Soundbank (JSON)", "*.json")])
        if not path:
            return False
        return self._write(path)

    def _write(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            write_bank(path, name, self.entries())
        except Exception as e:
            messagebox.showerror("Bank speichern", str(e))
            return False
        self.bank_path = path
        self.bank_name = name
        self.set_dirty(False)
        self.remember_bank(path)
        self.status("Gespeichert: " + os.path.basename(path))
        return True
