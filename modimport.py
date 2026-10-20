# -*- coding: utf-8 -*-
"""modimport.py - MOD-Import. Job 1/1b: Parser, Bericht, Spurverteilung. Job 2: WAVs und Patches. Job 3: Soundbank.

Aufruf im Projektordner (neben config.py, audio_utils.py, patch.py):
  python modimport.py datei.mod                 Bericht (liest nur)
  python modimport.py datei.mod -o log.txt      Bericht zusaetzlich in Datei
  python modimport.py datei.mod --export        zusaetzlich WAVs nach WAV/<Mod>/, Patches nach PATCHES/<Mod>/,
                                                Bank nach SOUNDBANK/<Mod>.json
"""
import os
import re
import shutil
import struct
import sys
import wave
from collections import Counter, defaultdict

PAL_C2 = 428
OVERLAP_TOL = 0.10      # Sekunden: kuerzere Ueberlappung zaehlt nicht als Konflikt
BASE_RATE = 8363.0      # Hz bei Period 428 (NTSC); C-2 = C4 (Semi 0)

# Job 2
MIN_NOTES = 5           # Instrumente mit weniger Noten bekommen keinen Patch
PATCH_VOLUME = 0.3
ENVELOPE = {"attack": 0.002, "decay": 0.002, "sustain": 1.0, "release": 0.005}
LOOP_TAIL = 256         # Samples Loop-Fortsetzung hinter dem Loop-Ende (0 = aus), siehe sample_pcm
FINETUNE_DIV = 96.0     # +-8 Finetune = +-1 Halbton
FX_NAMES = ["Vocoder", "Filter", "Offset-LFO", "Overdrive", "Fuzz", "Distortion", "Bitcrusher",
            "Flanger", "Chorus", "Phaser", "Tremolo", "Echo", "Reverb"]

# Job 3
BANK_FORMAT = "synth-bank"
BANK_VERSION = 1
BANK_OCTAVE = 3         # Oktave 3 = Taste Q spielt C4 (Root des Imports)

EFFECT_NAMES = {
    0x0: "0xy Arpeggio", 0x1: "1xx Portamento hoch", 0x2: "2xx Portamento runter",
    0x3: "3xx Tone-Portamento", 0x4: "4xy Vibrato", 0x5: "5xy Tone-Porta+VolSlide",
    0x6: "6xy Vibrato+VolSlide", 0x7: "7xy Tremolo", 0x8: "8xx Panning",
    0x9: "9xx Sample-Offset", 0xA: "Axy Volume-Slide", 0xB: "Bxx Position Jump",
    0xC: "Cxx Set Volume", 0xD: "Dxy Pattern Break", 0xE: "Exy Erweiterter Befehl",
    0xF: "Fxx Speed/Tempo",
}
E_NAMES = {
    0x0: "E0x Filter", 0x1: "E1x Feinporta hoch", 0x2: "E2x Feinporta runter",
    0x3: "E3x Glissando", 0x4: "E4x Vibrato-Wellenform", 0x5: "E5x Finetune",
    0x6: "E6x Pattern-Loop", 0x7: "E7x Tremolo-Wellenform", 0x8: "E8x Pan (grob)",
    0x9: "E9x Retrigger", 0xA: "EAx Fein-VolSlide hoch", 0xB: "EBx Fein-VolSlide runter",
    0xC: "ECx Note Cut", 0xD: "EDx Note Delay", 0xE: "EEx Pattern Delay", 0xF: "EFx Invert Loop",
}
MAPPED = {0xC, 0xF}   # vom Import abgebildet (Cxx Lautstaerke/Ende, Fxx Tempo)


class ModError(Exception):
    pass


def detect_format(data):
    notes = []
    if len(data) >= 1084:
        tag = data[1080:1084].decode("latin-1")
        if tag in ("M.K.", "M!K!", "FLT4", "4CHN"):
            return tag, 4, 31, 1084, notes
        if tag == "FLT8":
            notes.append("FLT8: Patterns liegen paarweise (4+4 Kanaele); wird als 8 Kanaele gelesen - ungeprueft")
            return tag, 8, 31, 1084, notes
        if tag in ("CD81", "OCTA", "OKTA"):
            return tag, 8, 31, 1084, notes
        if len(tag) == 4 and tag[0].isdigit() and tag[1:] == "CHN":
            return tag, int(tag[0]), 31, 1084, notes
        if len(tag) == 4 and tag[:2].isdigit() and tag[2:] in ("CH", "CN"):
            return tag, int(tag[:2]), 31, 1084, notes
    notes.append("Kein bekanntes Format-Tag bei Offset 1080: lese als alte 15-Sample-Variante (4 Kanaele, Header 600 Bytes)")
    return "", 4, 15, 600, notes


def parse_mod(data):
    if len(data) < 600:
        raise ModError("Datei zu kurz fuer ein MOD (%d Bytes)" % len(data))
    tag, nch, nsmp, hlen, notes = detect_format(data)
    title = data[0:20].split(b"\0")[0].decode("latin-1").rstrip()
    pos = 20
    samples = []
    for i in range(nsmp):
        h = data[pos:pos + 30]
        pos += 30
        name = h[0:22].split(b"\0")[0].decode("latin-1").rstrip()
        length = struct.unpack(">H", h[22:24])[0]
        ft = h[24] & 0x0F
        if ft > 7:
            ft -= 16
        vol = h[25]
        ls = struct.unpack(">H", h[26:28])[0]
        ll = struct.unpack(">H", h[28:30])[0]
        ls_words = ls // 2 if nsmp == 15 else ls   # alte Variante: Loop-Start in Bytes
        samples.append({"nr": i + 1, "name": name, "len_w": length, "finetune": ft, "vol": min(vol, 64),
                        "vol_raw": vol, "loop_start_w": ls_words, "loop_len_w": ll, "data": b""})
    songlen = data[pos]
    restart = data[pos + 1]
    order = list(data[pos + 2:pos + 2 + 128])
    n_pat = max(order) + 1
    n_pat_play = (max(order[:songlen]) + 1) if songlen else 0
    pat_bytes = 64 * nch * 4
    p0 = hlen
    need = p0 + n_pat * pat_bytes
    patterns = []
    if need > len(data):
        notes.append("Datei endet in den Patterns (%d Bytes fehlen)" % (need - len(data)))
    for p in range(n_pat):
        rows = []
        base = p0 + p * pat_bytes
        for r in range(64):
            row = []
            for c in range(nch):
                o = base + (r * nch + c) * 4
                b = data[o:o + 4]
                if len(b) < 4:
                    row.append((0, 0, 0, 0))
                    continue
                inst = (b[0] & 0xF0) | (b[2] >> 4)
                per = ((b[0] & 0x0F) << 8) | b[1]
                row.append((inst, per, b[2] & 0x0F, b[3]))
            rows.append(row)
        patterns.append(rows)
    sp = max(need, p0)
    for s in samples:
        nbytes = s["len_w"] * 2
        chunk = data[sp:sp + nbytes]
        if len(chunk) < nbytes:
            notes.append("Sample %02d '%s': Daten abgeschnitten (%d von %d Bytes)" % (s["nr"], s["name"], len(chunk), nbytes))
        s["data"] = chunk
        sp += nbytes
    extra = len(data) - sp
    if extra > 0:
        notes.append("%d Bytes hinter den Sampledaten (ungenutzt)" % extra)
    return {"title": title, "tag": tag, "channels": nch, "samples": samples, "songlen": songlen,
            "restart": restart, "order": order, "n_pat": n_pat, "n_pat_play": n_pat_play,
            "patterns": patterns, "notes": notes, "size": len(data)}


def has_loop(s):
    return s["loop_len_w"] > 1


def analyse(mod):
    nch = mod["channels"]
    fx, efx = Counter(), Counter()
    params = defaultdict(Counter)
    notes_ch = [0] * nch
    inst_ch = [Counter() for _ in range(nch)]
    cur = [0] * nch
    bad_per = 0
    per_min, per_max = 9999, 0
    used = set()
    tempo_ev, speed_ev, breaks, jumps = [], [], [], []
    for pos, pi in enumerate(mod["order"][:mod["songlen"]]):
        for r, row in enumerate(mod["patterns"][pi]):
            for c, (inst, per, e, prm) in enumerate(row):
                if inst:
                    cur[c] = inst
                if per:
                    notes_ch[c] += 1
                    inst_ch[c][cur[c]] += 1
                    used.add(cur[c])
                    per_min, per_max = min(per_min, per), max(per_max, per)
                    if not (113 <= per <= 856):
                        bad_per += 1
                if e or prm:
                    if e == 0xE:
                        efx[E_NAMES[prm >> 4]] += 1
                        params["E" + format(prm >> 4, "X")][prm & 0xF] += 1
                        fx[EFFECT_NAMES[0xE]] += 1
                    else:
                        fx[EFFECT_NAMES[e]] += 1
                        params[format(e, "X")][prm] += 1
                    if e == 0xF:
                        (speed_ev if prm < 0x20 else tempo_ev).append((pos, r, prm))
                    elif e == 0xD:
                        breaks.append((pos, r, prm))
                    elif e == 0xB:
                        jumps.append((pos, r, prm))
    combos = {(c, i) for c in range(nch) for i in inst_ch[c]}
    return {"fx": fx, "efx": efx, "params": params, "notes_ch": notes_ch, "inst_ch": inst_ch,
            "bad_per": bad_per, "per_range": (per_min, per_max), "used": used, "combos": combos,
            "tempo_ev": tempo_ev, "speed_ev": speed_ev, "breaks": breaks, "jumps": jumps}


def fmt_params(cnt, top=6):
    return ", ".join("%02X x%d" % (p, n) for p, n in cnt.most_common(top))


def report(mod, an, filename="", max_tracks=16):
    L = []
    a = L.append
    nch = mod["channels"]
    a("MOD-IMPORTBERICHT")
    a("=" * 60)
    a("Datei      : %s (%d Bytes)" % (filename, mod["size"]))
    a("Titel      : %s" % (mod["title"] or "(leer)"))
    a("Format-Tag : %s   Kanaele: %d   Samples im Header: %d" % (mod["tag"] or "(keins)", nch, len(mod["samples"])))
    a("Songlaenge : %d Positionen, Restart %d" % (mod["songlen"], mod["restart"]))
    a("Patterns   : %d in Order-Liste insgesamt, %d in den ersten %d Positionen gespielt" %
      (mod["n_pat"], mod["n_pat_play"], mod["songlen"]))
    if nch > 8:
        a("WARNUNG: %d Kanaele - Import ist bis 8 Kanaele vorgesehen" % nch)
    for n in mod["notes"]:
        a("HINWEIS: " + n)
    a("")
    a("SAMPLES  (Nr, Name, Bytes, Finetune, Vol, Loop, benutzt)")
    a("-" * 60)
    for s in mod["samples"]:
        nb = s["len_w"] * 2
        if nb == 0 and not s["name"]:
            continue
        lp = "Loop %d+%d W" % (s["loop_start_w"], s["loop_len_w"]) if has_loop(s) else "-"
        a("%02d  %-22s %6d  ft %+d  vol %2d  %-16s %s" % (s["nr"], s["name"][:22], nb, s["finetune"], s["vol"], lp,
          "ja" if s["nr"] in an["used"] else "NEIN"))
        if s["vol_raw"] > 64:
            a("    HINWEIS: Lautstaerke %d > 64, begrenzt" % s["vol_raw"])
        if has_loop(s) and (s["loop_start_w"] + s["loop_len_w"]) > s["len_w"]:
            a("    HINWEIS: Loop reicht ueber das Sample-Ende hinaus")
        if nb == 0 and s["nr"] in an["used"]:
            a("    WARNUNG: leeres Sample wird gespielt")
    a("")
    a("ORDER (Pattern je Position): " + " ".join("%02d" % p for p in mod["order"][:mod["songlen"]]))
    a("")
    a("NOTEN UND INSTRUMENTE JE KANAL (gespielte Reihenfolge)")
    a("-" * 60)
    for c in range(nch):
        ic = an["inst_ch"][c]
        a("Kanal %d: %4d Noten, %d Instrument(e): %s" % (c + 1, an["notes_ch"][c], len(ic),
          ", ".join("%02d x%d" % (i, n) for i, n in sorted(ic.items())) or "-"))
    n_comb = len(an["combos"])
    a("")
    a("Kanal/Instrument-Kombinationen: %d  (Spuren verfuegbar: %d) -> %s" %
      (n_comb, max_tracks, "passt" if n_comb <= max_tracks else "UEBERLAUF um %d" % (n_comb - max_tracks)))
    if an["per_range"][1]:
        a("Period-Bereich: %d..%d, ausserhalb 113..856: %d Noten" % (an["per_range"][0], an["per_range"][1], an["bad_per"]))
    a("")
    a("EFFEKTE (Anzahl)  [* = wird vom Import abgebildet]")
    a("-" * 60)
    if not an["fx"]:
        a("keine")
    for k, v in sorted(an["fx"].items()):
        a("%s %-28s %5d" % ("*" if int(k[0], 16) in MAPPED else " ", k, v))
    if an["efx"]:
        a("  davon E-Befehle:")
        for k, v in sorted(an["efx"].items()):
            a("      %-28s %5d" % (k, v))
    a("")
    a("PARAMETER-STATISTIK (haeufigste Werte, hex)")
    a("-" * 60)
    for k in sorted(an["params"]):
        cnt = an["params"][k]
        a("%-3s (%d versch.): %s" % (k, len(cnt), fmt_params(cnt)))
    a("")
    a("TEMPO/SPEED")
    a("-" * 60)
    a("Fxx Speed (<20h): %d Ereignisse %s" % (len(an["speed_ev"]), sorted(set(p for _, _, p in an["speed_ev"]))[:12]))
    a("Fxx Tempo (>=20h): %d Ereignisse %s" % (len(an["tempo_ev"]), sorted(set(p for _, _, p in an["tempo_ev"]))[:12]))
    if len(set(p for _, _, p in an["speed_ev"])) > 1 or len(set(p for _, _, p in an["tempo_ev"])) > 1:
        a("HINWEIS: Tempo/Speed wechselt im Song - nur ein Wert pro Song abbildbar")
    a("Dxy Pattern Break: %d   Bxx Position Jump: %d" % (len(an["breaks"]), len(an["jumps"])))
    for pos, r, p in an["breaks"][:5]:
        a("   D bei Pos %d Zeile %d: Ziel-Zeile %d" % (pos, r, (p >> 4) * 10 + (p & 15)))
    for pos, r, p in an["jumps"][:5]:
        a("   B bei Pos %d Zeile %d: Ziel-Pos %d" % (pos, r, p))
    return "\n".join(L)


# ---------------------------------------------------------------- Job 1b
def timeline(mod):
    """Linearer Durchlauf der Order-Liste (Dxy/Bxx noch nicht ausgewertet).
    noten: (start_s, ende_s, kanal, instrument, period, vol 0..64, pos, zeile)"""
    nch = mod["channels"]
    smp = {s["nr"]: s for s in mod["samples"]}
    speed, bpm = 6, 125
    t = 0.0
    cur_inst = [0] * nch
    open_note = [None] * nch
    noten = []
    speed_changes = []
    speed0 = 0

    def close(c):
        k = open_note[c]
        if k is not None:
            n = noten[k]
            noten[k] = (n[0], t, n[2], n[3], n[4], n[5], n[6], n[7])
            open_note[c] = None

    for pos, pi in enumerate(mod["order"][:mod["songlen"]]):
        for r, row in enumerate(mod["patterns"][pi]):
            for (inst, per, e, prm) in row:
                if e == 0xF:
                    if prm == 0:
                        speed0 += 1
                    elif prm < 0x20:
                        if prm != speed:
                            speed_changes.append((pos, r, prm))
                        speed = prm
                    else:
                        bpm = prm
            for c, (inst, per, e, prm) in enumerate(row):
                if inst:
                    cur_inst[c] = inst
                if per and cur_inst[c]:
                    close(c)
                    s_obj = smp.get(cur_inst[c])
                    vol = s_obj["vol"] if s_obj else 64
                    if e == 0xC:
                        vol = min(prm, 64)
                    noten.append((t, None, c, cur_inst[c], per, vol, pos, r))
                    open_note[c] = len(noten) - 1
                elif e == 0xC and prm == 0:
                    close(c)
            t += speed * 2.5 / bpm
    total = t
    out = []
    for (s, e_, c, i, per, vol, pos, r) in noten:
        s_obj = smp.get(i)
        if e_ is None:
            e_ = total
        if s_obj is not None and not has_loop(s_obj) and per:
            e_ = min(e_, s + (s_obj["len_w"] * 2) / (BASE_RATE * PAL_C2 / per))
        out.append((s, e_, c, i, per, vol, pos, r))
    return out, {"dauer": total, "speed_changes": speed_changes, "speed0": speed0}


def allocate_by_instrument(noten, tol=OVERLAP_TOL):
    """Eine Spur je Instrument; weitere Spur nur bei Ueberlappung > tol."""
    by_inst = defaultdict(list)
    for n in sorted(noten, key=lambda x: x[0]):
        if n[5] > 0:
            by_inst[n[3]].append(n)
    res = {}
    for i, lst in by_inst.items():
        tracks = []
        for n in lst:
            for tr in tracks:
                if tr[-1][1] - tol <= n[0]:
                    tr.append(n)
                    break
            else:
                tracks.append([n])
        res[i] = tracks
    return res


def report_tracks(mod, noten, info, max_tracks=32):
    L = []
    a = L.append
    a("")
    a("SPURVERTEILUNG NACH INSTRUMENT")
    a("-" * 60)
    silent = sum(1 for n in noten if n[5] == 0)
    a("Spieldauer (linear, ohne Dxy/Bxx): %.1f s, Noten: %d (davon Lautstaerke 0: %d)" % (info["dauer"], len(noten), silent))
    for tol, label in ((0.0, "strikt"), (OVERLAP_TOL, "Toleranz %d ms" % round(OVERLAP_TOL * 1000))):
        al = allocate_by_instrument(noten, tol)
        n_tr = sum(len(v) for v in al.values())
        a("Spuren %-16s: %d (Instrumente: %d, Zusatzspuren: %d) -> %s" % (
            label, n_tr, len(al), n_tr - len(al),
            "passt in %d" % max_tracks if n_tr <= max_tracks else "UEBERLAUF um %d bei %d Spuren" % (n_tr - max_tracks, max_tracks)))
    al = allocate_by_instrument(noten, OVERLAP_TOL)
    a("")
    a("Instrument: Noten je Spur (Zusatzspur = Ueberlappung > %d ms)" % round(OVERLAP_TOL * 1000))
    rare = []
    for i in sorted(al):
        counts = [len(t) for t in al[i]]
        tot = sum(counts)
        nm = mod["samples"][i - 1]["name"][:22] if i <= len(mod["samples"]) else "?"
        a("  %02d  %-22s %5d  %s" % (i, nm, tot, " + ".join(str(x) for x in counts) if len(counts) > 1 else ""))
        if tot < MIN_NOTES:
            rare.append((i, tot))
    if rare:
        a("Instrumente unter %d Noten (bekommen keinen Patch): " % MIN_NOTES + ", ".join("%02d (%d)" % x for x in rare))
    a("")
    a("TIMING / SPEED")
    a("-" * 60)
    sc = info["speed_changes"]
    a("Speed-Wechsel: %d, F00 (Speed 0): %d" % (len(sc), info["speed0"]))
    if sc:
        a("Verlauf (Pos.Zeile=Speed), erste 40: " + " ".join("%d.%d=%d" % x for x in sc[:40]))
        w = defaultdict(int)
        speed, idx = 6, 0
        for pos in range(mod["songlen"]):
            for r in range(64):
                while idx < len(sc) and (sc[idx][0], sc[idx][1]) <= (pos, r):
                    speed = sc[idx][2]
                    idx += 1
                w[speed] += 1
        tot = sum(w.values())
        a("Zeilen je Speed: " + ", ".join("%d: %d" % (k, v) for k, v in sorted(w.items())))
        avg = sum(k * v for k, v in w.items()) / tot
        a("Zeitgewichteter Mittelwert: Speed %.2f -> aequivalent %.1f BPM bei 4 Zeilen/Beat" % (avg, 125 * 6 / avg))
    return "\n".join(L)


# ---------------------------------------------------------------- Job 2: WAVs und Patches
def safe_name(s, maxlen=24):
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:maxlen].rstrip("_")


def note_counts(noten):
    return Counter(n[3] for n in noten if n[5] > 0)


def sample_pcm(s):
    """8 Bit signed -> 16 Bit. Rueckgabe None (zu kurz) oder dict(pcm, loop, warn).
    Mit Loop: Daten hinter dem Loop-Ende werden verworfen (der Amiga spielt sie nie), dahinter
    folgen LOOP_TAIL Samples Fortsetzung des Loops, damit der Ausblendschluss von trim_silence
    hinter dem Loop-Ende landet. Loop-Punkte sind Bruchteile der geschriebenen Laenge."""
    import numpy as np
    raw = s["data"]
    n = len(raw)
    if n < 4:
        return None
    warn = []
    ls = le = None
    if has_loop(s):
        a = s["loop_start_w"] * 2
        b = min(a + s["loop_len_w"] * 2, n)
        if a < b and b - a >= 4:
            ls, le = a, b
        else:
            warn.append("Loop ungueltig, ohne Loop geschrieben")
    body = raw[:le] if ls is not None else raw
    if ls is not None and le < n:
        warn.append("%d Bytes hinter dem Loop-Ende verworfen" % (n - le))
    pcm = np.frombuffer(body, dtype=np.int8).astype(np.int16) * 256
    loop = None
    if ls is not None:
        if LOOP_TAIL > 0:
            idx = ls + (np.arange(LOOP_TAIL) % (le - ls))
            pcm = np.concatenate([pcm, pcm[idx]])
        total = len(pcm)
        loop = (ls / total, le / total)
    return {"pcm": pcm, "loop": loop, "warn": warn}


def trim_shift(pcm, loop):
    """Nur Messung, aendert nichts: um wie viele Samples verschiebt der Trim des Programms die
    Loop-Punkte gegenueber dem geschriebenen WAV? Rueckgabe (dLS, dLE) oder None."""
    import numpy as np
    try:
        from config import SR, TRIM_PREROLL_MS, TRIM_THRESHOLD
    except ImportError:
        return None
    x = pcm.astype("float32") / 32768.0
    idx = np.flatnonzero(np.abs(x) >= TRIM_THRESHOLD)
    if not idx.size:
        return None
    a = max(0, int(idx[0]) - int(TRIM_PREROLL_MS / 1000.0 * SR))
    b = int(idx[-1]) + 1
    n0, n1 = len(x), b - a
    ls0, le0 = int(round(loop[0] * n0)), int(round(loop[1] * n0))
    ls1, le1 = int(round(loop[0] * n1)), int(round(loop[1] * n1))
    return (ls1 + a) - ls0, (le1 + a) - le0


def write_wav_mono16(path, pcm, rate):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(pcm.astype("<i2").tobytes())


def wav_size(n_samples):
    return 44 + 2 * n_samples


def build_patch_dict(name, wav_abs, patch_abs, loop, patchlib, version):
    ref = patchlib.sample_reference(wav_abs, patch_abs)
    return {
        "format": "synth-patch", "version": version, "name": name,
        "source": {"type": "sample", "wave": "sine", "root": "C4", "loop": bool(loop),
                   "loop_start": loop[0] if loop else 0.0, "loop_end": loop[1] if loop else 1.0,
                   "loop_xfade": 0.0, "loop_snap": False, "sample": ref},
        "envelope": dict(ENVELOPE), "volume": PATCH_VOLUME, "unison": 1, "detune": 0.0,
        "normalize": False, "modulator": None,
        "effects": [{"type": n, "enabled": False, "params": {}} for n in FX_NAMES],
    }


# ---------------------------------------------------------------- Job 3: Soundbank
def bank_entry_name(nr, mod_name):
    """'NN Samplename' mit dem Originalnamen aus dem MOD; ohne brauchbaren Namen nur 'NN'."""
    nm = "".join(ch for ch in mod_name if ch.isprintable()).strip()
    if not re.search(r"[A-Za-z0-9]", nm):
        return "%02d" % nr
    return "%02d %s" % (nr, nm[:22].rstrip())


def write_bank_file(path, name, entries):
    """Schreibt eine Bank im Format von soundbank.write_bank (synth-bank, Version 1)."""
    import json
    d = {"format": BANK_FORMAT, "version": BANK_VERSION, "name": name,
         "patches": [{"name": e["name"], "file": e["file"], "octave": e["octave"],
                      "category": e["category"]} for e in entries]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def rel_to_base(path):
    """Pfad relativ zum Programmordner (wie soundbank.to_rel), sonst unveraendert."""
    try:
        from config import BASE_DIR
        return os.path.normpath(os.path.relpath(os.path.abspath(path), BASE_DIR))
    except (ImportError, ValueError):
        return os.path.normpath(path)


def export_instruments(mod, noten, filename, base_dirs=None, bank_dir=None):
    """Schreibt WAVs nach WAV/<Mod>/ (alle nicht leeren Samples), Patches nach PATCHES/<Mod>/
    (Instrumente mit >= MIN_NOTES Noten) und die Bank nach SOUNDBANK/<Mod>.json.
    base_dirs = (wav_dir, patch_dir), bank_dir = Bank-Ordner; None = config.sub_dir."""
    import patch as patchlib
    try:
        from config import PATCH_VERSION as version
    except ImportError:
        version = 1
    if base_dirs is None:
        try:
            from config import sub_dir as _sd
        except ImportError:
            from config import subdir as _sd
        base_dirs = (_sd("WAV"), _sd("PATCHES"))
        if bank_dir is None:
            bank_dir = _sd("SOUNDBANK")
    wav_root, pat_root = base_dirs
    log = []
    counts = note_counts(noten)
    keep = {i for i, c in counts.items() if c >= MIN_NOTES}
    modname = safe_name(os.path.splitext(os.path.basename(filename))[0]) or "mod"

    items = []
    for s in mod["samples"]:
        info = sample_pcm(s)
        if info is None:
            if s["len_w"] > 0:
                log.append("Sample %02d '%s': zu kurz, uebersprungen" % (s["nr"], s["name"]))
            continue
        info["rate"] = int(round(BASE_RATE * 2 ** (s["finetune"] / FINETUNE_DIV)))
        info["s"] = s
        items.append(info)
    if not items:
        log.append("Keine Samples mit Daten - nichts zu schreiben")
        return {"prefix": modname, "wavs": {}, "patches": {}, "keep": keep, "counts": counts, "log": log}

    def targets(prefix):
        wd, pd = os.path.join(wav_root, prefix), os.path.join(pat_root, prefix)
        out = []
        for it in items:
            s = it["s"]
            nm = safe_name(s["name"], 20)
            stem = "%s_%02d%s" % (prefix, s["nr"], "_" + nm if nm else "")
            out.append((it, stem, os.path.join(wd, stem + ".wav"),
                        os.path.join(pd, stem + ".json") if s["nr"] in keep else None))
        return out

    k = 1
    while True:
        prefix = modname if k == 1 else "%s_%d" % (modname, k)
        tg = targets(prefix)
        wav_state = []
        for it, stem, wav, pat in tg:
            if os.path.isfile(wav):
                wav_state.append(os.path.getsize(wav) == wav_size(len(it["pcm"])))
            else:
                wav_state.append(None)
        reimport = all(x is True for x in wav_state)
        conflict = any(x is False for x in wav_state) or \
            (not reimport and any(pat and os.path.isfile(pat) for _, _, _, pat in tg))
        if not conflict or k >= 99:
            break
        k += 1
    if k > 1:
        log.append("Namenskollision: Satz existiert schon mit anderen Dateien -> neuer Name '%s'" % prefix)
    if reimport:
        log.append("Erneuter Import erkannt (gleiche WAV-Groessen): vorhandene Dateien werden ueberschrieben, Patches und Bank mit .bak gesichert")
    os.makedirs(os.path.join(wav_root, prefix), exist_ok=True)
    os.makedirs(os.path.join(pat_root, prefix), exist_ok=True)

    res_w, res_p = {}, {}
    shifted = []
    for it, stem, wav, pat in tg:
        s = it["s"]
        write_wav_mono16(wav, it["pcm"], it["rate"])
        res_w[s["nr"]] = wav
        lp = "Loop %.4f..%.4f" % it["loop"] if it["loop"] else "kein Loop"
        line = "WAV %02d -> %s  (%d Samples, %d Hz, %s)" % (s["nr"], os.path.basename(wav), len(it["pcm"]), it["rate"], lp)
        for w in it["warn"]:
            line += "  [%s]" % w
        if it["loop"]:
            sh = trim_shift(it["pcm"], it["loop"])
            if sh is not None and (abs(sh[0]) > 2 or abs(sh[1]) > 2):
                line += "  [Trim verschiebt Loop um %+d/%+d Samples: im Generator pruefen]" % sh
                shifted.append(s["nr"])
        log.append(line)
        if pat:
            if os.path.isfile(pat):
                shutil.copy2(pat, pat + ".bak")
            d = build_patch_dict(stem, wav, pat, it["loop"], patchlib, version)
            patchlib.write_patch(pat, d)
            res_p[s["nr"]] = pat

    bank_path = None
    if bank_dir is not None and res_p:
        os.makedirs(bank_dir, exist_ok=True)
        entries = [{"name": bank_entry_name(nr, mod["samples"][nr - 1]["name"]),
                    "file": rel_to_base(res_p[nr]), "octave": BANK_OCTAVE, "category": prefix}
                   for nr in sorted(res_p)]
        bank_path = os.path.join(bank_dir, prefix + ".json")
        if os.path.isfile(bank_path):
            shutil.copy2(bank_path, bank_path + ".bak")
        write_bank_file(bank_path, prefix, entries)

    skipped = sorted((i, c) for i, c in counts.items() if i not in keep)
    log.append("WAVs: %d nach %s, Patches: %d nach %s (Instrumente mit >= %d Noten)" % (
        len(res_w), os.path.join(wav_root, prefix), len(res_p), os.path.join(pat_root, prefix), MIN_NOTES))
    if bank_path:
        log.append("Soundbank: %s (%d Patches, Kategorie '%s', Oktave %d)" % (bank_path, len(res_p), prefix, BANK_OCTAVE))
    if shifted:
        log.append("Loops, die der Trim verschiebt (von Hand pruefen): " + ", ".join("%02d" % i for i in shifted))
    if skipped:
        log.append("Ohne Patch (zu selten): " + ", ".join("%02d (%d Noten)" % x for x in skipped))
    unused = sorted(it["s"]["nr"] for it in items if it["s"]["nr"] not in counts)
    if unused:
        log.append("WAV ohne Noten im Song: " + ", ".join("%02d" % i for i in unused))
    return {"prefix": prefix, "wavs": res_w, "patches": res_p, "bank": bank_path,
            "keep": keep, "counts": counts, "log": log}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    with open(path, "rb") as f:
        data = f.read()
    mod = parse_mod(data)
    an = analyse(mod)
    txt = report(mod, an, os.path.basename(path))
    noten, info = timeline(mod)
    txt += "\n" + report_tracks(mod, noten, info)
    if "--export" in argv:
        res = export_instruments(mod, noten, path)
        txt += "\n\nEXPORT (Job 2/3)\n" + "-" * 60 + "\nPrefix: %s\n" % res["prefix"] + "\n".join(res["log"])
    print(txt)
    if "-o" in argv:
        with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
