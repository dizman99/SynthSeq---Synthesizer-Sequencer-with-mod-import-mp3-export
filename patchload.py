"""patchload.py - Patch ohne GUI in eine Engine laden (fuer Sequencer / mehrere Parts / App).

load_patch_into(eng, d, patch_path, cache=None) -> LoadResult
  LoadResult ist eine Liste von Warnungen (Strings) mit zusaetzlichen Attributen
  sample_path / mod_path (nur gesetzt, wenn die Datei wirklich geladen wurde).

Setzt nur Werte, die im Patch stehen (bzw. dokumentierte Defaults); setzt NICHTS vorher zurueck.
Fuer eine frische Engine() ist das ein sauberer Zustand. Eine Engine, die schon im Mixer
laeuft, wird direkt veraendert (wie vorher durch die Regler). Fuer neue Parts: erst laden,
dann mixer.add_part(eng).

cache: optionales dict {normalisierter Pfad: (daten, samplerate)} - Einhaengepunkt fuer das
Vorhalten von WAV-Dateien im Sequencer. Mit cache=None wird immer von der Platte geladen.
"""
import os

import patch as patchlib
from audio_utils import load_wav, trim_silence
from config import ROOT_CHOICES

WAVES = ("sine", "square", "saw", "noise")
INT_ATTRS = {"stages", "mode", "bits", "down", "unison"}

# Einzige Quelle fuer die Grenzen der Engine-Regler (app.py baut seine Regler daraus)
LIMITS = {
    "A": (0.002, 2.0), "D": (0.002, 2.0), "S": (0.0, 1.0), "R": (0.002, 3.0),
    "volume": (0.0, 1.0), "unison": (1, 9), "detune": (0, 50),
    "loop_start": (0.0, 1.0), "loop_end": (0.0, 1.0), "loop_xfade": (0.0, 0.5),
}


class LoadResult(list):
    sample_path = None
    mod_path = None


def _set(target, attr, val, lo, hi, integer=False):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return
    v = max(lo, min(hi, v))
    if integer:
        v = float(round(v))
    setattr(target, attr, v)


def _set_eng(eng, attr, val):
    lo, hi = LIMITS[attr]
    _set(eng, attr, val, lo, hi, attr in INT_ATTRS)


def _load_audio(path, cache):
    key = os.path.normcase(os.path.abspath(path))
    if cache is not None and key in cache:
        return cache[key]
    data, sr = load_wav(path)
    trimmed = trim_silence(data)
    if trimmed is not None:
        data = trimmed
    if cache is not None:
        cache[key] = (data, sr)
    return data, sr


def load_patch_into(eng, d, patch_path, cache=None):
    res = LoadResult()
    env = d.get("envelope", {}) or {}
    for key, attr in (("attack", "A"), ("decay", "D"), ("sustain", "S"), ("release", "R")):
        if key in env:
            _set_eng(eng, attr, env[key])
    if "volume" in d:
        _set_eng(eng, "volume", d["volume"])
    _set_eng(eng, "unison", d.get("unison", 1))
    _set_eng(eng, "detune", d.get("detune", 0.0))

    src = d.get("source", {}) or {}
    eng.normalize = bool(d.get("normalize", False))
    w = src.get("wave", "sine")
    if w in WAVES:
        eng.set_wave(w)
    label = src.get("root", "C4")
    if label in ROOT_CHOICES:
        eng.root_semi = ROOT_CHOICES[label]
    eng.sample_loop = bool(src.get("loop", False))
    _set_eng(eng, "loop_start", src.get("loop_start", 0.0))
    _set_eng(eng, "loop_end", src.get("loop_end", 1.0))
    _set_eng(eng, "loop_xfade", src.get("loop_xfade", 0.0))
    eng.loop_snap = bool(src.get("loop_snap", False))

    loaded = False
    sample = src.get("sample")
    if sample:
        path = patchlib.resolve_sample(sample, patch_path)
        if path:
            try:
                data, sr = _load_audio(path, cache)
                eng.set_sample(data, sr)
                loaded = True
                res.sample_path = path
            except Exception as e:
                res.append("Sample konnte nicht geladen werden: %s" % e)
        else:
            res.append("Sample-Datei nicht gefunden")
    use_sample = src.get("type") == "sample" and loaded
    eng.source = "sample" if use_sample else "generator"

    by_name = {fx.name: fx for fx in eng.chain}
    for e in d.get("effects", []) or []:
        fx = by_name.get(e.get("type"))
        if fx is None:
            continue
        fx.enabled = bool(e.get("enabled", False))
        limits = {attr: (lo, hi) for _l, lo, hi, attr in fx.PARAMS}
        for attr, val in (e.get("params", {}) or {}).items():
            if attr in limits:
                lo, hi = limits[attr]
                _set(fx, attr, val, lo, hi, attr in INT_ATTRS)

    mod = d.get("modulator")
    vc = by_name.get("Vocoder")
    if mod and vc is not None:
        mpath = patchlib.resolve_sample(mod, patch_path)
        if mpath:
            try:
                data, sr = _load_audio(mpath, cache)
                vc.set_modulator(data, sr)
                res.mod_path = mpath
            except Exception as e:
                res.append("Modulator konnte nicht geladen werden: %s" % e)
        else:
            res.append("Modulator-Datei nicht gefunden")
    return res
