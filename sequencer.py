"""sequencer.py - Sequencer-Tab: Songformat (synth-song) und Tab-Geruest."""
import ctypes
import math
import json
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import patch as patchlib
from engine import Engine
from patchload import load_patch_into
from soundbank import ensure_dirs, load_settings, save_settings, to_rel, to_abs

import patternedit
from config import SR, KEYMAP, sub_dir, VEL_MAX, vel_gain, NUM_TRACKS
from patterngrid import PatternGrid
from player import Player, build_plan, compile_song
from fxcmd import command_lines


UNDO_MAX = 100
SONG_FORMAT = "synth-song"
SONG_VERSION = 1
ROWS_MIN, ROWS_MAX, ROWS_DEFAULT = 1, 256, 64
BPM_MIN, BPM_MAX, BPM_DEFAULT = 20, 300, 120
RPB_MIN, RPB_MAX, RPB_DEFAULT = 1, 16, 4
OCT_MIN, OCT_MAX, OCT_DEFAULT = 1, 5, 2
PAN_MIN, PAN_MAX = -100, 100
NUDGE = {"plus": 1, "kp_add": 1, "minus": -1, "kp_subtract": -1,
         "asterisk": 12, "underscore": -12}   # deutsche Tastatur: Umschalt+ + = *, Umschalt+ - = _

HELP_LINES = [
    "Raster",
    "Klaviertasten            Note setzen (klingt kurz), Cursor rückt um Schritt weiter",
    ".                        Ende der klingenden Note",
    "Rück / Entf              Note an der Stelle löschen (mit Auswahl: Bereich leeren)",
    "Umschalt+Entf            aktive Spur im Pattern leeren (mit Rückfrage)",
    "Strg+Entf                ganzes Pattern leeren (mit Rückfrage)",
    "+ / -                    Note um einen Halbton höher / tiefer, klingt kurz",
    "* / _  (Umschalt)        Note um eine Oktave höher / tiefer",
    "Hoch / Runter            Zeile wechseln (mit Ringschluss)",
    "Links / Rechts           Feld wechseln: Note, Vol, Code, Wert, dann nächste Spur",
    "Ziffern (Vol/Code/Wert)  zweistellig eingeben, Rück/Entf = leer bzw. 00",
    "F11                      Liste der Befehle (Codes)",
    "Bild auf/ab, Pos1, Ende  ein Takt / erste / letzte Zeile",
    "Umschalt+Pfeile, ...     Bereich in der Spur markieren",
    "Strg+A                   ganze Spur markieren",
    "Strg+C / X / V           kopieren / ausschneiden / einfügen (Zielspur-Klang)",
    "Strg+Z / Strg+Y          Rückgängig / Wiederholen",
    "Strg+Bild auf/ab Lautstärke der Note ±10 (Umschalt: ±1), Strg+Pos1 = leer"
    "Esc                      Auswahl aufheben",
    "F1 - F5                  Oktave der gewählten Spur",
    "M / S                    Spur muten / Solo (nur Session, nicht gespeichert)",
    "Leertaste (halten)       Testton der gewählten Spur",
    "",
    "Maus",
    "Klick auf Zelle          Cursor setzen",
    "Ziehen                   Bereich in der Spur markieren",
    "Umschalt+Klick           Auswahl erweitern",
    "Umschalt+Ziehen          Auswahl / Note an die Mausposition kopieren",
    "Strg+Ziehen              Auswahl / Note verschieben",
    "(außerhalb loslassen     Aktion abbrechen, Markierung bleibt)",
    "Klick auf Spurkopf       Spur wählen",
    "Mausrad                  Raster scrollen",
    "",
    "F12                      diese Hilfe",
]

try:
    _gtc = ctypes.windll.kernel32.GetTickCount
    _gtc.restype = ctypes.c_uint32
except Exception:
    _gtc = None

def event_lag(e):
    """Sekunden zwischen Tastendruck (Windows-Zeitstempel) und Verarbeitung im Tk-Loop."""
    if _gtc is None or e is None:
        return 0.0
    d = (_gtc() - e.time) & 0xFFFFFFFF
    return d / 1000.0 if d < 2000 else 0.0


def pan_text(p):
    return "C" if p == 0 else (f"L{-p}" if p < 0 else f"R{p}")


def _clamp(v, lo, hi, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))

def new_track():
    return {"name": "", "patch": None, "octave": OCT_DEFAULT, "mute": False, "pan": 0}

def new_pattern(index, rows=ROWS_DEFAULT):
    return {"name": f"P{index:02d}", "rows": rows, "notes": [], "cmds": []}

def new_song():
    return {"format": SONG_FORMAT, "version": SONG_VERSION, "name": "",
            "bpm": BPM_DEFAULT, "rows_per_beat": RPB_DEFAULT,
            "tracks": [new_track() for _ in range(NUM_TRACKS)],
            "patterns": [new_pattern(0)], "order": [0]}


def samples_per_row(bpm, rpb):
    return SR * 60.0 / (bpm * rpb)


def read_song(path):
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or d.get("format") != SONG_FORMAT:
        raise ValueError("Das ist keine Song-Datei.")
    if int(d.get("version", 0)) > SONG_VERSION:
        raise ValueError("Song stammt aus einer neueren Programmversion.")
    song = new_song()
    song["name"] = str(d.get("name") or "")
    song["bpm"] = _clamp(d.get("bpm"), BPM_MIN, BPM_MAX, BPM_DEFAULT)
    song["rows_per_beat"] = _clamp(d.get("rows_per_beat"), RPB_MIN, RPB_MAX, RPB_DEFAULT)
    for i, t in enumerate((d.get("tracks") or [])[:NUM_TRACKS]):
        if not isinstance(t, dict):
            continue
        song["tracks"][i] = {
            "name": str(t.get("name") or ""),
            "patch": t.get("patch") or None,
            "octave": _clamp(t.get("octave"), OCT_MIN, OCT_MAX, OCT_DEFAULT),
            "mute": bool(t.get("mute", False)),
            "pan": _clamp(t.get("pan"), PAN_MIN, PAN_MAX, 0)}
    pats = []
    for i, p in enumerate(d.get("patterns") or []):
        if not isinstance(p, dict):
            continue
        rows = _clamp(p.get("rows"), ROWS_MIN, ROWS_MAX, ROWS_DEFAULT)
        notes = []
        for n in p.get("notes") or []:
            try:
                r, tr, semi, ln = (int(x) for x in n[:4])
                v = n[4] if len(n) > 4 else None
                v = None if v is None else _clamp(v, 0, VEL_MAX, VEL_MAX)
            except (TypeError, ValueError):
                continue
            if 0 <= r < rows and 0 <= tr < NUM_TRACKS:
                notes.append([r, tr, semi, max(1, ln), v])
        notes.sort()
        cmds = []
        for c in p.get("cmds") or []:
            try:
                cr, ctr, ccode, cval = (int(x) for x in c[:4])
            except (TypeError, ValueError):
                continue
            if 0 <= cr < rows and 0 <= ctr < NUM_TRACKS:
                cmds.append([cr, ctr, _clamp(ccode, 0, 99, 0), _clamp(cval, 0, 99, 0)])
        cmds.sort()
        pats.append({"name": str(p.get("name") or f"P{i:02d}"),
                     "rows": rows, "notes": notes, "cmds": cmds})

    if pats:
        song["patterns"] = pats
    n = len(song["patterns"])
    order = [o for o in (d.get("order") or []) if isinstance(o, int) and 0 <= o < n]
    song["order"] = order or [0]
    return song


def write_song(path, song):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(song, f, indent=2, ensure_ascii=False)


class SequencerTab(ttk.Frame):
    def __init__(self, nb, app):
        super().__init__(nb)
        self.app = app
        self.root = app.root
        ensure_dirs()
        self._rel_pending = {}
        self.song = new_song()
        self.path = None
        self.dirty = False
        self.cur = 0                      # aktuelles Pattern (Index)
        self.cache = {}                    # WAV-Cache, ein Cache pro Song
        self.engines: list[Engine | None] = [None] * NUM_TRACKS
        self.warns = [""] * NUM_TRACKS
        self.sel = 0                       # gewaehlte Spur
        self._pan_lock = False             # sperrt on_pan_move bei programmatischem set()
        self._pan_dirty = False
        self._space_down = False           # Leertaste als Test-Taste (gegen Auto-Repeat)
        self.player: Player | None = None
        self._poll_id = None
        self.test: tuple[Engine, tuple] | None = None   # (engine, key) waehrend Testton

        self.crow = 0                      # Cursorzeile im Raster
        self._edit_down = {}               # gedrueckte Klaviertasten -> (engine, key)
        self._oct_lock = False
        self._play_mode = "song"
        self._play_cur = 0
        self.rec_down = {}
        self.solo = None   # Spur (Index) oder None; gilt nur fuer die Session
        self._nudge_n = 0

        self.sel_a: int | None = None   # Ankerzeile der Auswahl
        self.sel_tr = 0                 # Spur der Auswahl
        self.clip: tuple[int, list, list] | None = None   # (Zeilen, Noten, Befehle)


        self._undo: list = []   # (Pattern, Noten) VOR einer Aenderung
        self._redo: list = []
        self._snap = None       # Stand nach der letzten Aenderung: (Pattern, Noten)
        self._drag_src = None   # Maus-Ziehen (Teil 2)
        self.field = 0               # 0 = Note, 1 = Lautstärke, 2 = Effekt
        self._digit_down = set()     # gedrückte Ziffern (gegen Auto-Repeat)
        self._ms_anchor = 0

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        for text, cmd in (("Neuer Song", self.on_new), ("Song laden", self.on_load),
                          ("Speichern", self.on_save), ("Speichern unter", self.on_save_as)):
            ttk.Button(bar, text=text, takefocus=0,
                       command=lambda c=cmd: self.run(c)).pack(side="left", padx=2)

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="MOD importieren…", takefocus=0,
                command=lambda: self.run(self.on_import_mod)).pack(side="left", padx=2)
        ttk.Button(bar, text="WAV/MP3 speichern", takefocus=0,
            command=lambda: self.run(self.on_export_audio)).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.bpm_var = tk.IntVar(value=BPM_DEFAULT)
        self.rpb_var = tk.IntVar(value=RPB_DEFAULT)
        ttk.Label(bar, text="BPM").pack(side="left")
        self.sp_bpm = self._spin(bar, self.bpm_var, BPM_MIN, BPM_MAX)
        ttk.Label(bar, text="Zeilen/Beat").pack(side="left", padx=(8, 0))
        self.sp_rpb = self._spin(bar, self.rpb_var, RPB_MIN, RPB_MAX)
        self.lbl_time = ttk.Label(bar, text="")
        self.lbl_time.pack(side="left", padx=12)
        self.lbl_song = ttk.Label(bar, text="", font=("Segoe UI", 10, "bold"))
        self.lbl_song.pack(side="left", padx=12)

        tp = ttk.Frame(self, padding=(8, 0))
        tp.pack(fill="x")
        self.btn_play = ttk.Button(tp, text="▶ Play", takefocus=0,
                                   command=lambda: self.run(self.on_play))
        self.btn_play.pack(side="left", padx=2)
        self.btn_stopp = ttk.Button(tp, text="■ Stop", takefocus=0, state="disabled",
                                    command=lambda: self.run(self.stop_play))
        self.btn_stopp.pack(side="left", padx=2)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tp, text="Loop", variable=self.loop_var, takefocus=0,
                        command=self.root.focus_set).pack(side="left", padx=8)
        self.mode_var = tk.StringVar(value="song")
        for txt, val in (("Pattern", "pattern"), ("Song", "song")):
            ttk.Radiobutton(tp, text=txt, value=val, variable=self.mode_var, takefocus=0,
                            command=self.root.focus_set).pack(side="left", padx=2)
        ttk.Button(tp, text="Demo-Noten", takefocus=0,
                   command=lambda: self.run(self.on_demo)).pack(side="left", padx=12)
        self.lbl_play = ttk.Label(tp, text="Gestoppt", width=60)
        self.lbl_play.pack(side="left", padx=6)

        tp2 = ttk.Frame(self, padding=(8, 2))
        tp2.pack(fill="x")
        self.metro_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tp2, text="Metronom", variable=self.metro_var, takefocus=0,
                        command=self._on_metro).pack(side="left", padx=2)
        self.rec_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tp2, text="● Rec", variable=self.rec_var, takefocus=0,
                        command=self._on_rec_toggle).pack(side="left", padx=10)
        ttk.Label(tp2, text="Vorzähler (Beats)").pack(side="left")
        self.count_var = tk.IntVar(value=4)
        self._spin(tp2, self.count_var, 0, 8, cb=self.root.focus_set)
        ttk.Label(tp2, text="Korrektur (ms)").pack(side="left", padx=(10, 0))
        self.lat_var = tk.IntVar(value=_clamp(load_settings().get("rec_offset_ms"), -300, 300, 0))
        self._spin(tp2, self.lat_var, -300, 300, cb=self._on_lat, signed=True)
        ttk.Label(tp2, foreground="#666666",
                  text="Beim Einspielen werden Start und Ende auf die nächste Zeile gerundet"
                  ).pack(side="left", padx=10)

        body = ttk.Frame(self, padding=8)
        body.pack(fill="x")                     # statt fill="both", expand=True
        # lb_pat: height=8, lb_ord: height=8, self.tv (Spuren): height=8

        left = ttk.LabelFrame(body, text="Patterns", padding=6)
        left.pack(side="left", fill="y")

        pf = ttk.Frame(left)
        pf.pack(fill="x")
        self.lb_pat = tk.Listbox(pf, width=22, height=21, exportselection=False,
                                 takefocus=0)
        sb_pat = ttk.Scrollbar(pf, orient="vertical", command=self.lb_pat.yview)
        self.lb_pat.configure(yscrollcommand=sb_pat.set)
        sb_pat.pack(side="right", fill="y")
        self.lb_pat.pack(side="left", fill="x", expand=True)

        self.lb_pat.bind("<<ListboxSelect>>", self.on_pat_select)
        pb = ttk.Frame(left)
        pb.pack(fill="x", pady=4)
        for text, cmd in (("Neu", self.on_pat_new), ("Kopie", self.on_pat_copy),
                          ("Löschen", self.on_pat_del)):
            ttk.Button(pb, text=text, width=7, takefocus=0,
                       command=lambda c=cmd: self.run(c)).pack(side="left", padx=1)
        rf = ttk.Frame(left)
        rf.pack(fill="x")
        ttk.Label(rf, text="Zeilen").pack(side="left")
        self.rows_var = tk.IntVar(value=ROWS_DEFAULT)
        self.sp_rows = self._spin(rf, self.rows_var, ROWS_MIN, ROWS_MAX)

        mid = ttk.LabelFrame(body, text="Reihenfolge", padding=6)
        mid.pack(side="left", fill="y", padx=8)

        of = ttk.Frame(mid)
        of.pack(fill="x")
        self.lb_ord = tk.Listbox(of, width=14, height=21, exportselection=False,
                                 takefocus=0)
        sb_ord = ttk.Scrollbar(of, orient="vertical", command=self.lb_ord.yview)
        self.lb_ord.configure(yscrollcommand=sb_ord.set)
        sb_ord.pack(side="right", fill="y")
        self.lb_ord.pack(side="left", fill="x", expand=True)

        self._drag_idx = None
        self._drag_moved = False
        self.lb_ord.bind("<ButtonPress-1>", self.on_ord_press)
        self.lb_ord.bind("<B1-Motion>", self.on_ord_drag)
        self.lb_ord.bind("<ButtonRelease-1>", self.on_ord_release)
        self.lb_ord.bind("<Double-Button-1>", self.on_ord_dbl)

        ob = ttk.Frame(mid)
        ob.pack(fill="x", pady=4)
        for text, cmd in (("+ Pattern", self.on_ord_add), ("Entfernen", self.on_ord_del)):
            ttk.Button(ob, text=text, width=9, takefocus=0,
                       command=lambda c=cmd: self.run(c)).pack(side="left", padx=1)

        ob2 = ttk.Frame(mid)
        ob2.pack(fill="x")
        for text, cmd in (("▲", lambda: self.on_ord_move(-1)), ("▼", lambda: self.on_ord_move(1))):
            ttk.Button(ob2, text=text, width=4, takefocus=0,
                       command=lambda c=cmd: self.run(c)).pack(side="left", padx=1)

        right = ttk.LabelFrame(body, text="Spuren", padding=6)
        right.pack(side="left", fill="both", expand=True)

        tvf = ttk.Frame(right)
        tvf.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(tvf, columns=("nr", "patch", "okt", "pan", "mute", "hinweis"),
                               show="headings", height=16, selectmode="browse",
                               takefocus=0)
        for c, t, w in (("nr", "Spur", 45), ("patch", "Patch", 300), ("okt", "Okt.", 45),
                        ("pan", "Pan", 50), ("mute", "Mute", 45), ("hinweis", "Hinweis", 220)):

            self.tv.heading(c, text=t)
            self.tv.column(c, width=w, anchor="w")

        sb_tv = ttk.Scrollbar(tvf, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb_tv.set)
        sb_tv.pack(side="right", fill="y")
        self.tv.pack(side="left", fill="both", expand=True)
        self.tv.bind("<<TreeviewSelect>>", self.on_track_select)
        tb = ttk.Frame(right)
        tb.pack(fill="x", pady=(6, 0))
        self.btn_patch = ttk.Button(tb, text="Patch wählen…", takefocus=0,
                                    command=lambda: self.run(self.on_patch_menu))
        self.btn_patch.pack(side="left", padx=2)
        ttk.Label(tb, text="Okt.").pack(side="left", padx=(10, 0))
        self.toct_var = tk.IntVar(value=OCT_DEFAULT)
        self._spin(tb, self.toct_var, OCT_MIN, OCT_MAX, cb=self.on_track_params)
        self.mute_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="Mute", variable=self.mute_var, takefocus=0,
                        command=lambda: self.run(self.on_track_params)).pack(side="left", padx=8)
        self.btn_test = ttk.Button(tb, text="▶ Test (halten) [Leertaste]", takefocus=0)
        self.btn_test.pack(side="left", padx=8)
        self.btn_test.bind("<ButtonPress-1>", self.on_test_press)
        self.btn_test.bind("<ButtonRelease-1>", self.on_test_release)

        self.root.bind("<KeyPress-space>", self.on_space_press, add="+")
        self.root.bind("<KeyRelease-space>", self.on_space_release, add="+")
        self.root.bind("<F12>", self.show_help, add="+")
        self.root.bind("<F11>", self.show_commands, add="+")
        for w in (self.tv, self.lb_pat, self.lb_ord):
            w.bind("<ButtonRelease-1>",
                   lambda e: self.root.after_idle(self.root.focus_set), add="+")

        tb2 = ttk.Frame(right)
        tb2.pack(fill="x", pady=(4, 0))
        ttk.Label(tb2, text="Pan").pack(side="left", padx=(2, 4))
        self.pan_scale = ttk.Scale(tb2, from_=PAN_MIN, to=PAN_MAX, orient="horizontal",
                                   length=220, takefocus=0, command=self.on_pan_move)
        self.pan_scale.pack(side="left")
        self.pan_scale.bind("<ButtonRelease-1>", self.on_pan_release)
        self.pan_scale.bind("<Double-Button-1>", self.on_pan_reset)
        self.lbl_pan = ttk.Label(tb2, text="C", width=5)
        self.lbl_pan.pack(side="left", padx=6)
        ttk.Label(tb2, text="(Doppelklick = Mitte)").pack(side="left")

        self._last_row = -1
        gf = ttk.LabelFrame(self, text="Pattern-Inhalt", padding=6)
        gf.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        gt = ttk.Frame(gf)
        gt.pack(fill="x", pady=(0, 4))

        ttk.Label(gt, foreground="#666666",
                text="Klaviertasten = Note   F12 = Hilfe (alle Tasten und Mausaktionen)"
                ).pack(side="left", padx=10)
        ttk.Button(gt, text="Hilfe (F12)", takefocus=0,
                command=self.show_help).pack(side="right")
        ttk.Button(gt, text="Befehle (F11)", takefocus=0,
           command=self.show_commands).pack(side="right", padx=4)
        self.step_var = tk.IntVar(value=1)
        self._spin(gt, self.step_var, 0, 16, cb=self._on_step)
        ttk.Label(gt, foreground="#666666",
                  text="Klaviertasten = Note   . = Ende   Entf/Rück = löschen   "
                       "Pfeile/Bild/Pos1/Ende = bewegen   F1-F5 = Oktave   Leertaste = Test"
                  ).pack(side="left", padx=10)
        self.pgrid = PatternGrid(gf, self)
        self.pgrid.pack(fill="both", expand=True)
        self.app.octave.trace_add("write", self._octave_changed)
        self.refresh_all()

    # ---- Hilfen
    def _spin(self, parent, var, lo, hi, cb=None, signed=False):
        cb = cb or self.on_params
        if signed:
            ok = lambda P: P in ("", "-") or P.lstrip("-").isdigit()
        else:
            ok = lambda P: P == "" or P.isdigit()
        vcmd = (self.register(ok), "%P")
        sp = ttk.Spinbox(parent, from_=lo, to=hi, width=5, textvariable=var,
                         command=cb, validate="key", validatecommand=vcmd)
        sp.pack(side="left", padx=4)
        sp.bind("<Return>", lambda e: cb())
        sp.bind("<FocusOut>", lambda e: cb())
        sp.bind("<Escape>", lambda e: self.root.focus_set())
        return sp

    def run(self, cmd):
        try:
            cmd()
        finally:
            self.root.focus_set()

    def set_dirty(self, flag=True):
        self.dirty = flag
        name = self.song["name"] or "neuer Song"
        self.lbl_song.config(text=name + (" *" if flag else ""))

    @property
    def pat(self):
        return self.song["patterns"][self.cur]

    # ---- Anzeige
    def refresh_all(self):
        s = self.song
        self.bpm_var.set(s["bpm"])
        self.rpb_var.set(s["rows_per_beat"])
        self.cur = min(self.cur, len(s["patterns"]) - 1)
        self.refresh_patterns()
        self.refresh_order()
        self.refresh_tracks()
        self.update_time()
        self.set_dirty(self.dirty)

    def refresh_patterns(self):
        self.lb_pat.delete(0, "end")
        for i, p in enumerate(self.song["patterns"]):
            self.lb_pat.insert("end", f"{i:02d}  {p['name']}  ({p['rows']} Z.)")
        self.lb_pat.selection_set(self.cur)
        self.rows_var.set(self.pat["rows"])
        self.refresh_grid()

    def refresh_order(self, select=None):
        lb = self.lb_ord
        y0 = lb.yview()[0]
        lb.delete(0, "end")
        for i, o in enumerate(self.song["order"]):
            lb.insert("end", f"{i:02d}  ->  {o:02d} {self.song['patterns'][o]['name']}")
        lb.yview_moveto(y0)
        if select is not None and 0 <= select < lb.size():
            lb.selection_set(select)
            lb.see(select)

    def refresh_tracks(self):
        self.tv.delete(*self.tv.get_children())
        for i, t in enumerate(self.song["tracks"]):

            self.tv.insert("", "end", iid=str(i),
                           values=(i + 1, t["patch"] or "-", t["octave"], pan_text(t["pan"]),
                                   ("S+M" if self.solo == i and t["mute"] else "S" if self.solo == i
                                    else "M" if t["mute"] else ""),
                                   self.warns[i]))

        self.tv.selection_set(str(self.sel))
        self.pgrid.draw_header()

    def update_time(self):
        s = self.song
        spr = samples_per_row(s["bpm"], s["rows_per_beat"])
        self.lbl_time.config(text=f"1 Zeile = {spr / SR * 1000:.1f} ms ({spr:.1f} Samples)")

    # ---- Parameter
    def on_params(self):
        s = self.song
        bpm = _clamp(self._get(self.bpm_var), BPM_MIN, BPM_MAX, s["bpm"])
        rpb = _clamp(self._get(self.rpb_var), RPB_MIN, RPB_MAX, s["rows_per_beat"])
        rows = _clamp(self._get(self.rows_var), ROWS_MIN, ROWS_MAX, self.pat["rows"])
        changed = False
        if bpm != s["bpm"] or rpb != s["rows_per_beat"]:
            s["bpm"], s["rows_per_beat"] = bpm, rpb
            changed = True
        if rows != self.pat["rows"]:
            self.pat["rows"] = rows
            self.pat["notes"] = [n for n in self.pat["notes"] if n[0] < rows]
            self.pat["cmds"] = [c for c in self.pat["cmds"] if c[0] < rows]
            changed = True
        self.bpm_var.set(bpm)
        self.rpb_var.set(rpb)
        self.rows_var.set(rows)
        if changed:
            self.update_time()
            self.refresh_patterns()
            self.set_dirty()

    @staticmethod
    def _get(var):
        try:
            return var.get()
        except tk.TclError:
            return None

    # ---- Patterns / Reihenfolge
    def on_pat_select(self, e=None):
        sel = self.lb_pat.curselection()
        if sel:
            self.cur = sel[0]
            self.rows_var.set(self.pat["rows"])
            self.refresh_grid()

    def on_pat_new(self):
        pats = self.song["patterns"]
        pats.append(new_pattern(len(pats), self.pat["rows"]))
        self.cur = len(pats) - 1
        self.refresh_patterns()
        self.set_dirty()

    def on_pat_copy(self):
        src = self.pat
        pats = self.song["patterns"]
        pats.append({"name": f"P{len(pats):02d}", "rows": src["rows"],
                     "notes": [list(n) for n in src["notes"]], "cmds": [list(c) for c in src["cmds"]]})
        self.cur = len(pats) - 1
        self.refresh_patterns()
        self.set_dirty()

    def on_pat_del(self):
        s = self.song
        if len(s["patterns"]) <= 1:
            messagebox.showinfo("Pattern", "Das letzte Pattern kann nicht gelöscht werden.")
            return
        self._undo.clear(); self._redo.clear()
        i = self.cur
        s["patterns"].pop(i)
        order = [o if o < i else o - 1 for o in s["order"] if o != i]
        s["order"] = order or [0]
        self.cur = min(i, len(s["patterns"]) - 1)
        self.refresh_patterns()
        self.refresh_order()
        self.set_dirty()

    def on_ord_add(self):
        self.song["order"].append(self.cur)
        self.refresh_order(select=len(self.song["order"]) - 1)
        self.set_dirty()

    def on_ord_del(self):
        sel = self.lb_ord.curselection()
        if not sel or len(self.song["order"]) <= 1:
            return
        self.song["order"].pop(sel[0])
        self.refresh_order(select=min(sel[0], len(self.song["order"]) - 1))
        self.set_dirty()

    # ---- Song-Datei
    def confirm_discard(self):
        if not self.dirty:
            return True
        r = messagebox.askyesnocancel(
            "Sequencer", f"Der Song '{self.song['name'] or 'neuer Song'}' wurde geändert. Speichern?")
        if r is None:
            return False
        if r:
            return self.on_save()
        return True

    def _load_song(self, song, path):
        self.solo = None
        self._undo.clear(); self._redo.clear()
        self.song, self.path, self.cur = song, path, 0
        self.song["name"] = os.path.splitext(os.path.basename(path))[0] if path else ""
        self.sel = 0
        self.load_all()
        self.dirty = False
        self.refresh_all()

    def on_new(self):
        if self.confirm_discard():
            self._load_song(new_song(), None)

    def on_load(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(initialdir=sub_dir("SEQUENCER"),
                                          filetypes=[("Song (JSON)", "*.json")])
        if not path:
            return
        try:
            song = read_song(path)
        except Exception as e:
            messagebox.showerror("Song laden", str(e))
            return
        self._load_song(song, path)

    def on_import_mod(self):
        """MOD importieren: WAVs, Patches, Soundbank und Song erzeugen (modsong.run_import), dann laden."""
        if not self.confirm_discard():
            return
        st = load_settings()
        start = st.get("last_mod_dir")
        if not start or not os.path.isdir(start):
            start = sub_dir("SEQUENCER")
        path = filedialog.askopenfilename(title="MOD importieren", initialdir=start,
                                          filetypes=[("Tracker-Module", "*.mod"), ("Alle Dateien", "*.*")])
        if not path:
            return
        st["last_mod_dir"] = os.path.dirname(path)
        save_settings(st)
        self.stop_play()
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            import modsong
            text, song_path = modsong.run_import(path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            messagebox.showerror("MOD importieren", f"{type(e).__name__}: {e}")
            return
        finally:
            self.root.config(cursor="")
        if not song_path:
            self._show_text_window("MOD-Import (kein Song erzeugt)", text)
            return
        log_path = os.path.splitext(song_path)[0] + "_import.txt"
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            log_path = None
        try:
            self._load_song(read_song(song_path), song_path)
        except Exception as e:
            messagebox.showerror("Song laden", str(e))
            return
        bank = os.path.join(sub_dir("SOUNDBANK"), os.path.splitext(os.path.basename(song_path))[0] + ".json")
        bank_msg = ""
        sbt = self.app.soundbank
        if os.path.isfile(bank):
            if sbt.confirm_discard() and sbt.load_bank_file(bank, silent=True):
                sbt.remember_bank(bank)
                bank_msg = ", Bank geladen"
            else:
                bank_msg = ", Bank nicht geladen"
        self.lbl_play.config(text=f"MOD importiert: {self.song['name']}{bank_msg}")
        self._show_text_window("MOD-Import: " + str(self.song["name"]), text, log_path)

    def _show_text_window(self, title, text, save_path=None):
        w = tk.Toplevel(self.root)
        w.title(title)
        w.transient(self.root)
        bf = ttk.Frame(w, padding=6)
        bf.pack(side="bottom", fill="x")
        sx = ttk.Scrollbar(w, orient="horizontal")
        sx.pack(side="bottom", fill="x")
        fr = ttk.Frame(w)
        fr.pack(side="top", fill="both", expand=True)
        txt = tk.Text(fr, width=110, height=36, font=("Consolas", 10), wrap="none", padx=10, pady=8)
        sy = ttk.Scrollbar(fr, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sx.configure(command=txt.xview)
        sy.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")

        def close(_e=None):
            w.destroy()
            self.root.focus_set()

        def copy():
            w.clipboard_clear()
            w.clipboard_append(text)

        if save_path:
            ttk.Label(bf, text="Log gespeichert: " + save_path, foreground="#666666").pack(side="left")
        ttk.Button(bf, text="Schließen", command=close).pack(side="right", padx=2)
        ttk.Button(bf, text="Kopieren", command=copy).pack(side="right", padx=2)
        w.protocol("WM_DELETE_WINDOW", close)
        w.bind("<Escape>", close)

    def on_export_audio(self):
        """WAV und MP3 des aktuellen Songs nach EXPORT/<Songname> speichern (songexport, eigener Thread)."""
        self.on_params()
        if all(not t["patch"] or t["mute"] for t in self.song["tracks"]):
            self.lbl_play.config(text="Keine Spur belegt")
            return
        self.stop_play()
        import songexport
        songexport.export_dialog(self.root, self.song, self.song["name"] or "song")

    def on_save(self):
        if not self.path:
            return self.on_save_as()
        return self._write(self.path)

    def on_save_as(self):
        path = filedialog.asksaveasfilename(
            initialdir=sub_dir("SEQUENCER"), defaultextension=".json",
            initialfile=(self.song["name"] or "song") + ".json",
            filetypes=[("Song (JSON)", "*.json")])
        if not path:
            return False
        return self._write(path)

    def _write(self, path):
        if to_rel(path) is None:
            messagebox.showwarning("Song speichern",
                                   "Der Song muss im Programmordner (SEQUENCER) liegen.")
            return False
        self.on_params()
        self.song["name"] = os.path.splitext(os.path.basename(path))[0]
        try:
            write_song(path, self.song)
        except Exception as e:
            messagebox.showerror("Song speichern", str(e))
            return False
        self.path = path
        self.set_dirty(False)
        return True

    # ---- Tab-Wechsel
    def on_tab_changed(self, event=None):
        self._space_down = False
        self.release_test()
        self.release_edit_all()
        if self._visible():
            self.refresh_tracks()
            self._set_app_octave(self.song["tracks"][self.sel]["octave"])
        self.root.focus_set()

    # ---- Spuren: Engines
    def unload_track(self, i):
        eng = self.engines[i]
        if eng is not None:
            self.app.mixer.remove_part(eng)
            self.engines[i] = None

    def build_track(self, i):
        self.stop_play()
        self.unload_track(i)
        self.warns[i] = ""
        t = self.song["tracks"][i]
        if not t["patch"]:
            return
        path = to_abs(t["patch"])
        if not os.path.isfile(path):
            self.warns[i] = "Patch fehlt"
            return
        try:
            d = patchlib.read_patch(path)
            eng = Engine()
            res = load_patch_into(eng, d, path, self.cache)   # erst laden ...
        except Exception as e:
            self.warns[i] = f"Fehler: {e}"
            return
        self.warns[i] = "; ".join(res)
        self.engines[i] = eng
        if not self._eff_mute(i):
            self.app.mixer.add_part(eng, t["pan"] / 100.0)      # ... dann einhaengen

    def load_all(self):
        self.release_test()
        self.cache = {}
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            for i in range(NUM_TRACKS):
                self.build_track(i)
        finally:
            self.root.config(cursor="")

    def shutdown(self):
        self.stop_play()
        self.release_test()
        self.release_edit_all()
        for i in range(NUM_TRACKS):
            self.unload_track(i)

    # ---- Spuren: Bedienung
    def on_track_select(self, e=None):
        s = self.tv.selection()
        if not s:
            return
        self.sel = int(s[0])
        t = self.song["tracks"][self.sel]
        self.toct_var.set(t["octave"])
        self.mute_var.set(t["mute"])
        self._set_pan_widget(t["pan"])
        if self._visible():
            self._set_app_octave(t["octave"])
        self.pgrid.draw_cursor()

    def _eff_mute(self, i):
        """Wirksamer Mute-Zustand: bei Solo ist nur die Solo-Spur hoerbar."""
        if self.solo is not None:
            return i != self.solo
        return bool(self.song["tracks"][i]["mute"])

    def _apply_mute(self, i):
        m = self._eff_mute(i)
        if self.player is not None:
            self.player.muted[i] = m
        eng = self.engines[i]
        if eng is None:
            return
        if m:
            self.release_test()
            self.app.mixer.remove_part(eng)
            eng.voices = {}   # nichts Unhoerbares ansammeln
            eng.dying = []
        else:
            self.app.mixer.add_part(eng, self.song["tracks"][i]["pan"] / 100.0)

    def toggle_mute(self):
        self.mute_var.set(not self.mute_var.get())
        self.on_track_params()

    def toggle_solo(self):
        self.solo = None if self.solo is not None else self.sel
        for i in range(NUM_TRACKS):
            self._apply_mute(i)
        self.refresh_tracks()
        self.pgrid.redraw()
        self.lbl_play.config(text=(f"Solo: Spur {self.solo + 1}" if self.solo is not None
                                else "Solo aus"))

    def on_track_params(self):
        t = self.song["tracks"][self.sel]
        o = _clamp(self._get(self.toct_var), OCT_MIN, OCT_MAX, t["octave"])
        self.toct_var.set(o)
        m = bool(self.mute_var.get())

        #print("MUTE sel", self.sel, "m", m, "eng", self.engines[self.sel] is not None,
        #"im_mixer", self.engines[self.sel] in self.app.mixer.parts,
        #"player", self.player is not None, flush=True)

        changed = False
        if o != t["octave"]:
            t["octave"] = o
            changed = True

        if m != t["mute"]:
            t["mute"] = m
            self._apply_mute(self.sel)
            changed = True
            if changed:
                    self.refresh_tracks()
                    self.pgrid.redraw()
                    self.set_dirty()

    def on_patch_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        groups = {}
        for en in self.app.soundbank.entries():
            groups.setdefault(en.get("category") or "ohne Kategorie", []).append(en)
        for cat in sorted(groups, key=str.lower):
            sub = tk.Menu(m, tearoff=0)
            for en in groups[cat]:
                sub.add_command(label=en["name"],
                                command=lambda en=en: self.run(
                                    lambda: self.assign(en["file"], en.get("octave"))))
            m.add_cascade(label=cat, menu=sub)
        if groups:
            m.add_separator()
        m.add_command(label="Datei wählen…", command=lambda: self.run(self.pick_file))
        m.add_command(label="Spur leeren", command=lambda: self.run(lambda: self.assign(None)))
        try:
            m.tk_popup(self.btn_patch.winfo_rootx(),
                       self.btn_patch.winfo_rooty() + self.btn_patch.winfo_height())
        finally:
            m.grab_release()

    def pick_file(self):
        p = filedialog.askopenfilename(initialdir=sub_dir("PATCHES"),
                                       filetypes=[("Synth-Patch (JSON)", "*.json")])
        if not p:
            return
        rel = to_rel(p)
        pdir = os.path.normcase(sub_dir("PATCHES") + os.sep)
        if rel is None or not os.path.normcase(os.path.abspath(p)).startswith(pdir):
            messagebox.showwarning("Patch wählen", "Der Patch muss im Ordner PATCHES liegen.")
            return
        self.assign(rel)

    def assign(self, rel, octave=None):
        i = self.sel
        t = self.song["tracks"][i]
        t["patch"] = rel
        if rel is None:
            t["octave"] = OCT_DEFAULT
        elif octave is not None:
            t["octave"] = _clamp(octave, OCT_MIN, OCT_MAX, OCT_DEFAULT)
        self.release_test()
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            self.build_track(i)
        finally:
            self.root.config(cursor="")
        self.toct_var.set(t["octave"])
        self.refresh_tracks()
        self.set_dirty()

    def on_test_press(self, e=None):
        eng = self.engines[self.sel]
        t = self.song["tracks"][self.sel]
        if eng is None or t["mute"]:
            return
        self.release_test()
        key = ("seq-test", self.sel)
        eng.note_on(key, (t["octave"] - 3) * 12)
        self.test = (eng, key)

    def release_test(self):
        if self.test:
            eng, key = self.test
            eng.note_off(key)
            self.test = None

    def on_test_release(self, e=None):
        self.release_test()
        self.root.focus_set()

    # ---- Pan
    def _set_pan_widget(self, p):
        self._pan_lock = True
        try:
            self.pan_scale.set(p)
        finally:
            self._pan_lock = False
        self.lbl_pan.config(text=pan_text(p))

    def apply_pan(self, i):
        eng = self.engines[i]
        if eng is not None:
            self.app.mixer.set_pan(eng, self.song["tracks"][i]["pan"] / 100.0)
        if self.player is not None:
            self.player.base_pan[i] = self.song["tracks"][i]["pan"] / 100.0

    def on_pan_move(self, v):
        if self._pan_lock:
            return
        p = int(round(float(v)))
        t = self.song["tracks"][self.sel]
        self.lbl_pan.config(text=pan_text(p))
        if p != t["pan"]:
            t["pan"] = p
            self._pan_dirty = True
            self.apply_pan(self.sel)

    def on_pan_release(self, e=None):
        if self._pan_dirty:
            self._pan_dirty = False
            self.refresh_tracks()
            self.set_dirty()
        self.root.focus_set()

    def on_pan_reset(self, e=None):
        t = self.song["tracks"][self.sel]
        self._set_pan_widget(0)
        if t["pan"] != 0:
            t["pan"] = 0
            self._pan_dirty = True
            self.apply_pan(self.sel)
        return "break"

    def on_space_press(self, e=None):
        try:
            visible = self.app.nb.select() == str(self)
        except tk.TclError:
            visible = False
        if not visible or self.app.typing_in_entry() or self._space_down:
            return
        self._space_down = True
        self.on_test_press()

    def on_space_release(self, e=None):
        if self._space_down:
            self._space_down = False
            self.release_test()

    # ---- Wiedergabe
    def on_play(self):
        if self.player is not None:
            return
        self.on_params()
        self.release_test()
        if all(e is None for e in self.engines):
            self.lbl_play.config(text="Keine Spur belegt")
            return
        s = self.song
        spr = samples_per_row(s["bpm"], s["rows_per_beat"])

        count = _clamp(self._get(self.count_var), 0, 8, 4) if self.rec_var.get() else 0
        pl = compile_song(s, self.engines, spr, self.mode_var.get(),
                          self.cur, self.loop_var.get(), count)
        pl.metro = bool(self.metro_var.get())
        pl.muted = [self._eff_mute(i) for i in range(NUM_TRACKS)]
        pl.base_pan = [t["pan"] / 100.0 for t in s["tracks"]]

        if pl.total_rows <= 0:
            return
        self.player = pl
        self._play_mode = self.mode_var.get()
        self._play_cur = self.cur
        self.btn_play.config(state="disabled")
        self.btn_stopp.config(state="normal")
        self.app.mixer.start_player(pl)
        self._poll()

    def stop_play(self):
        pl = self.player
        if pl is not None:
            pl.stop_flag = True          # der Audio-Thread gibt alle Noten frei

    def _poll(self):
        self._poll_id = None
        pl = self.player
        if pl is None:
            return
        if pl.done:
            self.player = None
            self.rec_down.clear()
            self.btn_play.config(state="normal")
            self.btn_stopp.config(state="disabled")
            self.pgrid.set_play_row(None)
            self.lbl_play.config(text=f"Fehler: {pl.error}" if pl.error else "Gestoppt")
            return
        try:
            opos, pi, r, rows = pl.where()
            if pi != self.cur:
                self._show_pattern(pi)
            if r != self._last_row:
                self._last_row = r
                self.pgrid.set_play_row(r)
            name = self.song["patterns"][pi]["name"]
            txt = (f"▶ Pos {opos + 1}  Pattern {name}  Zeile {r + 1}/{rows}"
                   + (f"  (Loop {pl.loops + 1})" if pl.loop else ""))
            if pl.pos < 0:
                txt = "Vorzähler …"
            elif self.rec_var.get():
                txt = "● REC  " + txt

        except (IndexError, KeyError):
            txt = "▶ läuft"
        self.lbl_play.config(text=txt)
        self._poll_id = self.root.after(50, self._poll)

    def on_demo(self):
        p = self.pat
        if p["notes"] and not messagebox.askyesno(
                "Demo-Noten", "Die Noten dieses Patterns werden ersetzt. Fortfahren?"):
            return
        chord = (0, 4, 7, 12)
        notes, k = [], 0
        for tr, t in enumerate(self.song["tracks"]):
            if self.engines[tr] is None:
                continue
            base = (t["octave"] - 3) * 12
            for r in range((k % 2) * 2, p["rows"], 4):
                notes.append([r, tr, base + chord[(r // 4 + k) % 4], 3, None])
            k += 1
        notes.sort()
        p["notes"] = notes
        self.set_dirty()
        self.refresh_grid()
        self.lbl_play.config(text=f"Demo: {len(notes)} Noten auf {k} Spuren")

    # ---- Pattern-Ansicht
    def refresh_grid(self):
        self.sel_a = None
        self.crow = max(0, min(self.crow, self.pat["rows"] - 1))
        self.pgrid.redraw()
        self._last_row = -1
        self._snap = self._pat_state()

    def _show_pattern(self, pi):
        self.cur = pi
        self.lb_pat.selection_clear(0, "end")
        self.lb_pat.selection_set(pi)
        self.lb_pat.see(pi)
        self.rows_var.set(self.pat["rows"])
        self.refresh_grid()

    # ---- Raster-Editor
    def _visible(self):
        try:
            return self.app.nb.select() == str(self)
        except tk.TclError:
            return False

    def _step(self):
        return _clamp(self._get(self.step_var), 0, 16, 1)

    def _on_step(self):
        self.step_var.set(self._step())
        self.root.focus_set()

    def _set_app_octave(self, o):
        self._oct_lock = True
        try:
            self.app.octave.set(o)
        finally:
            self._oct_lock = False

    def _octave_changed(self, *a):
        if self._oct_lock or not self._visible():
            return
        try:
            o = _clamp(self.app.octave.get(), OCT_MIN, OCT_MAX, OCT_DEFAULT)
        except tk.TclError:
            return
        t = self.song["tracks"][self.sel]
        if o != t["octave"]:
            t["octave"] = o
            self.toct_var.set(o)
            self.refresh_tracks()
            self.set_dirty()

    def select_track(self, i):
        self.sel = i
        self.tv.selection_set(str(i))
        self.tv.see(str(i))
        self.on_track_select()

    def on_grid_click(self, row, col):
        self.sel_a = None
        self.crow = row
        self.select_track(col)
        self.pgrid.draw_cursor()

    def _move(self, dr, dc):
        self.sel_a = None
        self.crow = max(0, min(self.pat["rows"] - 1, self.crow + dr))
        if dc:
            self.select_track(max(0, min(NUM_TRACKS - 1, self.sel + dc)))
        self.pgrid.draw_cursor()

    def _push_plan(self):
        pl = self.player
        if pl is None or pl.done:
            return
        s = self.song
        spr = samples_per_row(s["bpm"], s["rows_per_beat"])
        pl.set_plan(build_plan(s, pl.engines, spr, self._play_mode, self._play_cur))

    def _pat_state(self, pi=None):
        pi = self.cur if pi is None else pi
        p = self.song["patterns"][pi]
        return (pi, [list(n) for n in p["notes"]], [list(c) for c in p["cmds"]])

    def _track_edit(self):
        now = self._pat_state()
        old = self._snap
        recording = self.player is not None and self.rec_var.get()
        if old is not None and old[0] == self.cur and old[1:] != now[1:] and not recording:
            self._undo.append(old)
            del self._undo[:-UNDO_MAX]
            self._redo.clear()
        self._snap = now

    def _restore(self, src, dst):
        pi, notes, cmds = src.pop()
        pats = self.song["patterns"]
        if pi >= len(pats):
            return
        dst.append(self._pat_state(pi))
        rows = pats[pi]["rows"]
        pats[pi]["notes"] = [list(n) for n in notes if n[0] < rows]
        pats[pi]["cmds"] = [list(c) for c in cmds if c[0] < rows]
        if pi != self.cur:
            self._show_pattern(pi)
        self.sel_a = None
        self._snap = self._pat_state(pi)
        self.set_dirty()
        self.pgrid.redraw()
        self._push_plan()

    def undo(self):
        if self._undo:
            self._restore(self._undo, self._redo)
        else:
            self.lbl_play.config(text="Nichts rückgängig zu machen")

    def redo(self):
        if self._redo:
            self._restore(self._redo, self._undo)
        else:
            self.lbl_play.config(text="Nichts wiederherzustellen")

    def _edited(self):
        self._track_edit()
        self.set_dirty()
        self.pgrid.redraw()
        self._push_plan()

    def _preview(self, semi, vel=None, ms=250):
        """Kurzer Orientierungston der gewaehlten Spur (auch bei Solo-Sperre, nicht bei M)."""
        t = self.song["tracks"][self.sel]
        eng = self.engines[self.sel]
        if eng is None or t["mute"]:
            return
        tr = self.sel
        if self._eff_mute(tr):
            self.app.mixer.add_part(eng, t["pan"] / 100.0)
        self._nudge_n += 1
        key = ("seq-nudge", tr, self._nudge_n)
        eng.note_on(key, semi, vel_gain(vel))

        def off():
            eng.note_off(key)
            if self._eff_mute(tr):
                self.root.after(1500, lambda: self._eff_mute(tr) and self.app.mixer.remove_part(eng))
        self.root.after(ms, off)

    def _nudge(self, d):
        n = patternedit.transpose_note(self.pat["notes"], self.crow, self.sel, d)
        if n is None:
            return
        self._edited()
        self._preview(n[2])

    def clear_track_in_pattern(self):
        notes, cmds = self.pat["notes"], self.pat["cmds"]
        if not (any(n[1] == self.sel for n in notes) or any(c[1] == self.sel for c in cmds)):
            return
        if messagebox.askyesno("Spur leeren",
                            f"Spur {self.sel + 1} im Pattern {self.pat['name']} komplett leeren?"):
            patternedit.clear_track(notes, self.sel)
            cmds[:] = [c for c in cmds if c[1] != self.sel]
            self._edited()
        self.root.focus_set()

    def clear_pattern(self):
        if not (self.pat["notes"] or self.pat["cmds"]):
            return
        if messagebox.askyesno("Pattern leeren",
                            f"Pattern {self.pat['name']} komplett leeren?"):
            patternedit.clear_all(self.pat["notes"])
            self.pat["cmds"].clear()
            self._edited()
        self.root.focus_set()

    def sel_range(self):
        if self.sel_a is None or self.sel_tr != self.sel:
            return None
        return min(self.sel_a, self.crow), max(self.sel_a, self.crow)

    def _extend(self, dr):
        #print("EXT", self.sel_a, self.sel_tr, self.crow, self.sel, flush=True)
        if self.sel_a is None or self.sel_tr != self.sel:
            self.sel_a, self.sel_tr = self.crow, self.sel
        self.crow = max(0, min(self.pat["rows"] - 1, self.crow + dr))
        self.pgrid.draw_cursor()

    def select_all_track(self):
        self.sel_a, self.sel_tr = 0, self.sel
        self.crow = self.pat["rows"] - 1
        self.pgrid.draw_cursor()

    def copy_sel(self):
        r0, r1 = self.sel_range() or (self.crow, self.crow)
        clip = patternedit.copy_range(self.pat["notes"], self.sel, r0, r1)
        cclip = patternedit.copy_cmd_range(self.pat["cmds"], self.sel, r0, r1)
        self.clip = (r1 - r0 + 1, clip, cclip)
        self.lbl_play.config(
            text=f"Kopiert: {len(clip)} Noten, {len(cclip)} Befehle, {r1 - r0 + 1} Zeilen")

    def cut_sel(self):
        self.copy_sel()
        r0, r1 = self.sel_range() or (self.crow, self.crow)
        patternedit.clear_range(self.pat["notes"], self.sel, r0, r1)
        patternedit.clear_cmd_range(self.pat["cmds"], self.sel, r0, r1)
        self.sel_a = None
        self._edited()

    def paste_clip(self):
        if self.clip is None:
            self.lbl_play.config(text="Zwischenablage leer")
            return
        n_rows, clip, cclip = self.clip
        patternedit.paste(self.pat["notes"], self.pat["rows"], self.sel, self.crow, n_rows, clip)
        patternedit.paste_cmds(self.pat["cmds"], self.pat["rows"], self.sel, self.crow,
                            n_rows, cclip)
        self.sel_a = None
        self._edited()
        if clip:
            self._preview(clip[0][1])

    def mouse_press(self, row, col, state):
        shift, ctrl = bool(state & 0x1), bool(state & 0x4)
        self._drag_src = None
        rg = self.sel_range()
        if (shift or ctrl) and rg is not None and col == self.sel and rg[0] <= row <= rg[1]:
            self._drag_src = (self.sel, rg[0], rg[1], row - rg[0], ctrl)
            return "drag"
        if (shift or ctrl) and (any(n[0] == row and n[1] == col for n in self.pat["notes"])
                        or patternedit.cmd_at(self.pat["cmds"], row, col) is not None):
            self.sel_a = None
            self.on_grid_click(row, col)
            self._drag_src = (col, row, row, 0, ctrl)
            return "drag"
        if shift and not ctrl and col == self.sel:
            self.sel_a = self.sel_a if rg else self.crow
            self.sel_tr = self.sel
            self._ms_anchor = self.sel_a
            self.crow = row
            self.pgrid.draw_cursor()
            return "select"
        self.sel_a = None
        self.on_grid_click(row, col)
        self._ms_anchor = row
        return "select"

    def mouse_select(self, row):
        if row == self.crow and self.sel_a is None:
            return
        if row == self.crow and self.sel_a == self._ms_anchor and self.sel_tr == self.sel:
            return
        self.sel_a, self.sel_tr = self._ms_anchor, self.sel
        self.crow = row
        self.pgrid.draw_cursor()

    def mouse_ghost(self, col, row, inside):
        if self._drag_src is None:
            return
        _tr, r0, r1, off, move = self._drag_src
        self.pgrid.draw_ghost(col, row - off, r1 - r0 + 1, inside,
                            "#ff9f6c" if move else "#ffffff")

    def mouse_drop(self, col, row, inside):
        src, self._drag_src = self._drag_src, None
        if src is None or not inside:
            return                      # ausserhalb: nichts tun, Markierung bleibt
        tr, r0, r1, off, move = src
        rows = self.pat["rows"]
        n = r1 - r0 + 1
        top = max(0, min(rows - 1, row - off))
        if move and col == tr and top == r0:
            return
        notes, cmds = self.pat["notes"], self.pat["cmds"]
        clip = patternedit.copy_range(notes, tr, r0, r1)
        cclip = patternedit.copy_cmd_range(cmds, tr, r0, r1)
        if move:
            patternedit.clear_range(notes, tr, r0, r1)
            patternedit.clear_cmd_range(cmds, tr, r0, r1)
        patternedit.paste(notes, rows, col, top, n, clip)
        patternedit.paste_cmds(cmds, rows, col, top, n, cclip)
        self.select_track(col)
        self.sel_tr = col
        self.sel_a = min(rows - 1, top + n - 1)
        self.crow = top
        self._edited()
        if clip:
            self._preview(clip[0][1])

    def show_help(self, e=None):
        if not self._visible():
            return
        w = getattr(self, "_help_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        w = tk.Toplevel(self.root)
        self._help_win = w
        w.title("Sequencer: Tasten und Maus")
        w.transient(self.root)

        def close(e=None):
            w.destroy()
            self.root.focus_set()
        w.protocol("WM_DELETE_WINDOW", close)
        w.bind("<Escape>", close)
        w.bind("<F12>", close)
        txt = tk.Text(w, width=86, height=len(HELP_LINES) + 1, font=("Consolas", 10),
                    wrap="none", padx=10, pady=8)
        txt.insert("1.0", "\n".join(HELP_LINES))
        txt.configure(state="disabled")
        txt.pack(fill="both", expand=True)
        ttk.Button(w, text="Schließen", command=close).pack(pady=(0, 8))

    def show_commands(self, e=None):
        if not self._visible():
            return
        w = getattr(self, "_cmd_win", None)
        if w is not None and w.winfo_exists():
            w.lift()
            return
        w = tk.Toplevel(self.root)
        self._cmd_win = w
        w.title("Sequencer: Befehle (Effekt-Spalte)")
        w.transient(self.root)

        def close(e=None):
            w.destroy()
            self.root.focus_set()
        w.protocol("WM_DELETE_WINDOW", close)
        w.bind("<Escape>", close)
        w.bind("<F11>", close)
        fr = ttk.Frame(w)
        fr.pack(fill="both", expand=True)
        txt = tk.Text(fr, width=78, height=32, font=("Consolas", 10), wrap="none",
                    padx=10, pady=8)
        sb = ttk.Scrollbar(fr, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", "\n".join(command_lines()))
        txt.configure(state="disabled")
        ttk.Button(w, text="Schließen", command=close).pack(pady=(0, 8))


    def _vel_change(self, step=0, clear=False):
        n = patternedit.covering(self.pat["notes"], self.crow, self.sel)
        if n is None:
            return
        cur = n[4] if len(n) > 4 else None
        if clear:
            v = None
        else:
            v = max(0, min(VEL_MAX, (VEL_MAX if cur is None else cur) + step))
        patternedit.set_vel(self.pat["notes"], self.crow, self.sel, v)
        self._edited()
        self._preview(n[2], v)
        self.lbl_play.config(text=f"Spur {self.sel + 1}, Zeile {n[0]}: Lautstärke "
                                + ("leer" if v is None else f"{v:02d}"))

    def _note_at(self):
        """Note, die in der Cursorzeile BEGINNT (Spur = gewaehlte Spur) oder None."""
        for n in self.pat["notes"]:
            if n[0] == self.crow and n[1] == self.sel:
                return n
        return None

    def _field_move(self, d):
        self.sel_a = None
        f, tr = self.field + d, self.sel
        if f > 3:
            f, tr = 0, (tr + 1) % NUM_TRACKS
        elif f < 0:
            f, tr = 3, (tr - 1) % NUM_TRACKS
        self.field = f
        if tr != self.sel:
            self.select_track(tr)
        self.pgrid.draw_cursor()

    def _digit(self, d):
        d = int(d)
        if self.field == 1:
            n = self._note_at()
            if n is None:
                self.lbl_play.config(text="Hier beginnt keine Note")
                return
            cur = n[4] if len(n) > 4 else None
            v = min(VEL_MAX, d if cur is None else (cur % 10) * 10 + d)
            patternedit.set_vel(self.pat["notes"], n[0], n[1], v)
            self._edited()
            self._preview(n[2], v)
            return
        c = patternedit.cmd_at(self.pat["cmds"], self.crow, self.sel)
        cur = None if c is None else c[2 if self.field == 2 else 3]
        v = d if cur is None else (cur % 10) * 10 + d
        if self.field == 2:
            patternedit.set_cmd(self.pat["cmds"], self.crow, self.sel, code=v)
        else:
            patternedit.set_cmd(self.pat["cmds"], self.crow, self.sel, value=v)
        self._edited()

    def on_key_press(self, e):
        kl = e.keysym.lower()
        #print("KEY", kl, e.state, flush=True)
        rows = self.pat["rows"]
        bar = self.song["rows_per_beat"] * 4

        if (e.state & 0x4) and kl in ("z", "y"):
            if kl == "y" or (e.state & 0x1):      # Strg+Y oder Strg+Umschalt+Z
                self.redo()
            else:
                self.undo()
            return

        if (e.state & 0x4) and kl in ("prior", "next", "home"):
            if kl == "home":
                self._vel_change(clear=True)
            else:
                st = 1 if (e.state & 0x1) else 10
                self._vel_change(st if kl == "prior" else -st)
            return

        if (e.state & 0x4) and kl in ("c", "x", "v", "a"):
            {"c": self.copy_sel, "x": self.cut_sel, "v": self.paste_clip,
            "a": self.select_all_track}[kl]()
            return

        if self.field != 0 and not (e.state & 0x4):
            dg = kl[3:] if kl.startswith("kp_") else kl
            if len(dg) == 1 and dg.isdigit():
                if kl not in self._digit_down:
                    self._digit_down.add(kl)
                    self._digit(dg)
                return
            if kl in KEYMAP:
                return                  # Klaviertasten nur im Notenfeld

        if kl in KEYMAP:
            if kl in self._rel_pending:    # Auto-Repeat: das Loslassen war nur scheinbar
                self.root.after_cancel(self._rel_pending.pop(kl))
                return
            if kl not in self._edit_down:
                self._enter_note(kl, e)

        elif kl in ("up", "down", "prior", "next", "home", "end"):
            dr = {"up": -1, "down": 1, "prior": -bar, "next": bar,
                "home": -rows, "end": rows}[kl]

            if e.state & 0x1:
                self._extend(dr)
            elif kl == "up" and self.crow == 0:
                self._move(rows, 0)       # Ringschluss: erste Zeile -> letzte
            elif kl == "down" and self.crow == rows - 1:
                self._move(-rows, 0)      # Ringschluss: letzte Zeile -> erste
            else:
                self._move(dr, 0)

        elif kl == "left":
            self._field_move(-1)
        elif kl == "right":
            self._field_move(1)

        elif kl == "delete" and (e.state & 0x4):      # Strg+Entf
            self.clear_pattern()
        elif kl == "delete" and (e.state & 0x1):      # Umschalt+Entf
            self.clear_track_in_pattern()

        elif kl in ("backspace", "delete"):
            rg = self.sel_range()
            if rg:
                patternedit.clear_range(self.pat["notes"], self.sel, rg[0], rg[1])
                patternedit.clear_cmd_range(self.pat["cmds"], self.sel, rg[0], rg[1])
                self.sel_a = None
                self._edited()
            elif self.field == 1:
                n = self._note_at()
                if n is not None and len(n) > 4 and n[4] is not None:
                    patternedit.set_vel(self.pat["notes"], n[0], n[1], None)
                    self._edited()
            elif self.field == 2:
                if patternedit.clear_cmd(self.pat["cmds"], self.crow, self.sel):
                    self._edited()
            elif self.field == 3:
                c = patternedit.cmd_at(self.pat["cmds"], self.crow, self.sel)
                if c is not None and c[3] != 0:
                    c[3] = 0
                    self._edited()
            elif patternedit.clear_note(self.pat["notes"], self.crow, self.sel):
                self._edited()

        elif kl in NUDGE:
            self._nudge(NUDGE[kl])

        elif kl == "period":
            if patternedit.set_end(self.pat["notes"], self.crow, self.sel):
                self._edited()

        elif kl == "escape":
            self.sel_a = None
            self.pgrid.draw_cursor()

        elif kl == "m" and not (e.state & 0x4):
            self.toggle_mute()
        elif kl == "s" and not (e.state & 0x4):
            self.toggle_solo()

    def on_key_release(self, e):
        kl = e.keysym.lower()
        self._digit_down.discard(kl)
        #print("R", kl, e.time)
        if kl in KEYMAP:
            if kl not in self._rel_pending:
                self._rel_pending[kl] = self.root.after(
                    15, lambda: self._real_release(kl, e))
            return
        self._real_release(kl, e)

    def _real_release(self, kl, e):
        self._rel_pending.pop(kl, None)
        if kl in self.rec_down:
            self._rec_release(kl, e)
        v = self._edit_down.pop(kl, None)
        if v:
            eng, key = v
            eng.note_off(key)
            if self._eff_mute(key[1]):
                self.root.after(1500, lambda i=key[1], g=eng:
                                self._eff_mute(i) and self.app.mixer.remove_part(g))

    def release_edit_all(self):
        self._digit_down.clear()
        for v in list(self._edit_down.values()):
            if v:
                v[0].note_off(v[1])
        self._edit_down.clear()

    def _enter_note(self, kl, e=None):
        self.sel_a = None
        t = self.song["tracks"][self.sel]
        semi = KEYMAP[kl] + (t["octave"] - 3) * 12
        pl = self.player
        if pl is not None and not pl.done and self.rec_var.get():
            self._rec_press(kl, e, pl, semi)
            return
        eng = self.engines[self.sel]
        if eng is not None and not t["mute"]:
            if self._eff_mute(self.sel):   # nur durch Solo stumm: kurz einhängen
                self.app.mixer.add_part(eng, t["pan"] / 100.0)
            key = ("seq-edit", self.sel, kl)
            eng.note_on(key, semi)                     # nur zur Orientierung
            self._edit_down[kl] = (eng, key)
        else:
            self._edit_down[kl] = None
        patternedit.set_note(self.pat["notes"], self.pat["rows"], self.crow, self.sel, semi)
        self.crow = min(self.pat["rows"] - 1, self.crow + self._step())
        self._edited()
        self.pgrid.draw_cursor()


    # ---- Reihenfolge: Ziehen und Verschieben
    def on_ord_press(self, e):
        lb = self.lb_ord
        if lb.size() == 0:
            return "break"
        i = lb.nearest(e.y)
        lb.selection_clear(0, "end")
        lb.selection_set(i)
        self._drag_idx, self._drag_moved = i, False
        return "break"

    def on_ord_drag(self, e):
        i = self._drag_idx
        if i is None:
            return "break"
        lb = self.lb_ord
        if e.y < 0:
            lb.yview_scroll(-1, "units")
        elif e.y > lb.winfo_height():
            lb.yview_scroll(1, "units")
        t = max(0, min(lb.size() - 1, lb.nearest(e.y)))
        if t != i:
            order = self.song["order"]
            order.insert(t, order.pop(i))
            self._drag_idx, self._drag_moved = t, True
            self.refresh_order(select=t)
        return "break"

    def on_ord_release(self, e=None):
        if self._drag_moved:
            self.set_dirty()
        self._drag_idx, self._drag_moved = None, False

    def on_ord_dbl(self, e=None):
        sel = self.lb_ord.curselection()
        if sel:
            self._show_pattern(self.song["order"][sel[0]])
        return "break"

    def on_ord_move(self, d):
        sel = self.lb_ord.curselection()
        if not sel:
            return
        i, t = sel[0], sel[0] + d
        order = self.song["order"]
        if not 0 <= t < len(order):
            return
        order.insert(t, order.pop(i))
        self.refresh_order(select=t)
        self.set_dirty()

    # ---- Einspielen (Rec)
    def _on_metro(self):
        if self.player is not None:
            self.player.metro = bool(self.metro_var.get())
        self.root.focus_set()

    def _on_rec_toggle(self):
        if not self.rec_var.get():
            for kl in list(self.rec_down):
                self._rec_release(kl)
        self.root.focus_set()

    def _on_lat(self):
        v = _clamp(self._get(self.lat_var), -300, 300, 0)
        self.lat_var.set(v)
        s = load_settings()
        s["rec_offset_ms"] = v
        save_settings(s)
        self.root.focus_set()

    def _heard_pos(self, pl, e):
        """Position in Samples, die der Spieler zum Zeitpunkt des Tastendrucks gehoert hat."""
        lag = event_lag(e)
        off = _clamp(self._get(self.lat_var), -300, 300, 0) / 1000.0
        p = pl.estimate_pos() - (self.app.mixer.latency() + lag - off) * SR
        end = pl.end_t
        if pl.loop and end > 0:
            if p >= end:
                p -= end
            elif p < 0 and pl.loops > 0:
                p += end
        return p

    @staticmethod
    def _locate(pl, g):
        m = pl.marks[0]
        for mk in pl.marks:
            if mk[0] <= g:
                m = mk
            else:
                break
        start, _opos, pi, rows = m
        return pi, g - start, rows

    def _rec_press(self, kl, e, pl, semi):
        tr = self.sel
        self._edit_down[kl] = None                     # Auto-Repeat sperren
        if self.engines[tr] is None:
            self.lbl_play.config(text="Aufnahmespur hat keinen Klang")
            return
        spr = pl.plan.spr
        p = self._heard_pos(pl, e)
        gq = int(math.floor(p / spr + 0.5))
        #print(f"REC {p / spr:.2f} -> {gq}", flush=True)
        wrapped = False
        if gq < 0:
            return                                     # zu frueh im Vorzaehler
        if gq >= pl.total_rows:
            if not pl.loop:
                return
            gq %= pl.total_rows
            wrapped = True
        pi, r, rows = self._locate(pl, gq)
        patternedit.set_note(self.song["patterns"][pi]["notes"], rows, r, tr, semi)
        self.rec_down[kl] = (pi, r, tr, semi, gq)
        if not wrapped and round(gq * spr) <= pl.estimate_pos() + 2048:
            pl.inject(tr, semi)                        # sofort klingen lassen
        self._edited()

    def _rec_release(self, kl, e=None):
        info = self.rec_down.pop(kl, None)
        if info is None:
            return
        pi, r, tr, semi, g0 = info
        pl = self.player
        ln = 10 ** 6
        if pl is not None and not pl.done:
            p = self._heard_pos(pl, e)
            gq = int(math.floor(p / pl.plan.spr + 0.5))
            ln = gq - g0
            if ln < 0 and pl.loop:
                ln += pl.total_rows
            ln = max(1, ln)
        for n in self.song["patterns"][pi]["notes"]:
            if n[0] == r and n[1] == tr and n[2] == semi:
                n[3] = max(1, min(ln, n[3]))
                break
        self._edited()

