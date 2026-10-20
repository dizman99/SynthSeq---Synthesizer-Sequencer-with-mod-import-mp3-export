import math
import wave

import numpy as np

from config import (SR, TABLE_N, NOISE_N, TRIM_THRESHOLD, TRIM_PREROLL_MS,
                    TRIM_FADEOUT, NORMALIZE_DBFS, RENDER_TAIL_S)


def to_int8(x):
    return np.clip(np.round(np.asarray(x) * 127.0), -127, 127).astype(np.int8)


def make_table(kind):
    if kind == 'noise':
        rng = np.random.default_rng(12345)
        return to_int8(rng.uniform(-1, 1, NOISE_N))
    ph = np.arange(TABLE_N) / TABLE_N
    if kind == 'sine':
        w = np.sin(2 * np.pi * ph)
    elif kind == 'square':
        w = np.where(ph < 0.5, 1.0, -1.0)
    elif kind == 'saw':
        w = 2.0 * ph - 1.0
    else:
        raise ValueError(kind)
    return to_int8(w)

def make_tables(kind):
    if kind not in ('saw', 'square'):
        return [(None, make_table(kind))]
    out, seen, k = [], set(), 0
    while True:
        nh = int((TABLE_N // 2 - 1) / 2 ** (k / 2.0))
        if nh < 1:
            break
        k += 1
        if nh in seen:
            continue
        seen.add(nh)
        ks = np.arange(1, nh + 1)
        if kind == 'saw':
            b = -(2.0 / np.pi) / ks
        else:
            b = np.where(ks % 2 == 1, (4.0 / np.pi) / ks, 0.0)
        b = b * np.sinc(ks / (nh + 1.0))
        spec = np.zeros(TABLE_N // 2 + 1, dtype=complex)
        spec[1:nh + 1] = -0.5j * TABLE_N * b
        w = np.fft.irfft(spec, TABLE_N)
        w /= max(1.0, float(np.max(np.abs(w))))
        out.append((nh, to_int8(w)))
    return out

def trim_silence(x):
    m = np.abs(x) if x.ndim == 1 else np.abs(x).max(axis=1)
    idx = np.flatnonzero(m >= TRIM_THRESHOLD)
    if idx.size == 0:
        return None
    start = max(0, int(idx[0]) - int(TRIM_PREROLL_MS / 1000.0 * SR))
    end = int(idx[-1]) + 1
    y = x[start:end].copy()
    n = min(TRIM_FADEOUT, len(y))
    fade = np.linspace(1.0, 0.0, n, dtype=np.float32)
    if y.ndim == 2:
        fade = fade[:, None]
    y[-n:] *= fade
    return y

def write_wav16(path, x):
    x = np.asarray(x)
    ch = 1 if x.ndim == 1 else x.shape[1]
    data = np.clip(np.round(x * 32767.0), -32768, 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())      # (N, 2) in C-Ordnung = interleaved

def normalize_gain(x):
    pk = float(np.max(np.abs(x))) if len(x) else 0.0
    if pk < 1e-6:
        return 1.0
    return (10 ** (NORMALIZE_DBFS / 20.0)) / pk


def load_wav(path):
    with wave.open(path, 'rb') as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, '<i2').astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        a = ((v ^ 0x800000) - 0x800000).astype(np.float32) / 8388608.0
    elif sw == 4:
        a = np.frombuffer(raw, '<i4').astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Nicht unterstützte Sample-Breite: {sw} Byte")
    if ch > 1:
        a = a[:len(a) // ch * ch].reshape(-1, ch).mean(axis=1)
    if len(a) < 2:
        raise ValueError("WAV ist zu kurz")
    return a.astype(np.float32), sr


def render_offline(data, sr, chain, progress=None):
    if sr != SR:
        n = int(len(data) * SR / sr)
        data = np.interp(np.linspace(0, len(data) - 1, n),
                         np.arange(len(data)), data).astype(np.float32)
    fxs = []
    for fx in chain:
        if not fx.enabled:
            continue
        nf = type(fx)()
        for _label, _lo, _hi, attr in fx.PARAMS:
            setattr(nf, attr, getattr(fx, attr))
        nf.enabled = True
        nf.amt = 1.0
        fxs.append(nf)
    if not fxs:
        return None
    x = np.concatenate([data, np.zeros(int(RENDER_TAIL_S * SR), np.float32)]).astype(np.float32)
    out = np.empty_like(x)
    BLOCK = 1024
    for i, s in enumerate(range(0, len(x), BLOCK)):
        b = x[s:s + BLOCK]
        for fx in fxs:
            b = fx.process(b)
        out[s:s + len(b)] = b
        if progress and i % 20 == 0:
            progress(s / len(x))
    return np.clip(out, -1.0, 1.0)