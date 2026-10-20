# -*- coding: utf-8 -*-
"""songexport.py - Song (synth-song) als WAV und MP3 speichern.

Rendert offline (schneller als Echtzeit) mit denselben Klassen wie die Wiedergabe: Engine, Player, Mixer-Logik.
Speichert nach EXPORT/<Songname>.wav und EXPORT/<Songname>.mp3 (vorhandene Dateien werden ueberschrieben).
MP3 braucht das Paket lameenc ("pip install lameenc"); ohne lameenc wird nur die WAV-Datei geschrieben.

Aufruf aus dem Sequencer: songexport.export_dialog(root, song, name)  (Fortschrittsfenster, eigener Thread).
"""
import copy
import math
import os
import re
import threading
import time
import wave

import numpy as np

import patch as patchlib
from config import BASE_DIR, NORMALIZE_DBFS, SR, sub_dir
from engine import Engine
from mixer import CENTER, RAMP_STEP, Mixer
from patchload import load_patch_into
from player import compile_song

BLOCK = 2048             # Samples je Rendering-Schritt
TAIL_MAX_S = 8.0         # laengster Nachlauf nach dem Songende (Hall, Echo, Release)
SILENCE = 1e-4           # Pegel (ca. -80 dBFS), unter dem der Nachlauf als beendet gilt
SILENCE_S = 0.3          # so lange muss es still sein
FADE_S = 0.05            # kurzes Ausblenden am Ende
MP3_KBPS = 256
MP3_QUALITY = 2          # 2 = hoechste Qualitaet, 7 = am schnellsten


class Cancelled(Exception):
    pass


class _OfflineMixer(Mixer):
    """Wie Mixer._callback, aber ohne Audiogeraet, ohne Metronom und OHNE Begrenzung (np.clip)."""

    def block(self, frames):
        out = np.zeros((frames, 2), np.float32)
        pos = 0
        while pos < frames:
            n = frames - pos
            pl = self.player
            if pl is not None:
                n = pl.prepare(n)
            gains = self.gains
            for part in self.parts:
                x = part.render(n)
                tgt = gains.get(part, CENTER)
                cur = self._cur.get(part, tgt)
                if cur == tgt:
                    self._cur[part] = tgt
                    out[pos:pos + n, 0] += x * tgt[0]
                    out[pos:pos + n, 1] += x * tgt[1]
                else:
                    ramp = np.arange(1, n + 1, dtype=np.float32) / n
                    mv = RAMP_STEP * n
                    new, g = [], []
                    for c, t in zip(cur, tgt):
                        d = t - c
                        e = t if abs(d) <= mv else c + math.copysign(mv, d)
                        g.append(c + (e - c) * ramp)
                        new.append(e)
                    self._cur[part] = (new[0], new[1])
                    out[pos:pos + n, 0] += x * g[0]
                    out[pos:pos + n, 1] += x * g[1]
            if pl is not None:
                pl.advance(n)
                if pl.done:
                    self.player = None
            pos += n
        return out


def _abs_path(rel):
    return os.path.normpath(os.path.join(BASE_DIR, os.path.normpath(rel)))


def render_raw(song, progress=None, cancel=None, warn=None):
    """Rendert den Song einmal (Modus 'song', ohne Loop, ohne Metronom, ohne Vorzaehler).
    Rueckgabe: float32-Array (N, 2), unbegrenzt (kann ueber +-1 liegen), mit Nachlauf."""
    warn = warn if warn is not None else []
    progress = progress or (lambda f, t: None)
    cancel = cancel or (lambda: False)
    tracks = song["tracks"]
    mixer = _OfflineMixer()
    engines: list[Engine | None] = [None] * len(tracks)
    cache = {}
    used = [i for i, t in enumerate(tracks) if t.get("patch") and not t.get("mute")]
    for k, i in enumerate(used):
        if cancel():
            raise Cancelled()
        t = tracks[i]
        progress(0.05 * k / max(1, len(used)), "Lade Patches (%d/%d)" % (k + 1, len(used)))
        path = _abs_path(t["patch"])
        if not os.path.isfile(path):
            warn.append("Spur %d: Patch fehlt (%s)" % (i + 1, t["patch"]))
            continue
        try:
            d = patchlib.read_patch(path)
            eng = Engine()
            res = load_patch_into(eng, d, path, cache)
        except Exception as e:
            warn.append("Spur %d: Fehler beim Laden: %s" % (i + 1, e))
            continue
        for w in res:
            warn.append("Spur %d: %s" % (i + 1, w))
        engines[i] = eng
        mixer.add_part(eng, t["pan"] / 100.0)
    if all(e is None for e in engines):
        raise ValueError("Keine Spur mit gueltigem Patch - nichts zu rendern.")
    spr = SR * 60.0 / (song["bpm"] * song["rows_per_beat"])
    pl = compile_song(song, engines, spr, "song", 0, False, 0)
    pl.base_pan = [t["pan"] / 100.0 for t in tracks]
    if pl.total_rows <= 0 or pl.end_t <= 0:
        raise ValueError("Der Song ist leer.")
    mixer.start_player(pl)
    blocks = []
    max_blocks = int(pl.end_t / BLOCK) + 10 + int(TAIL_MAX_S * SR / BLOCK)
    while mixer.player is not None:
        if cancel():
            raise Cancelled()
        blocks.append(mixer.block(BLOCK))
        progress(0.05 + 0.85 * min(1.0, max(0, pl.pos) / pl.end_t), "Rendere Song")
        time.sleep(0.001)
        if len(blocks) > max_blocks:
            raise RuntimeError("Wiedergabe endet nicht (Abbruch nach %d Blöcken)." % len(blocks))
    quiet, n_tail = 0, 0
    need = int(SILENCE_S * SR / BLOCK) + 1
    while n_tail * BLOCK < TAIL_MAX_S * SR:
        if cancel():
            raise Cancelled()
        b = mixer.block(BLOCK)
        blocks.append(b)
        n_tail += 1
        quiet = quiet + 1 if float(np.max(np.abs(b))) < SILENCE else 0
        progress(0.90 + 0.04 * min(1.0, n_tail * BLOCK / (TAIL_MAX_S * SR)), "Rendere Nachlauf")
        if quiet >= need:
            break
    return np.concatenate(blocks, axis=0)


def finish(audio):
    """Stille am Ende abschneiden, kurz ausblenden, bei Uebersteuerung absenken.
    Rueckgabe (audio, info). info: dauer_s, peak_db, gain_db."""
    if len(audio) == 0:
        raise ValueError("Leeres Ergebnis.")
    idx = np.flatnonzero(np.max(np.abs(audio), axis=1) >= SILENCE)
    last = int(idx[-1]) if idx.size else 0
    end = min(len(audio), last + 1 + int(FADE_S * SR))
    out = audio[:end].copy()
    nf = min(len(out), int(FADE_S * SR))
    if nf > 1:
        out[-nf:] *= np.linspace(1.0, 0.0, nf, dtype=np.float32)[:, None]
    peak = float(np.max(np.abs(out)))
    gain = 1.0
    if peak > 1.0:                                  # in der Wiedergabe wuerde hier hart begrenzt
        gain = (10 ** (NORMALIZE_DBFS / 20.0)) / peak
        out *= np.float32(gain)
    info = {"dauer_s": len(out) / float(SR),
            "peak_db": 20 * math.log10(peak) if peak > 0 else -120.0,
            "gain_db": 20 * math.log10(gain)}
    return out, info


def _pcm16(x):
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")


def write_wav(path, audio):
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        for a in range(0, len(audio), 1 << 18):
            w.writeframes(_pcm16(audio[a:a + (1 << 18)]).tobytes())


def write_mp3(path, audio, kbps=MP3_KBPS):
    """Braucht lameenc (ImportError, wenn nicht installiert)."""
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(kbps)
    enc.set_in_sample_rate(SR)
    enc.set_channels(2)
    enc.set_quality(MP3_QUALITY)
    if hasattr(enc, "silence"):
        enc.silence()
    with open(path, "wb") as f:
        for a in range(0, len(audio), 1 << 18):
            f.write(enc.encode(_pcm16(audio[a:a + (1 << 18)]).tobytes()))
        f.write(enc.flush())


def safe_filename(name):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(name or "")).strip(" .")
    return s or "song"


def export_files(song, name, out_dir=None, progress=None, cancel=None):
    """Rendert und schreibt <out_dir>/<name>.wav (+ .mp3). Rueckgabe dict(wav, mp3, info, warn, mp3_hinweis)."""
    progress = progress or (lambda f, t: None)
    out_dir = out_dir or sub_dir("EXPORT")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, safe_filename(name))
    warn = []
    audio = render_raw(song, progress, cancel, warn)
    audio, info = finish(audio)
    res = {"wav": base + ".wav", "mp3": None, "info": info, "warn": warn, "mp3_hinweis": ""}
    progress(0.95, "Schreibe WAV")
    write_wav(res["wav"], audio)
    progress(0.97, "Schreibe MP3")
    try:
        write_mp3(base + ".mp3", audio)
        res["mp3"] = base + ".mp3"
    except ImportError:
        res["mp3_hinweis"] = "MP3 nicht erstellt: Paket 'lameenc' fehlt (im Terminal: pip install lameenc)."
    progress(1.0, "Fertig")
    return res


def result_text(res):
    i = res["info"]
    L = ["Dauer: %.1f s   Spitze: %.1f dBFS" % (i["dauer_s"], i["peak_db"])]
    if i["gain_db"] < -0.05:
        L.append("Übersteuerung: um %.1f dB abgesenkt (auf %.1f dBFS)" % (-i["gain_db"], NORMALIZE_DBFS))
    L.append("WAV: %s  (%.1f MB)" % (res["wav"], os.path.getsize(res["wav"]) / 1048576.0))
    if res["mp3"]:
        L.append("MP3: %s  (%.1f MB, %d kbit/s)" % (res["mp3"], os.path.getsize(res["mp3"]) / 1048576.0, MP3_KBPS))
    elif res["mp3_hinweis"]:
        L.append(res["mp3_hinweis"])
    for w in res["warn"]:
        L.append("Hinweis: " + w)
    return "\n".join(L)


def export_dialog(root, song, name, on_close=None):
    """Fortschrittsfenster; rendert in einem Thread. song wird kopiert (Editor darf weiterlaufen)."""
    import tkinter as tk
    from tkinter import ttk
    song = copy.deepcopy(song)
    st = {"frac": 0.0, "text": "Starte …", "done": False, "res": None, "err": None, "cancel": False}

    def work():
        try:
            st["res"] = export_files(song, name,
                                     progress=lambda f, t: st.update(frac=f, text=t),
                                     cancel=lambda: st["cancel"])
        except Cancelled:
            st["err"] = "Abgebrochen."
        except Exception as e:
            import traceback
            traceback.print_exc()
            st["err"] = "%s: %s" % (type(e).__name__, e)
        finally:
            st["done"] = True

    w = tk.Toplevel(root)
    w.title("WAV/MP3 speichern: " + str(name or "song"))
    w.transient(root)
    w.resizable(False, False)
    lbl = ttk.Label(w, text=st["text"], width=70, anchor="w", justify="left")
    lbl.pack(padx=12, pady=(12, 4), fill="x")
    bar = ttk.Progressbar(w, maximum=100, length=520)
    bar.pack(padx=12, pady=4)
    bf = ttk.Frame(w, padding=8)
    bf.pack(fill="x")
    btn_cancel = ttk.Button(bf, text="Abbrechen", command=lambda: st.update(cancel=True))
    btn_cancel.pack(side="right", padx=2)

    def close():
        st["cancel"] = True
        w.destroy()
        root.focus_set()
        if on_close:
            on_close()

    def open_folder(path):
        f = getattr(os, "startfile", None)
        if f is not None:
            f(os.path.dirname(path))

    def poll():
        if not w.winfo_exists():
            return
        bar["value"] = 100.0 * st["frac"]
        lbl.config(text=st["text"])
        if not st["done"]:
            w.after(150, poll)
            return
        btn_cancel.destroy()
        if st["err"]:
            lbl.config(text=st["err"])
        else:
            res = st["res"]
            bar["value"] = 100
            lbl.config(text=result_text(res), width=100)
            ttk.Button(bf, text="Ordner öffnen", command=lambda: open_folder(res["wav"])).pack(side="right", padx=2)
        ttk.Button(bf, text="Schließen", command=close).pack(side="right", padx=2)

    w.protocol("WM_DELETE_WINDOW", close)
    threading.Thread(target=work, daemon=True).start()
    poll()
