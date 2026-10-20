import math
import json
import os
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np

import patch as patchlib
from config import (SR, KEYMAP, ROOT_CHOICES, NORMALIZE_DBFS, PATCH_VERSION, sub_dir)
from audio_utils import (load_wav, normalize_gain, render_offline,
                         trim_silence, write_wav16)
from engine import Engine
from mixer import Mixer
from patchload import LIMITS, INT_ATTRS, load_patch_into
from widgets import Knob, install_knob_keys
from soundbank import SoundbankTab
from sequencer import SequencerTab

def _p(label, attr):
    lo, hi = LIMITS[attr]
    return (label, lo, hi, attr)


ENV_PARAMS = [_p("Attack s", 'A'), _p("Decay s", 'D'), _p("Sustain", 'S'),
              _p("Release s", 'R'), _p("Volume", 'volume')]

LOOP_PARAMS = [_p("Loop-Start", 'loop_start'), _p("Loop-Ende", 'loop_end'),
               _p("Crossfade", 'loop_xfade')]

UNI_PARAMS = [_p("Unisono", 'unison'), _p("Verstimmung", 'detune')]


HINT = ("Tasten: Q W E R T Z U I O P Ü (weiss), 2 3 5 6 7 9 0 (schwarz)   |   "
        "Oktave: F1-F5   |   Regler: Ziehen, Mausrad, Pfeiltasten (Umschalt fein, Strg grob), "
        "Doppelklick = Reset")


def _same_patch(a, b):
    """Vergleicht zwei Patch-Dicts; None-Werte zählen als fehlend, Zahlen mit Toleranz."""
    if isinstance(a, dict) and isinstance(b, dict):
        ka = {k for k, v in a.items() if v is not None}
        kb = {k for k, v in b.items() if v is not None}
        return ka == kb and all(_same_patch(a[k], b[k]) for k in ka)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same_patch(x, y) for x, y in zip(a, b))
    if (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)):
        return math.isclose(a, b, rel_tol=1e-3, abs_tol=1e-3)
    return a == b

class App:
    def __init__(self, root):
        self.root = root
        root.title("SynthSeq")
        try:
            root.state('zoomed')
        except tk.TclError:
            root.geometry('1400x850')
        root.minsize(1100, 700)

        self.eng = Engine()
        self.mixer = Mixer()
        self.mixer.add_part(self.eng)
        self.pending = {}
        self.down = set()
        self.octave = tk.IntVar(value=3)
        self.scales = {}
        self.knobs = []
        self.fx_vars = {}
        self.sample_path = None
        self.mod_path = None
        self.tiles = []
        self._layout_key = None
        self.edit_path = None

        hint = ttk.Label(root, text=HINT, relief='sunken', anchor='w', padding=(8, 2))
        hint.pack(side='bottom', fill='x')

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill='both', expand=True)
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Generator")

        top = ttk.Frame(tab, padding=8)
        top.pack(fill='x')
        self.wave_var = tk.StringVar(value='sine')
        for txt, val in (("Sinus", 'sine'), ("Rechteck", 'square'),
                         ("Sägezahn", 'saw'), ("Rauschen", 'noise')):
            ttk.Radiobutton(top, text=txt, value=val, variable=self.wave_var,
                            command=self.on_wave).pack(side='left', padx=4)
        ttk.Separator(top, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Label(top, text="Oktave (F1-F5):").pack(side='left')
        for o in range(1, 6):
            ttk.Radiobutton(top, text=str(o), value=o, variable=self.octave,
                            command=self.focus_keys).pack(side='left')

        src = ttk.Frame(tab, padding=(8, 2))
        src.pack(fill='x')
        self.src_var = tk.StringVar(value='generator')
        ttk.Label(src, text="Quelle:").pack(side='left')
        ttk.Radiobutton(src, text="Generator", value='generator', variable=self.src_var,
                        command=self.on_source).pack(side='left', padx=4)
        ttk.Radiobutton(src, text="Sample", value='sample', variable=self.src_var,
                        command=self.on_source).pack(side='left', padx=4)
        ttk.Button(src, text="Lade WAV…", command=self.on_load).pack(side='left', padx=6)
        ttk.Button(src, text="Effekte anwenden", command=self.on_apply).pack(side='left', padx=2)
        ttk.Label(src, text="Root-Note:").pack(side='left', padx=(10, 2))
        self.root_var = tk.StringVar(value="C4")
        cb = ttk.Combobox(src, textvariable=self.root_var, values=list(ROOT_CHOICES),
                          width=5, state='readonly')
        cb.pack(side='left')
        cb.bind('<<ComboboxSelected>>', self.on_root)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(src, text="Loop", variable=self.loop_var,
                        command=self.on_loop).pack(side='left', padx=8)
        self.snap_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(src, text="Nulldurchgang", variable=self.snap_var,
                        command=self.on_snap).pack(side='left', padx=4)
        self.norm_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(src, text="Normalize", variable=self.norm_var,
                        command=self.on_norm).pack(side='left', padx=4)
        self.smp_label = ttk.Label(src, text="(kein WAV geladen)")
        self.smp_label.pack(side='left', padx=6)

        ttk.Button(src, text="Modulator laden…", command=self.on_load_mod).pack(side='left', padx=6)
        self.mod_label = ttk.Label(src, text="(kein Modulator)")
        self.mod_label.pack(side='left', padx=2)

        rec = ttk.Frame(tab, padding=(8, 0))
        rec.pack(fill='x')
        self.btn_rec = ttk.Button(rec, text="● Record", command=self.on_record)
        self.btn_rec.pack(side='left', padx=2)
        self.btn_stop = ttk.Button(rec, text="■ Stop", command=self.on_stop, state='disabled')
        self.btn_stop.pack(side='left', padx=2)
        ttk.Separator(rec, orient='vertical').pack(side='left', fill='y', padx=8)
        ttk.Button(rec, text="Patch speichern…", command=self.on_save_patch).pack(side='left', padx=2)
        ttk.Button(rec, text="Patch laden…", command=self.on_load_patch).pack(side='left', padx=2)
        ttk.Button(rec, text="Defaults", command=self.on_defaults).pack(side='left', padx=2)
        self.btn_back = ttk.Button(rec, text="Zurück in die Bank",
                                   command=self.on_back_to_bank, state='disabled')
        self.btn_back.pack(side='left', padx=2)
        self.status = ttk.Label(rec, text="Bereit")
        self.status.pack(side='left', padx=10)

        self.cv = tk.Canvas(tab, height=150, bg='black', highlightthickness=0)
        self.cv.pack(side='bottom', fill='x', padx=8, pady=4)
        self.cv.bind('<Configure>', lambda e: self.draw())

        self.fx_outer = ttk.Frame(tab)
        self.fx_outer.pack(fill='both', expand=True, padx=8, pady=2)
        self.fx_cv = tk.Canvas(self.fx_outer, highlightthickness=0, yscrollincrement=20)
        sb = ttk.Scrollbar(self.fx_outer, orient='vertical', command=self.fx_cv.yview)
        self.fx_cv.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.fx_cv.pack(side='left', fill='both', expand=True)
        self.tile_host = ttk.Frame(self.fx_cv)
        self._win_id = self.fx_cv.create_window((0, 0), window=self.tile_host, anchor='nw')
        self.tile_host.bind('<Configure>',
                            lambda e: self.fx_cv.configure(scrollregion=self.fx_cv.bbox('all')))
        self.fx_cv.bind('<Configure>', self.on_area_resize)
        root.bind_all('<MouseWheel>', self.on_wheel)

        self.make_tile("Hüllkurve / Lautstärke", ENV_PARAMS, self.eng)

        self.make_tile("Loop-Punkte", LOOP_PARAMS, self.eng)
        for a in ('loop_start', 'loop_end', 'loop_xfade'):
            self.scales[(id(self.eng), a)].command = (
                lambda v, a=a: (setattr(self.eng, a, float(v)), self.draw()))
        self.make_tile("Unisono", UNI_PARAMS, self.eng)
        for fx in self.eng.chain:
            self.make_tile(fx.name, fx.PARAMS, fx, fx)

        for o in range(1, 6):
            root.bind(f'<F{o}>', lambda e, o=o: self.set_octave(o))
        root.bind('<KeyPress>', self.on_press)
        root.bind('<KeyRelease>', self.on_release)

        install_knob_keys(root, enabled=lambda: self.nb.select() != str(self.sequencer))
        for _k in ("Left", "Right", "Up", "Down"):
            root.bind(f"<{_k}>",
                    lambda e: self.on_press(e) if self.nb.select() == str(self.sequencer) else None,
                    add="+")

        root.protocol("WM_DELETE_WINDOW", self.close)

        root.after_idle(self.relayout)
        self.capture_defaults()
        self.soundbank = SoundbankTab(self.nb, self)
        self.nb.add(self.soundbank, text="SoundBank")
        self.sequencer = SequencerTab(self.nb, self)
        self.nb.add(self.sequencer, text="Sequencer")
        self.nb.bind('<<NotebookTabChanged>>', self.on_tab_changed)
        self.draw()
        self.mixer.start()

    # ---- Kacheln und Regler
    def make_tile(self, title, params, target, fx=None):
        tile = ttk.Frame(self.tile_host, relief='groove', borderwidth=2, padding=4)
        ncols = max(1, (len(params) + 1) // 2)
        if fx is not None:
            var = tk.BooleanVar(value=False)
            self.fx_vars[fx.name] = var
            ttk.Checkbutton(tile, text=title, variable=var,
                            command=lambda fx=fx, var=var: self.on_fx(fx, var)
                            ).grid(row=0, column=0, columnspan=ncols, sticky='w')
        else:
            ttk.Label(tile, text=title, font=('Segoe UI', 9, 'bold')
                      ).grid(row=0, column=0, columnspan=ncols, sticky='w')
        for i, (label, lo, hi, attr) in enumerate(params):
            self.add_knob(tile, 1 + i // ncols, i % ncols, label, lo, hi, target, attr)
        self.tiles.append(tile)
        return tile

    def add_knob(self, parent, row, col, name, lo, hi, target, attr):
        step = 1.0 if attr in INT_ATTRS else None
        init = getattr(target, attr)
        k = Knob(parent, name, lo, hi, init,
                 command=lambda v, a=attr, t=target: setattr(t, a, float(v)),
                 default=init, step=step,
                 fmt=getattr(target, 'DISPLAY', {}).get(attr))
        k.grid(row=row, column=col, padx=2, pady=1)
        self.scales[(id(target), attr)] = k
        self.knobs.append((target, attr, k))

    def set_param(self, target, attr, val):
        s = self.scales.get((id(target), attr))
        if s is not None:
            s.set(val)
            val = s.get()
        setattr(target, attr, float(val))

    def on_area_resize(self, e):
        self.fx_cv.itemconfigure(self._win_id, width=e.width)
        self.relayout()

    def relayout(self):
        if not self.tiles:
            return
        avail = self.fx_cv.winfo_width()
        if avail < 50:
            return
        self.tile_host.update_idletasks()
        gap = 8
        rows, cur, x = [], [], gap
        for t in self.tiles:
            tw = t.winfo_reqwidth()
            if cur and x + tw + gap > avail:
                rows.append(cur)
                cur, x = [], gap
            cur.append(t)
            x += tw + gap
        if cur:
            rows.append(cur)
        key = tuple(tuple(id(t) for t in r) for r in rows)
        if key == self._layout_key:
            return
        self._layout_key = key
        y = gap
        for r in rows:
            x = gap
            rh = max(t.winfo_reqheight() for t in r)
            for t in r:
                t.place(x=x, y=y)
                x += t.winfo_reqwidth() + gap
            y += rh + gap
        self.tile_host.configure(height=y)

    def on_wheel(self, e):
        try:
            w = self.root.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        while w is not None:
            if w is self.fx_outer:
                self.fx_cv.yview_scroll(int(-e.delta / 120), 'units')
                return
            w = w.master

    def on_fx(self, fx, var):
        fx.enabled = var.get()
        self.root.focus_set()

    def set_octave(self, o):
        self.octave.set(o)

    def focus_keys(self):
        self.root.focus_set()

    # ---- Defaults
    def capture_defaults(self):
        eng = self.eng
        self._defaults = []
        for attr in ('A', 'D', 'S', 'R', 'volume', 'unison', 'detune',
                     'loop_start', 'loop_end', 'loop_xfade'):
            self._defaults.append((eng, attr, getattr(eng, attr)))
        for fx in eng.chain:
            for _l, _lo, _hi, attr in fx.PARAMS:
                self._defaults.append((fx, attr, getattr(fx, attr)))

    def reset_defaults(self):
        self.set_edit_target(None)
        eng = self.eng
        eng.source = 'generator'
        self.src_var.set('generator')
        for target, attr, val in self._defaults:
            self.set_param(target, attr, val)
        for fx in eng.chain:
            fx.enabled = False
            self.fx_vars[fx.name].set(False)
        self.wave_var.set('sine')
        eng.set_wave('sine')
        self.root_var.set('C2')
        eng.root_semi = ROOT_CHOICES['C2']
        self.loop_var.set(False)
        eng.sample_loop = False
        self.snap_var.set(False)
        eng.loop_snap = False
        self.norm_var.set(False)
        eng.normalize = False
        eng.sample = np.zeros(0, np.float32)
        eng.sample_sr = SR
        eng.sample_gain = 1.0
        eng._snap_key = None
        self.sample_path = None
        self.smp_label.config(text="(kein WAV geladen)")
        vc = next((fx for fx in eng.chain if fx.name == "Vocoder"), None)
        if vc is not None:
            vc.mod = np.zeros(0, np.float32)
            vc.mpos = 0
        self.mod_path = None
        self.mod_label.config(text="(kein Modulator)")
        self.draw()

    def on_defaults(self):
        self.reset_defaults()
        self.status.config(text="Defaults gesetzt")
        self.root.focus_set()


    # ---- Quelle / Sample
    def on_source(self):
        self.eng.source = self.src_var.get()
        if self.eng.source == 'sample' and len(self.eng.sample) == 0:
            self.status.config(text="Kein WAV geladen")
        self.draw()
        self.root.focus_set()

    def load_sample_file(self, path):
        try:
            data, sr = load_wav(path)
        except Exception as e:
            messagebox.showerror("WAV laden", f"Konnte die Datei nicht laden:\n{e}")
            return False

        trimmed = trim_silence(data)
        if trimmed is not None:
            data = trimmed

        self.eng.set_sample(data, sr)
        self.sample_path = path
        self.smp_label.config(text=f"{os.path.basename(path)}  ({len(data) / sr:.2f} s, {sr} Hz)")
        return True

    def on_load(self):
        path = filedialog.askopenfilename(initialdir=sub_dir("WAV"),
                                          filetypes=[("WAV", "*.wav")])
        self.root.focus_set()
        if not path:
            return
        if self.load_sample_file(path):
            self.eng.source = 'sample'
            self.src_var.set('sample')
            self.draw()

    def load_mod_file(self, path):
        try:
            data, sr = load_wav(path)
        except Exception as e:
            messagebox.showerror("Modulator laden", f"Konnte die Datei nicht laden:\n{e}")
            return False
        trimmed = trim_silence(data)
        if trimmed is not None:
            data = trimmed
        vc = next(fx for fx in self.eng.chain if fx.name == "Vocoder")
        vc.set_modulator(data, sr)
        self.mod_path = path
        self.mod_label.config(text=f"{os.path.basename(path)} ({len(data) / sr:.2f} s)")
        return True

    def on_load_mod(self):
        path = filedialog.askopenfilename(initialdir=sub_dir("WAV"),
                                          filetypes=[("WAV", "*.wav")])
        self.root.focus_set()
        if path:
            self.load_mod_file(path)

    def on_apply(self):
        self.root.focus_set()
        if len(self.eng.sample) < 2:
            self.status.config(text="Kein WAV geladen")
            return

        def prog(f):
            self.status.config(text=f"Rendere… {f * 100:.0f}%")
            self.root.update_idletasks()

        res = render_offline(self.eng.sample, self.eng.sample_sr, self.eng.chain, prog)
        if res is None:
            self.status.config(text="Kein Effekt eingeschaltet")
            return
        trimmed = trim_silence(res)
        if trimmed is None:
            self.status.config(text="Ergebnis ist still")
            return
        self.status.config(text=f"Gerendert: {len(trimmed) / SR:.2f} s")
        self.show_preview(trimmed)

    def on_root(self, event=None):
        self.eng.root_semi = ROOT_CHOICES[self.root_var.get()]
        self.root.focus_set()

    def on_loop(self):
        self.eng.sample_loop = self.loop_var.get()
        self.draw()
        self.root.focus_set()

    def on_snap(self):
        self.eng.loop_snap = self.snap_var.get()
        #self.status.config(text=f"Loop: {self.eng.loop_range(len(self.eng.sample))}")
        self.draw()
        self.root.focus_set()

    def on_norm(self):
        self.eng.normalize = self.norm_var.get()
        self.eng.update_gain()
        self.root.focus_set()

    def on_wave(self):
        self.eng.set_wave(self.wave_var.get())
        self.draw()
        self.root.focus_set()

    # ---- Patch
    def make_patch(self, patch_path):
        eng = self.eng
        sample = None
        mod = patchlib.sample_reference(self.mod_path, patch_path) if self.mod_path else None
        if self.sample_path and self.src_var.get() == 'sample':
            sample = patchlib.sample_reference(self.sample_path, patch_path)
        return {
            "format": "synth-patch",
            "version": PATCH_VERSION,
            "name": os.path.splitext(os.path.basename(patch_path))[0],
            "source": {
                "type": self.src_var.get() if sample else "generator",
                "wave": self.wave_var.get(),
                "root": self.root_var.get(),
                "loop": bool(self.loop_var.get()),
                "loop_start": eng.loop_start,
                "loop_end": eng.loop_end,
                "loop_xfade": eng.loop_xfade,
                "loop_snap": bool(self.snap_var.get()),
                "sample": sample,
            },
            "envelope": {"attack": eng.A, "decay": eng.D, "sustain": eng.S, "release": eng.R},
            "volume": eng.volume,
            "unison": int(eng.unison),
            "detune": eng.detune,
            "normalize": bool(self.norm_var.get()),
            "modulator": mod,
            "effects": [
                {"type": fx.name, "enabled": bool(fx.enabled),
                 "params": {attr: getattr(fx, attr) for _l, _lo, _hi, attr in fx.PARAMS}}
                for fx in eng.chain
            ],
        }

    def sync_gui(self):
        """Regler und Schalter aus dem Zustand der Engine neu setzen."""
        eng = self.eng
        for target, attr, _k in self.knobs:
            self.set_param(target, attr, getattr(target, attr))
        self.wave_var.set(eng.kind)
        label = next((l for l, s in ROOT_CHOICES.items() if s == eng.root_semi), "C4")
        self.root_var.set(label)
        self.loop_var.set(bool(eng.sample_loop))
        self.snap_var.set(bool(eng.loop_snap))
        self.norm_var.set(bool(eng.normalize))
        self.src_var.set(eng.source)
        for fx in eng.chain:
            self.fx_vars[fx.name].set(bool(fx.enabled))

    def apply_patch(self, d, patch_path):
        self.set_edit_target(None)
        res = load_patch_into(self.eng, d, patch_path)
        if res.sample_path:
            self.sample_path = res.sample_path
            sr = self.eng.sample_sr
            self.smp_label.config(
                text=f"{os.path.basename(res.sample_path)} "
                     f"({len(self.eng.sample) / sr:.2f} s, {sr} Hz)")
        if res.mod_path:
            vc = next(fx for fx in self.eng.chain if fx.name == "Vocoder")
            self.mod_path = res.mod_path
            self.mod_label.config(
                text=f"{os.path.basename(res.mod_path)} ({len(vc.mod) / SR:.2f} s)")
        self.sync_gui()
        self.draw()
        if res:
            messagebox.showwarning("Patch laden", "\n".join(res))

    def on_save_patch(self):
        self.root.focus_set()
        path = filedialog.asksaveasfilename(initialdir=sub_dir("PATCHES"),
            defaultextension=".json", initialfile="patch.json",
            filetypes=[("Synth-Patch (JSON)", "*.json")])
        if not path:
            return
        try:
            patchlib.write_patch(path, self.make_patch(path))
        except Exception as e:
            messagebox.showerror("Patch speichern", str(e))
            return
        self.status.config(text=f"Patch gespeichert: {os.path.basename(path)}")

    def on_load_patch(self):
        self.root.focus_set()
        path = filedialog.askopenfilename(initialdir=sub_dir("PATCHES"),
         filetypes=[("Synth-Patch (JSON)", "*.json")])
        if not path:
            return
        try:
            d = patchlib.read_patch(path)
            self.apply_patch(d, path)
        except Exception as e:
            messagebox.showerror("Patch laden", str(e))
            return
        self.status.config(text=f"Patch geladen: {os.path.basename(path)}")

    # ---- Bearbeiten aus der Soundbank
    def set_edit_target(self, path):
        self.edit_path = path
        self.btn_back.config(state='normal' if path else 'disabled')

    def on_back_to_bank(self):
        self.root.focus_set()
        path = self.edit_path
        if not path:
            return
        try:
            new = json.loads(json.dumps(self.make_patch(path)))
            changed = True
            if os.path.isfile(path):
                changed = not _same_patch(new, patchlib.read_patch(path))
        except Exception as e:
            messagebox.showerror("Zurück in die Bank", str(e))
            return
        if not changed:
            self.status.config(text="Keine Änderung - nichts gespeichert")
        else:
            r = messagebox.askyesnocancel(
                "Zurück in die Bank",
                f"Patch '{os.path.basename(path)}' wurde geändert.\n\n"
                "Ja: speichern (mit Sicherung .bak) und zurück\n"
                "Nein: Änderungen verwerfen und zurück\n"
                "Abbrechen: hier im Generator bleiben")
            if r is None:
                return
            if r:
                try:
                    if os.path.isfile(path):
                        shutil.copy2(path, path + ".bak")
                    patchlib.write_patch(path, new)
                except Exception as e:
                    messagebox.showerror("Zurück in die Bank", str(e))
                    return
                self.status.config(text=f"Patch gespeichert: {os.path.basename(path)}")
            else:
                self.status.config(text="Änderungen verworfen")
        self.set_edit_target(None)
        self.nb.select(self.soundbank)

    # ---- Aufnahme
    def on_record(self):
        self.mixer.start_recording()
        self.btn_rec.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.tick()
        self.root.focus_set()

    def tick(self):
        if self.mixer.recording:
            self.status.config(text=f"● Aufnahme läuft  {self.mixer.rec_frames / SR:5.1f} s")
            self.root.after(200, self.tick)

    def on_stop(self):
        data = self.mixer.stop_recording()
        self.btn_rec.config(state='normal')
        self.btn_stop.config(state='disabled')
        self.root.focus_set()
        if data is None:
            self.status.config(text="Nichts aufgenommen")
            return
        trimmed = trim_silence(data)
        if trimmed is None:
            self.status.config(text="Aufnahme war still - nichts zu speichern")
            return
        self.status.config(text=f"Aufnahme: {len(trimmed) / SR:.2f} s (getrimmt)")
        self.show_preview(trimmed)

    def show_preview(self, data):
        win = tk.Toplevel(self.root)
        win.title("Aufnahme prüfen")
        info = ttk.Label(win, padding=8)
        info.pack()
        cv = tk.Canvas(win, width=500, height=90, bg='black')
        cv.pack(padx=8)
        nv = tk.BooleanVar(value=self.norm_var.get())

        def current():
            return data * normalize_gain(data) if nv.get() else data

        def refresh():
            d = current()
            peak = float(np.max(np.abs(d)))
            db = 20 * math.log10(max(peak, 1e-9))
            kind = "Stereo" if d.ndim == 2 else "Mono"
            info.config(text=f"Dauer: {len(d) / SR:.2f} s  Spitze: {db:.1f} dBFS  "
                             f"16 Bit {kind}, {SR} Hz")
            cv.delete('all')
            bins = 500
            a = np.abs(d)
            if a.ndim == 2:
                a = a.max(axis=1)
            k = max(1, len(a) // bins)
            n = len(a) // k
            env = a[:n * k].reshape(n, k).max(axis=1)

            for i, v in enumerate(env):
                x = i * 500 / len(env)
                h = min(1.0, float(v)) * 40
                cv.create_line(x, 45 - h, x, 45 + h, fill='#4f4')

        def toggle():
            refresh()
            self.mixer.play_preview(current())

        ttk.Checkbutton(win, text=f"Normalisieren ({NORMALIZE_DBFS:.0f} dBFS)",
                        variable=nv, command=toggle).pack()

        bf = ttk.Frame(win, padding=8)
        bf.pack()
        ttk.Button(bf, text="▶ Anhören",
                   command=lambda: self.mixer.play_preview(current())).pack(side='left', padx=3)
        ttk.Button(bf, text="■ Stopp", command=self.mixer.stop_preview).pack(side='left', padx=3)

        def close():
            self.mixer.stop_preview()
            win.destroy()
            self.root.focus_set()

        def save():
            path = filedialog.asksaveasfilename(parent=win, initialdir=sub_dir("WAV"),
                                                defaultextension=".wav",
                                                initialfile="aufnahme.wav",
                                                filetypes=[("WAV", "*.wav")])

            if path:
                write_wav16(path, current())
                self.status.config(text=f"Gespeichert: {path}")
                close()

        ttk.Button(bf, text="Speichern…", command=save).pack(side='left', padx=3)
        ttk.Button(bf, text="Verwerfen", command=close).pack(side='left', padx=3)
        win.protocol("WM_DELETE_WINDOW", close)
        refresh()
        self.mixer.play_preview(current())

    def typing_in_entry(self):
        try:
            w = self.root.focus_get()
        except KeyError:
            return False
        return isinstance(w, (tk.Entry, ttk.Entry))

    # ---- Tastatur
    def on_press(self, e):
        if self.typing_in_entry():
            return
        if self.nb.select() == str(self.sequencer):
            self.sequencer.on_key_press(e)
            return
        k = e.keysym.lower()
        if k not in KEYMAP:
            return
        if k in self.pending:
            self.root.after_cancel(self.pending.pop(k))
            return
        if k in self.down:
            return
        self.down.add(k)
        self.eng.note_on(k, KEYMAP[k] + (self.octave.get() - 3) * 12)

    def on_release(self, e):
        if self.nb.select() == str(self.sequencer):
            self.sequencer.on_key_release(e)
            return
        k = e.keysym.lower()
        if k in KEYMAP and k in self.down and k not in self.pending:
            self.pending[k] = self.root.after(15, lambda: self.real_release(k))

    def real_release(self, k):
        self.pending.pop(k, None)
        self.down.discard(k)
        self.eng.note_off(k)

    # ---- Anzeige
    def draw_sample(self):
        cv = self.cv
        cv.delete('all')
        W, H = max(cv.winfo_width(), 400), 150
        d = self.eng.sample
        k = max(1, len(d) // W)
        n = len(d) // k
        env = np.abs(d[:n * k].reshape(n, k)).max(axis=1)
        cv.create_line(0, H / 2, W, H / 2, fill='#444')
        for i, v in enumerate(env):
            x = i * W / n
            h = min(1.0, float(v)) * (H / 2 - 10)
            cv.create_line(x, H / 2 - h, x, H / 2 + h, fill='#4f4')
        cv.create_text(6, 10, anchor='w', fill='#aaa',
                       text=f"Sample: {len(d) / self.eng.sample_sr:.2f} s")
        if self.eng.sample_loop:
            ls_, le_ = self.eng.loop_range(len(d))
            for frac_, name in ((ls_ / len(d), "S"), (le_ / len(d), "E")):
                x = frac_ * W
                cv.create_line(x, 0, x, H, fill='orange', width=2)
                cv.create_text(x + 4, H - 10, anchor='w', fill='orange', text=name)

    def draw(self):
        if self.eng.source == 'sample' and len(self.eng.sample) > 1:
            self.draw_sample()
            return
        cv = self.cv
        cv.delete('all')
        W, H = max(cv.winfo_width(), 400), 150
        t = self.eng.table.astype(int)
        if self.eng.kind == 'noise':
            t = t[:512]
        two = np.concatenate([t, t])
        xs = np.linspace(0, W, len(two))
        ys = H / 2 - two / 127.0 * (H / 2 - 10)
        cv.create_line(0, H / 2, W, H / 2, fill='#444')
        cv.create_line(*np.column_stack([xs, ys]).ravel().tolist(), fill='#4f4')
        cv.create_line(W / 2, 0, W / 2, H, fill='red', dash=(4, 3))
        cv.create_text(W / 2 + 6, 10, anchor='w', fill='red', text="Loop-Naht")
        cv.create_text(6, 10, anchor='w', fill='#aaa',
                       text=f"{self.eng.kind}  Anfang={t[0]}  Ende={t[-1]}  N={len(self.eng.table)}")

    def on_tab_changed(self, e=None):
        self.soundbank.on_tab_changed(e)
        self.sequencer.on_tab_changed(e)

    def close(self):
        if not self.soundbank.confirm_discard():
            return
        if not self.sequencer.confirm_discard():
            return
        self.soundbank.release_note()
        self.sequencer.shutdown()
        self.mixer.stop()
        self.root.destroy()


