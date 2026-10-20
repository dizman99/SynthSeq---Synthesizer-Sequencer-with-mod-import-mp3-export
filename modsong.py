# -*- coding: utf-8 -*-
"""modsong.py - MOD-Import Job 4: Song (synth-song) aus einem MOD erzeugen.

Braucht modimport.py (Parser, WAV-/Patch-/Bank-Export) im selben Ordner.
Aufruf im Projektordner:
  python modsong.py datei.mod [-o log.txt]
fuehrt den kompletten Import aus: Bericht, WAVs, Patches, Soundbank und Song (SEQUENCER/<Mod>.json).

Regeln (Stand der Abstimmung):
- eine Spur je Instrument mit Patch (>= MIN_NOTES Noten); Zusatzspur nur bei Ueberlappung, nur mit
  >= MIN_NOTES Noten und nur solange Platz ist; ueberlappende Noten ohne Spur fallen weg
- Lautstaerke je Note explizit: vel = Cxx bzw. Sample-Standard * 99 / 64
- Notenende = naechstes Ereignis desselben MOD-Kanals (Note oder Cxx 00), bei One-Shot-Samples hoechstens
  die Sample-Dauer, immer hoechstens das Patternende (so wie der Editor es kennt)
- Tempo: festes BPM aus der mittleren Zeilendauer des MOD (4 Zeilen pro Beat); Pan je Spur klassisch L R R L
- nicht abgebildet (nur im Log gezaehlt): alle Effekte ausser Cxx/Fxx, Dxy, Bxx
"""
import json
import math
import os
import shutil
import sys
from collections import Counter, defaultdict

from modimport import (BASE_RATE, FINETUNE_DIV, MIN_NOTES, OVERLAP_TOL, PAL_C2, allocate_by_instrument,
                       bank_entry_name, has_loop, rel_to_base)

SONG_FORMAT = "synth-song"
SONG_VERSION = 1
PATTERN_ROWS = 64
ROWS_PER_BEAT = 4
TRACK_OCTAVE = 3        # wie die Bank-Eintraege: Taste Q = C4 = Originaltonhoehe
PAN_SIDE = 50           # Spur-Pan fuer links/rechts (-100..100)
BPM_MIN, BPM_MAX = 20, 300


def _max_tracks():
    try:
        from config import NUM_TRACKS
        return NUM_TRACKS
    except ImportError:
        return 32


def collect(mod):
    """Linearer Durchlauf der Order-Liste. Rueckgabe (noten, bpm, nicht_abgebildete_effekte, hinweise).
    noten: (start_row, end_row, kanal, instrument, semi, vol 0..64, pos, zeile) - globale Zeilen,
    Ende exklusiv und hoechstens das Patternende."""
    nch = mod["channels"]
    smp = {s["nr"]: s for s in mod["samples"]}
    order = mod["order"][:mod["songlen"]]
    st = Counter()      # nicht abgebildete Effekte
    inf = Counter()     # Hinweise zu Noten
    speed, tempo, t = 6, 125, 0.0
    cur = [0] * nch
    ev = [[] for _ in range(nch)]
    for pos, pi in enumerate(order):
        for r, row in enumerate(mod["patterns"][pi]):
            g = pos * PATTERN_ROWS + r
            for (_i, _p, e, prm) in row:
                if e == 0xF and prm:
                    if prm < 0x20:
                        speed = prm
                    else:
                        tempo = prm
            for c, (inst, per, e, prm) in enumerate(row):
                if inst:
                    cur[c] = inst
                if per and cur[c]:
                    s = smp.get(cur[c])
                    vol = s["vol"] if s else 64
                    if e == 0xC:
                        vol = min(prm, 64)
                    ev[c].append((g, "on", cur[c], per, vol, pos, r))
                elif e == 0xC:
                    if prm == 0:
                        ev[c].append((g, "cut", 0, 0, 0, pos, r))
                    else:
                        st["Cxx ohne Note (Lautstaerke mitten in der Note)"] += 1
                if e == 0xD:
                    st["Dxy Pattern Break (nicht ausgewertet)"] += 1
                elif e == 0xB:
                    st["Bxx Position Jump (nicht ausgewertet)"] += 1
                elif e not in (0xC, 0xF) and (e or prm):
                    st["Effekt %Xxx nicht abgebildet" % e if e != 0xE else "Exy nicht abgebildet"] += 1
            t += speed * 2.5 / tempo
    total_rows = len(order) * PATTERN_ROWS
    bpm = int(round(15.0 * total_rows / t)) if t > 0 else 125
    bpm = max(BPM_MIN, min(BPM_MAX, bpm))
    row_sec = 15.0 / bpm
    notes = []
    for c in range(nch):
        evs = ev[c]
        for k, (g, kind, inst, per, vol, pos, r) in enumerate(evs):
            if kind != "on":
                continue
            if vol <= 0:
                inf["stumme Noten (Cxx 00 beim Anschlag)"] += 1
                continue
            nxt = evs[k + 1][0] if k + 1 < len(evs) else total_rows
            s = smp.get(inst)
            semi = int(round(12 * math.log2(PAL_C2 / per)))
            natural = nxt
            if s is not None and not has_loop(s):
                dur = s["len_w"] * 2 / (BASE_RATE * 2 ** (s["finetune"] / FINETUNE_DIV) * 2 ** (semi / 12.0))
                natural = min(nxt, g + max(1, int(math.ceil(dur / row_sec))))
            pat_end = (pos + 1) * PATTERN_ROWS
            if natural > pat_end:
                inf["Noten am Patternende gekuerzt (Ausklang faellt weg)"] += 1
            notes.append((g, max(g + 1, min(natural, pat_end)), c, inst, semi, vol, pos, r))
    return notes, bpm, st, inf


def build_song(mod, notes, bpm, patches, name, max_tracks):
    smp = {s["nr"]: s for s in mod["samples"]}
    row_sec = 15.0 / bpm
    usable = [n for n in notes if n[3] in patches]
    dropped = Counter()
    dropped["Noten ohne Patch (Instrument zu selten)"] = len(notes) - len(usable)
    al = allocate_by_instrument(usable, OVERLAP_TOL / row_sec)
    mains = [(i, 0, al[i][0]) for i in sorted(al)]
    if len(mains) > max_tracks:
        mains.sort(key=lambda m: -len(m[2]))
        for m in mains[max_tracks:]:
            dropped["Noten von Instrumenten ohne freie Spur"] += len(m[2])
        mains = sorted(mains[:max_tracks])
    extras = [(i, k, tr) for i in sorted(al) for k, tr in enumerate(al[i]) if k > 0]
    extras.sort(key=lambda x: -len(x[2]))
    room = max_tracks - len(mains)
    chosen = [x for x in extras if len(x[2]) >= MIN_NOTES][:max(0, room)]
    for x in extras:
        if x not in chosen:
            dropped["Noten ueberlappend, ohne Zusatzspur (frueher begonnene Note gewinnt)"] += len(x[2])
    main_inst = {m[0] for m in mains}
    spuren = sorted([m for m in mains] + [x for x in chosen if x[0] in main_inst], key=lambda x: (x[0], x[1]))
    track_of = {}
    tracks = []
    for idx, (inst, k, tr_notes) in enumerate(spuren):
        for n in tr_notes:
            track_of[(n[0], n[2])] = idx
        chans = Counter(n[2] for n in tr_notes)
        ch = chans.most_common(1)[0][0]
        pan = -PAN_SIDE if ch % 4 in (0, 3) else PAN_SIDE
        nm = bank_entry_name(inst, smp[inst]["name"]) if inst in smp else "%02d" % inst
        if k:
            nm += " #%d" % (k + 1)
        tracks.append({"name": nm, "patch": rel_to_base(patches[inst]), "octave": TRACK_OCTAVE,
                       "mute": False, "pan": pan})
    while len(tracks) < max_tracks:
        tracks.append({"name": "", "patch": None, "octave": 2, "mute": False, "pan": 0})

    order = mod["order"][:mod["songlen"]]
    by_pos = defaultdict(list)
    seen = set()
    for n in sorted(usable, key=lambda x: (x[0], x[2])):
        ti = track_of.get((n[0], n[2]))
        if ti is None:
            continue
        if (n[6], ti, n[7]) in seen:      # zwei Kanaele schlagen dasselbe Instrument in derselben Zeile an
            dropped["Noten gleichzeitig auf derselben Spur (Kanal mit kleinerer Nummer gewinnt)"] += 1
            continue
        seen.add((n[6], ti, n[7]))
        vel = max(1, min(99, int(round(n[5] * 99 / 64.0))))
        by_pos[n[6]].append([n[7], ti, n[4], max(1, n[1] - n[0]), vel])
    patterns, sigs, out_order = [], {}, []
    used_names = Counter()
    for pos, pi in enumerate(order):
        pn = sorted(by_pos.get(pos, []))
        last = {}                         # wie der Editor: eine neue Note beendet die vorherige derselben Spur
        for x in pn:
            prev = last.get(x[1])
            if prev is not None and prev[0] + prev[3] > x[0]:
                prev[3] = x[0] - prev[0]
            last[x[1]] = x
        sig = (pi, tuple(tuple(x) for x in pn))
        if sig not in sigs:
            used_names[pi] += 1
            nm = "P%02d" % pi if used_names[pi] == 1 else "P%02d_%d" % (pi, used_names[pi])
            sigs[sig] = len(patterns)
            patterns.append({"name": nm, "rows": PATTERN_ROWS, "notes": pn, "cmds": []})
        out_order.append(sigs[sig])
    song = {"format": SONG_FORMAT, "version": SONG_VERSION, "name": name, "bpm": bpm,
            "rows_per_beat": ROWS_PER_BEAT, "tracks": tracks, "patterns": patterns, "order": out_order}
    return song, spuren, dropped


def export_song(mod, res, seq_dir=None, max_tracks=None):
    """Schreibt SEQUENCER/<Prefix>.json. res = Rueckgabe von modimport.export_instruments."""
    if seq_dir is None:
        try:
            from config import sub_dir
        except ImportError:
            from config import subdir as sub_dir
        seq_dir = sub_dir("SEQUENCER")
    max_tracks = max_tracks or _max_tracks()
    log = []
    patches = res["patches"]
    if not patches:
        return ["Kein Patch vorhanden - kein Song erzeugt"], None
    notes, bpm, st, inf = collect(mod)
    song, spuren, dropped = build_song(mod, notes, bpm, patches, res["prefix"], max_tracks)
    os.makedirs(seq_dir, exist_ok=True)
    path = os.path.join(seq_dir, res["prefix"] + ".json")
    if os.path.isfile(path):
        shutil.copy2(path, path + ".bak")
        log.append("Song existierte schon: ueberschrieben, alte Datei als .bak gesichert")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(song, f, indent=2, ensure_ascii=False)
    log.append("Song: %s" % path)
    log.append("BPM %d, %d Zeilen/Beat, %d Positionen, %d Patterns, %d Spuren belegt" % (
        bpm, ROWS_PER_BEAT, len(song["order"]), len(song["patterns"]), len(spuren)))
    for i, (inst, k, trn) in enumerate(spuren):
        t = song["tracks"][i]
        log.append("  Spur %2d: %-26s Pan %+4d  %5d Noten" % (i + 1, t["name"][:26], t["pan"], len(trn)))
    kept = sum(len(trn) for _, _, trn in spuren) - dropped.get(
        "Noten gleichzeitig auf derselben Spur (Kanal mit kleinerer Nummer gewinnt)", 0)
    log.append("Noten im MOD (mit Lautstaerke > 0): %d, uebernommen (alle Vorkommen im Song gezaehlt): %d" % (
        len(notes), kept))
    for k, v in sorted(dropped.items()):
        if v:
            log.append("verworfen: %s: %d" % (k, v))
    for k, v in sorted(inf.items()):
        log.append("Hinweis: %s: %d" % (k, v))
    for k, v in sorted(st.items()):
        log.append("nicht abgebildet: %s: %d" % (k, v))
    return log, path


def run_import(mod_path, log_path=None):
    """Kompletter Import (fuer Kommandozeile und spaeteren Button). Rueckgabe (Text, Song-Pfad)."""
    import modimport as mi
    with open(mod_path, "rb") as f:
        data = f.read()
    mod = mi.parse_mod(data)
    an = mi.analyse(mod)
    txt = mi.report(mod, an, os.path.basename(mod_path))
    noten, info = mi.timeline(mod)
    txt += "\n" + mi.report_tracks(mod, noten, info)
    res = mi.export_instruments(mod, noten, mod_path)
    txt += "\n\nEXPORT (Job 2/3)\n" + "-" * 60 + "\nPrefix: %s\n" % res["prefix"] + "\n".join(res["log"])
    log, song_path = export_song(mod, res)
    txt += "\n\nSONG (Job 4)\n" + "-" * 60 + "\n" + "\n".join(log)
    if log_path:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    return txt, song_path


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    log_path = argv[argv.index("-o") + 1] if "-o" in argv else None
    txt, _ = run_import(argv[1], log_path)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
