SR = 44100
TABLE_N = 2048
NOISE_N = 44100
BASE_C4 = 261.6256
RENDER_TAIL_S = 3.0
NORMALIZE_DBFS = -1.0
SLIDER_LEN = 80
FX_AREA_H = 260

TRIM_THRESHOLD = 1.0 / 127.0      # ca. -42 dBFS
TRIM_PREROLL_MS = 10.0
TRIM_FADEOUT = 128                # Samples, verhindert Knacks am Schnittende
PATCH_VERSION = 1

KEYMAP = {
    'q': 0, '2': 1, 'w': 2, '3': 3, 'e': 4, 'r': 5, '5': 6, 't': 7,
    '6': 8, 'z': 9, '7': 10, 'u': 11, 'i': 12, '9': 13, 'o': 14,
    '0': 15, 'p': 16, 'udiaeresis': 17,
}

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "H"]

VEL_MAX = 99
NUM_TRACKS = 32          # Spuren im Sequencer (Raster, Spurtabelle, Engines richten sich danach)

def vel_gain(v):
    """Notenlautstaerke 0..99 -> Faktor 0..1; None = Patch-Lautstaerke (1.0)."""
    if v is None:
        return 1.0
    return max(0, min(VEL_MAX, int(v))) / VEL_MAX

def note_label(s):
    return f"{NOTE_NAMES[s % 12]}{4 + s // 12}"


ROOT_CHOICES = {note_label(s): s for s in range(-36, 36)}

import os
import sys

def _base_dir():
    # .exe: Ordner der exe; sonst Ordner dieser Datei (unabhängig vom Arbeitsverzeichnis)
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = _base_dir()

def sub_dir(name):
    return os.path.join(BASE_DIR, name)
