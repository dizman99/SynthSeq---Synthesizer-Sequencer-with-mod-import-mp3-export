"""player.py - Sequencer-Wiedergabe: GUI-frei, laeuft im Audio-Thread (Mixer-Callback).

build_plan() (GUI-Thread) wandelt Song-Daten in einen Plan (sortierte Ereignisliste) um.
Ereignisse sind Tupel (zeit, art, spur, a, b):
  NOTE_ON  (t, NOTE_ON,  spur, semi, lautstaerke_roh|None)
  NOTE_OFF (t, NOTE_OFF, spur, semi, None)
  CMD      (t, CMD,      spur, code, wert)
  CLICK    (t, CLICK,    erzwungen, betont, None)
Lautstaerke und Pan sind Zustand im Player (Tracker-Prinzip: der letzte Wert gilt weiter,
auch ueber Pattern- und Loop-Grenzen; Start = Patch-Lautstaerke bzw. Spur-Pan).
Player.prepare(n) / click_samples(n) / advance(n) werden vom Mixer-Callback aufgerufen.
Player.set_plan(plan) tauscht den Plan waehrend der Wiedergabe aus (Live-Editieren);
Player.inject(tr, semi) laesst eine Note sofort klingen (Einspielen).
Vorzaehler: Klick-Ereignisse mit negativer Zeit; der Player startet dann bei pos < 0.
"""
import bisect
import time
from typing import Any

import numpy as np
from config import SR, vel_gain
from fxcmd import CODE_PAN, TOGGLES, PARAMS, DISCRETE
NOTE_OFF, CMD, NOTE_ON, CLICK = 0, 1, 2, 3   # gleiche Zeit: Off, Befehl, On, Klick

def _click(freq, ms=30, amp=0.35):
    n = int(SR * ms / 1000.0)
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.006) * amp).astype(np.float32)

CLICK_STD = _click(1000.0)
CLICK_ACC = _click(1500.0)
SWAP_MARGIN = 4096   # Samples: Noten kurz vor ihrem Beginn behalten
GLIDE_N = int(SR * 0.02)   # Dauer des Parameter-Uebergangs (20 ms)
GLIDE_SEG = 128            # max. Segmentlaenge waehrend eines Uebergangs

class Plan:
    __slots__ = ("ev", "ev_t", "notes_t", "end_t", "row_t", "marks", "total_rows",
                 "spr", "rpb")

def build_plan(song, engines, spr, mode, cur, count_in=0):
    """mode 'song' = ganze Reihenfolge, 'pattern' = nur Pattern cur.
    count_in = Anzahl Vorzaehl-Beats (Klicks bei negativer Zeit)."""
    pats = song["patterns"]
    order = song["order"] if mode == "song" else [cur]
    rpb = max(1, int(song["rows_per_beat"]))
    raw, cmds, beats, marks, g = [], [], [], [], 0
    for opos, pi in enumerate(order):
        p = pats[pi]
        marks.append((g, opos, pi, p["rows"]))
        for r, tr, semi, ln, *rest in p["notes"]:
            if engines[tr] is not None:
                raw.append((g + r, g + r + ln, tr, semi, rest[0] if rest else None))
        for r, tr, code, val in p.get("cmds", []):
            if engines[tr] is not None and code > 0:
                cmds.append((g + r, tr, code, val))
        for r in range(0, p["rows"], rpb):
            beats.append((g + r, 1 if r % (rpb * 4) == 0 else 0))
        g += p["rows"]
    total = g
    ev, notes_t = [], []
    for r_on, r_off, tr, semi, vel in raw:
        a = int(round(r_on * spr))
        b = int(round(min(r_off, total) * spr))
        ev.append((a, NOTE_ON, tr, semi, vel))
        if r_off < total:             # Offs ab Ende macht _release
            ev.append((b, NOTE_OFF, tr, semi, None))
        notes_t.append((a, b, tr, semi))
    for row, tr, code, val in cmds:
        ev.append((int(round(row * spr)), CMD, tr, code, val))
    for row, acc in beats:
        ev.append((int(round(row * spr)), CLICK, 0, acc, None))
    for k in range(int(count_in)):    # Vorzaehler: forced=1, erster Beat betont
        ev.append((int(round(-(count_in - k) * rpb * spr)), CLICK, 1,
                   1 if k == 0 else 0, None))
    ev.sort(key=lambda e: (e[0], e[1]))
    plan = Plan()
    plan.ev = ev
    plan.ev_t = [e[0] for e in ev]
    plan.notes_t = notes_t
    plan.end_t = int(round(total * spr))
    plan.row_t = [int(round(r * spr)) for r in range(total + 1)]
    plan.marks = marks
    plan.total_rows = total
    plan.spr = spr
    plan.rpb = rpb
    return plan

class Player:
    def __init__(self, plan, engines, muted, loop):
        self.engines = engines      # Liste je Spur (Engine oder None), unveraendert
        self.muted = muted          # Liste je Spur (bool), GUI darf Elemente setzen
        self.loop = loop
        self.metro = False          # Metronom (GUI darf live umschalten)
        self.pending = None         # neuer Plan (vom GUI-Thread gesetzt)
        self.inbox = []             # (spur, halbton) zum sofortigen Anspielen
        self.click_wav = None
        self.click_pos = 0
        self.mixer: Any = None      # wird von Mixer.start_player gesetzt
        self.base_pan: list = [0.0] * len(engines)   # Spur-Pan (-1..1), GUI darf setzen
        self.vel_state: list = [None] * len(engines)  # None = Patch-Lautstaerke
        self.pan_touched: set = set()                 # Spuren mit Pan-Befehl
        self.fx_orig: dict = {}     # (spur, effekt, attr) -> Wert vor dem ersten Befehl
        self.fx_en_orig: dict = {}  # (spur, effekt) -> enabled vor dem ersten Befehl
        self.glides: dict = {}      # (spur, effekt, attr) -> [fx, attr, start, ziel, abgelaufen]
        self._adopt(plan)
        self.pos = min(0, plan.ev_t[0]) if plan.ev_t else 0
        self.idx = 0
        self.active = set()         # (spur, halbton) der gerade gehaltenen Noten
        self.loops = 0
        self.done = False
        self.stop_flag = False
        self.error = None
        self.clock = (self.pos, time.perf_counter())

    def _adopt(self, plan):
        self.plan = plan
        self.ev = plan.ev
        self.ev_t = plan.ev_t
        self.end_t = plan.end_t
        self.row_t = plan.row_t
        self.marks = plan.marks
        self.total_rows = plan.total_rows

    # ---- GUI-Thread
    def set_plan(self, plan):
        self.pending = plan

    def inject(self, tr, semi):
        self.inbox.append((tr, semi))

    def estimate_pos(self):
        """Geschaetzte Synthese-Position jetzt (Samples), interpoliert zwischen Bloecken."""
        pos, t = self.clock
        return pos + (time.perf_counter() - t) * SR

    # ---- Audio-Thread
    def _command(self, tr, eng, code, val):
        if code == CODE_PAN:
            if self.mixer is not None:
                pan = max(-1.0, min(1.0, (val - 50) / 50.0))
                self.mixer.set_pan(eng, pan)
                self.pan_touched.add(tr)
            return
        i = TOGGLES.get(code)
        if i is not None:
            fx = eng.chain[i]
            self.fx_en_orig.setdefault((tr, i), fx.enabled)
            fx.enabled = val > 0
            return
        p = PARAMS.get(code)
        if p is None:
            return
        i, attr, lo, hi, _label = p
        fx = eng.chain[i]
        key = (tr, i, attr)
        cur = float(getattr(fx, attr))
        self.fx_orig.setdefault(key, cur)
        target = lo + (val / 99.0) * (hi - lo)
        if attr in DISCRETE:
            self.glides.pop(key, None)
            setattr(fx, attr, target)
        else:
            self.glides[key] = [fx, attr, cur, target, 0]

    def _fire(self, e):
        _t, kind, tr, a, b = e
        if kind == CLICK:
            if self.metro or tr:        # tr=1: Vorzaehler (immer hoerbar)
                self.click_wav = CLICK_ACC if a else CLICK_STD
                self.click_pos = 0
            return
        eng = self.engines[tr]
        if eng is None:
            return
        if kind == CMD:
            self._command(tr, eng, a, b)
            return
        semi = a
        key = ("seq", tr, semi)
        if kind == NOTE_ON:
            if b is not None:
                self.vel_state[tr] = b
            if self.muted[tr]:
                return
            eng.note_on(key, semi, vel_gain(self.vel_state[tr]))
            self.active.add((tr, semi))
        else:
            eng.note_off(key)
            self.active.discard((tr, semi))

    def _release(self, keys):
        for tr, semi in list(keys):
            eng = self.engines[tr]
            if eng is not None:
                eng.note_off(("seq", tr, semi))
            self.active.discard((tr, semi))

    def _restore(self):
        """Pan, Effekt-Schalter und -Parameter nach Befehlen auf die Ausgangswerte setzen."""
        m = self.mixer
        if m is not None:
            for tr in list(self.pan_touched):
                eng = self.engines[tr]
                if eng is not None:
                    m.set_pan(eng, self.base_pan[tr])
        self.pan_touched.clear()
        self.glides.clear()
        for (tr, i, attr), v in self.fx_orig.items():
            eng = self.engines[tr]
            if eng is not None:
                setattr(eng.chain[i], attr, v)
        for (tr, i), v in self.fx_en_orig.items():
            eng = self.engines[tr]
            if eng is not None:
                eng.chain[i].enabled = v
        self.fx_orig.clear()
        self.fx_en_orig.clear()

    def _drain(self):
        while self.inbox:
            tr, semi = self.inbox.pop(0)
            eng = self.engines[tr]
            if eng is not None and not self.muted[tr]:
                eng.note_on(("seq", tr, semi), semi, vel_gain(self.vel_state[tr]))
                self.active.add((tr, semi))

    def _swap(self):
        plan, self.pending = self.pending, None
        if plan is None:
            return
        keep = {(tr, semi) for a, b, tr, semi in plan.notes_t
                if a - SWAP_MARGIN <= self.pos < b}
        self._release(self.active - keep)   # nur Noten, die es nicht mehr gibt
        self._adopt(plan)
        self.idx = bisect.bisect_left(self.ev_t, self.pos)

    def prepare(self, n):
        if self.stop_flag:
            self._release(self.active)
            self._restore()
            self.done = True
            return n
        if self.inbox:
            self._drain()
        if self.pending is not None:
            self._swap()
        while True:
            ev = self.ev
            while self.idx < len(ev) and ev[self.idx][0] <= self.pos:
                self._fire(ev[self.idx])
                self.idx += 1
            if self.pos >= self.end_t:
                self._release(self.active)
                if self.loop and self.end_t > 0:
                    self.pos = 0
                    self.idx = bisect.bisect_left(self.ev_t, 0)   # ohne Vorzaehler
                    self.loops += 1
                    continue
                self._restore()
                self.done = True
                return n
            break

        limit = self.end_t - self.pos
        if self.idx < len(self.ev):
            limit = min(limit, self.ev[self.idx][0] - self.pos)
        if self.glides:
            limit = min(limit, GLIDE_SEG)
        return max(1, min(n, limit))

    def click_samples(self, n):
        """Metronom-Klick fuer das aktuelle Segment (Laenge n) oder None."""
        wav = self.click_wav
        if wav is None:
            return None
        p = self.click_pos
        seg = wav[p:p + n]
        out = np.zeros(n, np.float32)
        out[:len(seg)] = seg
        self.click_pos = p + n
        if self.click_pos >= len(wav):
            self.click_wav = None
        return out

    def advance(self, n):
        self.pos += n
        self.clock = (self.pos, time.perf_counter())
        if self.glides:
            self._step_glides(n)

    def _step_glides(self, n):
        for key, g in list(self.glides.items()):
            fx, attr, a, b, t = g
            t += n
            if t >= GLIDE_N:
                setattr(fx, attr, b)
                del self.glides[key]
            else:
                g[4] = t
                setattr(fx, attr, a + (b - a) * t / GLIDE_N)

    def fail(self, exc):
        self.error = repr(exc)
        self.done = True
        try:
            self._release(self.active)
            self._restore()
        except Exception:
            pass

    # ---- GUI-Thread (nur lesen)
    def where(self):
        """(order_pos, pattern_idx, row_in_pattern, rows_in_pattern) fuer die Anzeige."""
        marks, row_t = self.marks, self.row_t
        total = max(1, self.total_rows)
        row = max(0, min(total - 1, bisect.bisect_right(row_t, self.pos) - 1))
        m = marks[0]
        for mk in marks:
            if mk[0] <= row:
                m = mk
            else:
                break
        start, opos, pi, rows = m
        return opos, pi, row - start, rows

def compile_song(song, engines, spr, mode, cur, loop, count_in=0):
    plan = build_plan(song, engines, spr, mode, cur, count_in)
    muted = [bool(t["mute"]) for t in song["tracks"]]
    return Player(plan, list(engines), muted, loop)
