# -*- coding: utf-8 -*-
"""loopcheck.py - Diagnose der Loop-Punkte importierter Patches (nur lesen, aendert nichts).

Im Projektordner (neben config.py, audio_utils.py, patch.py) ausfuehren:
  python loopcheck.py            alle Patches in PATCHES (inkl. Unterordner) mit Loop
  python loopcheck.py eth        nur Patches, deren Dateiname mit 'eth' beginnt
  python loopcheck.py eth -o loopcheck_eth.txt
Pro Loop-Patch: wie weit der Trim des Programms die Loop-Punkte gegenueber dem WAV verschiebt und
wie gross der Sprung am Loop-Uebergang ist (Knackser-Indikator).
"""
import glob
import os
import sys

import numpy as np

import patch as patchlib
from audio_utils import load_wav, trim_silence
from config import SR, TRIM_FADEOUT, TRIM_PREROLL_MS, TRIM_THRESHOLD, sub_dir


def trim_start(x):
    idx = np.flatnonzero(np.abs(x) >= TRIM_THRESHOLD)
    if idx.size == 0:
        return None, None
    return max(0, int(idx[0]) - int(TRIM_PREROLL_MS / 1000.0 * SR)), int(idx[-1]) + 1


def jump(x, ls, le):
    """Sprung beim Wrap (Sample le-1 -> ls) im Verhaeltnis zum typischen Schritt in der Schleife."""
    if le - ls < 4 or le > len(x):
        return 0.0, 0.0
    seg = x[ls:le]
    typ = float(np.median(np.abs(np.diff(seg)))) + 1e-9
    step = abs(float(x[ls]) - float(x[le - 1]))
    pk = float(np.max(np.abs(x))) + 1e-9
    return step / pk * 100.0, step / typ


def main(argv):
    prefix = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else ""
    lines = []
    lines.append("%-34s %6s %6s %6s %6s | %7s %7s | %5s %5s | %-9s %-10s %s" %
                 ("Patch", "N0", "cutA", "cutE", "N1", "ls_roh", "le_roh", "dLS", "dLE", "Sprung%", "Sprung/typ", "Befund"))
    n_bad = 0
    n_all = 0
    pattern = os.path.join(sub_dir("PATCHES"), "**", prefix + "*.json")
    for p in sorted(glob.glob(pattern, recursive=True)):
        try:
            d = patchlib.read_patch(p)
        except Exception as e:
            lines.append("%s: %s" % (os.path.basename(p), e))
            continue
        src = d.get("source") or {}
        if src.get("type") != "sample" or not src.get("loop"):
            continue
        wav = patchlib.resolve_sample(src.get("sample") or {}, p)
        if not wav:
            lines.append("%s: WAV nicht gefunden" % os.path.basename(p))
            continue
        x0, sr = load_wav(wav)
        a, b = trim_start(x0)
        if a is None:
            lines.append("%s: still" % os.path.basename(p))
            continue
        tr = trim_silence(x0)
        n0, n1 = len(x0), len(tr)
        fs, fe = float(src.get("loop_start", 0.0)), float(src.get("loop_end", 1.0))
        ls0, le0 = int(round(fs * n0)), int(round(fe * n0))       # gemeint (Bruchteile gegen WAV-Laenge)
        ls1, le1 = int(round(fs * n1)), int(round(fe * n1))       # was die Engine nimmt
        d_ls, d_le = (ls1 + a) - ls0, (le1 + a) - le0              # in WAV-Koordinaten
        j0 = jump(x0, ls0, le0)
        j1 = jump(tr, ls1, le1)
        befund = []
        if abs(d_ls) > 2 or abs(d_le) > 2:
            befund.append("VERSCHOBEN")
        if le1 > n1 - TRIM_FADEOUT:
            befund.append("Fade im Loop")
        if j1[1] > 6.0:
            befund.append("Sprung")
        n_all += 1
        n_bad += 1 if befund else 0
        lines.append("%-34s %6d %6d %6d %6d | %7d %7d | %+5d %+5d | %4.1f/%4.1f  %5.1f/%5.1f  %s" % (
            os.path.basename(p)[:-5][:34], n0, a, n0 - b, n1, ls0, le0, d_ls, d_le,
            j0[0], j1[0], j0[1], j1[1], " ".join(befund) or "ok"))
    lines.append("")
    lines.append("Loop-Patches: %d, davon mit Befund: %d" % (n_all, n_bad))
    lines.append("Legende: cutA/cutE = vom Trim vorn/hinten abgeschnittene Samples; ls_roh/le_roh = gemeinte Loop-Punkte im WAV;")
    lines.append("dLS/dLE = Abweichung der Engine-Punkte davon (Samples); Sprung% = Sprung am Wrap in % der Spitze (gemeint/Engine);")
    lines.append("Sprung/typ = Sprung im Verhaeltnis zum typischen Sample-Schritt (> 6 = wahrscheinlich hoerbar).")
    txt = "\n".join(lines)
    print(txt)
    if "-o" in argv:
        with open(argv[argv.index("-o") + 1], "w", encoding="utf-8") as f:
            f.write(txt + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
