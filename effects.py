import math

import numpy as np

from config import SR


class Effect:
    name = "Effect"
    PARAMS = []          # (Label, min, max, Attributname)
    DISPLAY = {}         # optional: {Attributname: Funktion Wert -> Text}

    def __init__(self):
        self.enabled = False
        self.amt = 0.0

    def step_amt(self):
        t = 1.0 if self.enabled else 0.0
        self.amt = min(1.0, max(0.0, self.amt + max(-0.04, min(0.04, t - self.amt))))

    def bypassed(self):
        return (not self.enabled) and self.amt == 0.0

    def amt_ramp(self, n):
        t = 1.0 if self.enabled else 0.0
        a0 = self.amt
        step = n / (0.03 * SR)
        a1 = min(1.0, max(0.0, a0 + max(-step, min(step, t - a0))))
        self.amt = a1
        return np.linspace(a0, a1, n, dtype=np.float32)

    def process(self, x):
        return x

import numpy as np

class Flanger(Effect):
    name = "Flanger"
    L = 8192
    CH = 32
    MIN_D = 34
    PARAMS = [("Delay ms", 0.8, 10.0, 'delay_ms'), ("Depth ms", 0.0, 5.0, 'depth_ms'),
              ("Rate Hz", 0.05, 5.0, 'rate'), ("Feedback", -0.95, 0.95, 'feedback'),
              ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.delay_ms, self.depth_ms, self.rate = 3.0, 2.0, 0.5
        self.feedback, self.mix = 0.5, 0.5
        self.buf = np.zeros(self.L, np.float32)
        self.w = 0
        self.lfo = 0.0
        self.dirty = False

    def process(self, x):
        if self.bypassed():
            if self.dirty:
                self.buf[:] = 0
                self.dirty = False
            return x
        self.dirty = True
        L, CH = self.L, self.CH
        fb = max(-0.95, min(0.95, self.feedback))
        out = np.empty_like(x)
        for s in range(0, len(x), CH):
            xs = x[s:s + CH]
            m = len(xs)
            self.step_amt()
            step = self.rate / SR
            ph = self.lfo + step * np.arange(m)
            self.lfo = (self.lfo + step * m) % 1.0
            d_ms = self.delay_ms + self.depth_ms * np.sin(2 * np.pi * ph)
            d = np.clip(d_ms * SR / 1000.0, self.MIN_D, L - 4)
            idx = self.w + np.arange(m)
            pos = idx - d
            i0 = np.floor(pos).astype(np.int64)
            fr = (pos - i0).astype(np.float32)
            dl = self.buf[i0 % L] * (1.0 - fr) + self.buf[(i0 + 1) % L] * fr
            self.buf[idx % L] = xs + fb * dl
            wet = self.mix * self.amt
            out[s:s + m] = xs * (1.0 - wet) + dl * wet
            self.w = (self.w + m) % L
        return out


class Chorus(Effect):
    name = "Chorus"
    L = 8192
    CH = 256
    MIN_D = 300
    PARAMS = [("Delay ms", 8.0, 30.0, 'delay_ms'), ("Depth ms", 0.0, 8.0, 'depth_ms'),
              ("Rate Hz", 0.1, 3.0, 'rate'), ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.delay_ms, self.depth_ms, self.rate, self.mix = 18.0, 4.0, 0.8, 0.5
        self.buf = np.zeros(self.L, np.float32)
        self.w = 0
        self.lfo = 0.0
        self.dirty = False

    def _tap(self, idx, ph):
        d = np.clip((self.delay_ms + self.depth_ms * np.sin(2 * np.pi * ph)) * SR / 1000.0,
                    self.MIN_D, self.L - 4)
        pos = idx - d
        i0 = np.floor(pos).astype(np.int64)
        fr = (pos - i0).astype(np.float32)
        return self.buf[i0 % self.L] * (1.0 - fr) + self.buf[(i0 + 1) % self.L] * fr

    def process(self, x):
        if self.bypassed():
            if self.dirty:
                self.buf[:] = 0
                self.dirty = False
            return x
        self.dirty = True
        out = np.empty_like(x)
        for s in range(0, len(x), self.CH):
            xs = x[s:s + self.CH]
            m = len(xs)
            self.step_amt()
            step = self.rate / SR
            ph = self.lfo + step * np.arange(m)
            self.lfo = (self.lfo + step * m) % 1.0
            idx = self.w + np.arange(m)
            wet_sig = 0.5 * (self._tap(idx, ph) + self._tap(idx, ph + 0.5))
            self.buf[idx % self.L] = xs
            wet = self.mix * self.amt
            out[s:s + m] = xs * (1.0 - wet) + wet_sig * wet
            self.w = (self.w + m) % self.L
        return out


class Phaser(Effect):
    name = "Phaser"
    CH = 32
    PARAMS = [("Rate Hz", 0.05, 5.0, 'rate'), ("Center Hz", 200.0, 2000.0, 'center'),
              ("Depth", 0.0, 1.0, 'depth'), ("Feedback", -0.9, 0.9, 'feedback'),
              ("Stufen", 2, 8, 'stages'), ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.rate, self.center, self.depth = 0.4, 700.0, 0.6
        self.feedback, self.stages, self.mix = 0.4, 4, 0.5
        self.lfo = 0.0
        D = np.subtract.outer(np.arange(self.CH), np.arange(self.CH))
        self._mask = D >= 0
        self._dc = np.clip(D, 0, None)
        self.reset()

    def reset(self):
        self.xp = np.zeros(8)
        self.yp = np.zeros(8)
        self.last = 0.0

    def process(self, x):
        if self.bypassed():
            self.reset()
            return x
        n = int(round(max(2, min(8, self.stages))))
        fb = max(-0.9, min(0.9, self.feedback))
        out = np.empty_like(x)
        for s in range(0, len(x), self.CH):
            xs = x[s:s + self.CH].astype(np.float64)
            m = len(xs)
            self.step_amt()
            ph = self.lfo
            self.lfo = (self.lfo + self.rate / SR * m) % 1.0
            f = min(self.center * 2 ** (2 * self.depth * math.sin(2 * math.pi * ph)), SR * 0.45)
            t = math.tan(math.pi * f / SR)
            a = (t - 1.0) / (t + 1.0)
            p = (-a) ** np.arange(self.CH)
            T = np.where(self._mask, p[self._dc], 0.0)[:m, :m]
            decay = (-a) ** np.arange(1, m + 1)
            y = xs + fb * self.last
            for i in range(n):
                u = a * y + np.concatenate(([self.xp[i]], y[:-1]))
                yo = T @ u + decay * self.yp[i]
                self.xp[i] = y[-1]
                self.yp[i] = yo[-1]
                y = yo
            self.last = float(y[-1])
            wet = self.mix * self.amt
            out[s:s + m] = xs * (1.0 - wet) + y * wet
        return out


class Tremolo(Effect):
    name = "Tremolo"
    PARAMS = [("Rate Hz", 0.5, 20.0, 'rate'), ("Depth", 0.0, 1.0, 'depth')]

    def __init__(self):
        super().__init__()
        self.rate, self.depth = 5.0, 0.6
        self.lfo = 0.0

    def process(self, x):
        if self.bypassed():
            return x
        n = len(x)
        a = self.amt_ramp(n)
        step = self.rate / SR
        ph = self.lfo + step * np.arange(n)
        self.lfo = (self.lfo + step * n) % 1.0
        lfo = 0.5 * (1.0 + np.sin(2 * np.pi * ph))
        g = 1.0 - a * self.depth * (1.0 - lfo)
        return (x * g).astype(np.float32)


class Echo(Effect):
    name = "Echo"
    L = 65536
    CH = 1024
    PARAMS = [("Delay ms", 50.0, 1000.0, 'delay_ms'), ("Feedback", 0.0, 0.9, 'feedback'),
              ("Dämpfung", 0.0, 1.0, 'damp'), ("Mix", 0.0, 1.0, 'mix')]
    DISPLAY = {'damp': lambda v: "aus" if v <= 0.001 else _hz(12000.0 * (300.0 / 12000.0) ** v)}

    def __init__(self):
        super().__init__()
        self.delay_ms, self.feedback, self.mix = 300.0, 0.4, 0.35
        self.buf = np.zeros(self.L, np.float32)
        self.w = 0
        self.d_prev = None
        self.damp = 0.0
        self.lp = OnePole()
        self.dirty = False

    def process(self, x):
        if self.bypassed():
            if self.dirty:
                self.buf[:] = 0
                self.dirty = False
                self.d_prev = None
                self.lp.reset()
            return x
        self.dirty = True
        L = self.L
        fb = max(0.0, min(0.9, self.feedback))
        d_t = float(np.clip(self.delay_ms * SR / 1000.0, 2 * self.CH, L - 2 * self.CH))
        if self.d_prev is None:
            self.d_prev = d_t
        out = np.empty_like(x)
        for s in range(0, len(x), self.CH):
            xs = x[s:s + self.CH]
            m = len(xs)
            d_vec = np.linspace(self.d_prev, d_t, m)
            idx = self.w + np.arange(m)
            pos = idx - d_vec
            i0 = np.floor(pos).astype(np.int64)
            fr = (pos - i0).astype(np.float32)
            dl = self.buf[i0 % L] * (1.0 - fr) + self.buf[(i0 + 1) % L] * fr

            if self.damp > 0.001:
                fc = 12000.0 * (300.0 / 12000.0) ** self.damp
                fbs = self.lp.process(dl, math.exp(-2 * math.pi * fc / SR))
            else:
                self.lp.reset()
                fbs = dl
            self.buf[idx % L] = xs + fb * fbs

            wet = self.mix * self.amt_ramp(m)
            out[s:s + m] = xs + wet * dl
            self.w = (self.w + m) % L
            self.d_prev = d_t
        return out


class Filter(Effect):
    name = "Filter"
    CH = 32
    PARAMS = [("Typ 0LP 1HP 2BP", 0.0, 2.0, 'mode'), ("Cutoff", 0.0, 1.0, 'cutoff'),
              ("Resonanz", 0.7, 15.0, 'q'), ("LFO Rate Hz", 0.05, 10.0, 'rate'),
              ("LFO Depth", 0.0, 1.0, 'depth'), ("Mix", 0.0, 1.0, 'mix')]
    DISPLAY = {'cutoff': lambda v: _hz(40.0 * 300.0 ** v)}

    def __init__(self):
        super().__init__()
        self.mode, self.cutoff, self.q = 0.0, 0.5, 2.0
        self.rate, self.depth, self.mix = 0.5, 0.0, 1.0
        self.lfo = 0.0
        D = np.subtract.outer(np.arange(self.CH), np.arange(self.CH))
        self._mask = D >= 0
        self._dc = np.clip(D, 0, None)
        self._k = np.arange(self.CH)
        self.reset()

    def reset(self):
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def process(self, x):
        if self.bypassed():
            self.reset()
            return x
        mode = int(round(max(0.0, min(2.0, self.mode))))
        q = max(0.6, self.q)
        out = np.empty_like(x)
        for s in range(0, len(x), self.CH):
            xs = x[s:s + self.CH].astype(np.float64)
            m = len(xs)
            a = self.amt_ramp(m)
            ph = self.lfo
            self.lfo = (self.lfo + self.rate / SR * m) % 1.0
            fc = 40.0 * (12000.0 / 40.0) ** max(0.0, min(1.0, self.cutoff))
            f = fc * 2 ** (3.0 * self.depth * math.sin(2 * math.pi * ph))
            f = max(20.0, min(f, SR * 0.45))
            w0 = 2 * math.pi * f / SR
            cw, sw = math.cos(w0), math.sin(w0)
            alpha = sw / (2.0 * q)
            if mode == 0:
                b0, b1, b2 = (1 - cw) / 2, 1 - cw, (1 - cw) / 2
            elif mode == 1:
                b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
            else:
                b0, b1, b2 = alpha, 0.0, -alpha
            a0 = 1 + alpha
            b0, b1, b2 = b0 / a0, b1 / a0, b2 / a0
            a1, a2 = -2 * cw / a0, (1 - alpha) / a0

            p1 = (-a1 + 1j * math.sqrt(max(4 * a2 - a1 * a1, 1e-12))) / 2
            h = np.imag(p1 ** (self._k + 1)) / np.imag(p1)
            T = np.where(self._mask, h[self._dc], 0.0)[:m, :m]

            ext = np.concatenate(([self.x2, self.x1], xs))
            u = b0 * xs + b1 * ext[1:m + 1] + b2 * ext[0:m]
            hm = h[:m]
            hs = np.concatenate(([0.0], h[:m - 1]))
            zir = self.y1 * (-a1 * hm - a2 * hs) + self.y2 * (-a2 * hm)
            y = T @ u + zir

            self.x2, self.x1 = float(ext[-2]), float(ext[-1])
            ye = np.concatenate(([self.y2, self.y1], y))
            self.y2, self.y1 = float(ye[-2]), float(ye[-1])

            wet = self.mix * a
            out[s:s + m] = xs * (1.0 - wet) + y * wet
        return out


class Distortion(Effect):
    name = "Distortion"
    PARAMS = [("Drive", 1.0, 30.0, 'drive'), ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.drive, self.mix = 5.0, 0.7

    def process(self, x):
        if self.bypassed():
            return x
        d = max(1.0, self.drive)
        y = np.tanh(d * x) / math.tanh(d)
        wet = self.mix * self.amt_ramp(len(x))
        return (x * (1.0 - wet) + y * wet).astype(np.float32)

class Reverb(Effect):
    name = "Reverb"
    CH = 128
    COMB = (1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617)
    ALLP = (556, 441, 341, 225)
    PARAMS = [("Raum", 0.0, 1.0, 'room'), ("Dämpfung", 0.0, 1.0, 'damp'),
              ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.room, self.damp, self.mix = 0.6, 0.4, 0.3
        D = np.subtract.outer(np.arange(self.CH), np.arange(self.CH))
        self._mask = D >= 0
        self._dc = np.clip(D, 0, None)
        self.dirty = False
        self.reset()

    def reset(self):
        self.cb = [np.zeros(d) for d in self.COMB]
        self.cp = [0] * len(self.COMB)
        self.cf = [0.0] * len(self.COMB)
        self.ab = [np.zeros(d) for d in self.ALLP]
        self.ap = [0] * len(self.ALLP)

    def process(self, x):
        if self.bypassed():
            if self.dirty:
                self.reset()
                self.dirty = False
            return x
        self.dirty = True
        fb = max(0.0, min(1.0, self.room)) * 0.28 + 0.7
        d1 = max(0.0, min(1.0, self.damp)) * 0.4
        d2 = 1.0 - d1
        k = np.arange(self.CH)
        pw = d1 ** k
        T_full = np.where(self._mask, pw[self._dc], 0.0)
        decay_full = d1 ** (k + 1)
        out = np.empty_like(x)
        for s in range(0, len(x), self.CH):
            xs = x[s:s + self.CH].astype(np.float64)
            m = len(xs)
            a = self.amt_ramp(m)
            T = T_full[:m, :m]
            decay = decay_full[:m]
            inp = xs * 0.015
            ar = np.arange(m)
            acc = np.zeros(m)
            for i, D in enumerate(self.COMB):
                idx = (self.cp[i] + ar) % D
                o = self.cb[i][idx]
                fs = T @ (d2 * o) + decay * self.cf[i]
                self.cf[i] = float(fs[-1])
                self.cb[i][idx] = inp + fb * fs
                self.cp[i] = (self.cp[i] + m) % D
                acc += o
            y = acc
            for j, D in enumerate(self.ALLP):
                idx = (self.ap[j] + ar) % D
                bo = self.ab[j][idx]
                new_y = -y + bo
                self.ab[j][idx] = y + 0.5 * bo
                self.ap[j] = (self.ap[j] + m) % D
                y = new_y
            wet = self.mix * a
            out[s:s + m] = xs * (1.0 - wet) + y * 3.0 * wet
        return out

def _hz(f):
    if f < 10:
        return f"{f:.2f} Hz"
    if f < 100:
        return f"{f:.1f} Hz"
    return f"{f:.0f} Hz"


class OnePole:
    """Einpoliger Tiefpass, vektorisiert in Stücken (Matrix-Methode)."""
    CH = 64
    _D = np.subtract.outer(np.arange(CH), np.arange(CH))
    _MASK = _D >= 0
    _DC = np.clip(_D, 0, None)
    _K = np.arange(CH)

    def __init__(self):
        self.y = 0.0
        self._a = -1.0
        self._T = np.zeros((self.CH, self.CH))
        self._dec = np.zeros(self.CH)

    def reset(self):
        self.y = 0.0

    def process(self, x, a):
        if a != self._a:
            self._a = a
            self._T = np.where(self._MASK, (1.0 - a) * a ** self._DC, 0.0)
            self._dec = a ** (self._K + 1)
        out = np.empty(len(x))
        for s in range(0, len(x), self.CH):
            xs = np.asarray(x[s:s + self.CH], dtype=np.float64)
            m = len(xs)
            y = self._T[:m, :m] @ xs + self._dec[:m] * self.y
            self.y = float(y[-1])
            out[s:s + m] = y
        return out


class OffsetLFO(Effect):
    name = "Offset-LFO"
    PARAMS = [("Rate", 0.0, 1.0, 'rate'), ("Tiefe", 0.0, 0.33, 'depth')]
    DISPLAY = {'rate': lambda v: _hz(0.05 * 40000.0 ** v)}

    def __init__(self):
        super().__init__()
        self.rate, self.depth = 0.28, 0.10
        self.lfo = 0.0

    def process(self, x):
        if self.bypassed():
            return x
        n = len(x)
        w = self.amt_ramp(n) * self.depth
        step = 0.05 * 40000.0 ** self.rate / SR
        ph = self.lfo + step * np.arange(n)
        self.lfo = (self.lfo + step * n) % 1.0
        s = np.sin(2 * np.pi * ph)
        return (x * (1.0 - w) + w * s).astype(np.float32)


class Overdrive(Effect):
    name = "Overdrive"
    PARAMS = [("Drive", 0.0, 1.0, 'drive'), ("Härte", 0.0, 1.0, 'hard'),
              ("Tone", 0.0, 1.0, 'tone'), ("Level", 0.0, 1.5, 'level'),
              ("Mix", 0.0, 1.0, 'mix')]
    DISPLAY = {'tone': lambda v: _hz(1500.0 * 8.0 ** v)}

    def __init__(self):
        super().__init__()
        self.drive, self.hard, self.tone = 0.5, 0.3, 0.6
        self.level, self.mix = 0.7, 1.0
        self.hp = OnePole()
        self.tp = OnePole()

    def process(self, x):
        if self.bypassed():
            self.hp.reset()
            self.tp.reset()
            return x
        n = len(x)
        w = self.mix * self.amt_ramp(n)
        xd = x.astype(np.float64)
        lo = self.hp.process(xd, math.exp(-2 * math.pi * 60.0 / SR))
        pre = xd - lo
        g = 1.0 + 60.0 * self.drive ** 2
        bias = 0.15
        h = max(0.0, min(1.0, self.hard))
        z = g * pre + bias
        y = (1.0 - h) * np.tanh(z) + h * np.clip(z, -1.0, 1.0)
        y -= (1.0 - h) * math.tanh(bias) + h * min(1.0, bias)
        y = np.tanh(1.5 * y) / math.tanh(1.5)
        fc = 1500.0 * 8.0 ** self.tone
        y = self.tp.process(y, math.exp(-2 * math.pi * fc / SR)) * self.level
        return (xd * (1.0 - w) + y * w).astype(np.float32)

class Fuzz(Effect):
    name = "Fuzz"
    PARAMS = [("Sustain", 0.0, 1.0, 'sustain'), ("Bias", 0.0, 0.6, 'bias'),
              ("Tone", 0.0, 1.0, 'tone'), ("Level", 0.0, 1.5, 'level'),
              ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.sustain, self.bias, self.tone = 0.6, 0.15, 0.5
        self.level, self.mix = 0.5, 1.0
        self.lo = OnePole()
        self.hi = OnePole()

    def process(self, x):
        if self.bypassed():
            self.lo.reset()
            self.hi.reset()
            return x
        n = len(x)
        w = self.mix * self.amt_ramp(n)
        xd = x.astype(np.float64)
        g1 = 5.0 + 95.0 * self.sustain
        s1 = np.tanh(g1 * xd + self.bias) - math.tanh(self.bias)
        s2 = np.tanh(3.0 * s1)
        low = self.lo.process(s2, math.exp(-2 * math.pi * 800.0 / SR))
        high = s2 - self.hi.process(s2, math.exp(-2 * math.pi * 1500.0 / SR))
        y = ((1.0 - self.tone) * low + self.tone * high) * self.level
        return (xd * (1.0 - w) + y * w).astype(np.float32)


class Bitcrusher(Effect):
    name = "Bitcrusher"
    PARAMS = [("Bits", 1, 16, 'bits'), ("Downsample", 1, 32, 'down'),
              ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.bits, self.down, self.mix = 8.0, 4.0, 1.0
        self.pos = 0
        self.held = 0.0

    def process(self, x):
        if self.bypassed():
            self.pos = 0
            self.held = 0.0
            return x
        n = len(x)
        down = max(1, int(round(self.down)))
        bits = max(1, min(16, int(round(self.bits))))
        ar = np.arange(n)
        take = ((self.pos + ar) % down) == 0
        idx = np.maximum.accumulate(np.where(take, ar, -1))
        held = np.where(idx >= 0, x[np.maximum(idx, 0)], self.held)
        self.held = float(held[-1])
        self.pos = (self.pos + n) % down
        levels = 2.0 ** (bits - 1)
        y = np.round(held * levels) / levels
        w = self.mix * self.amt_ramp(n)
        return (x * (1.0 - w) + y * w).astype(np.float32)

class Vocoder(Effect):
    name = "Vocoder"
    N = 1024
    HOP = 256
    NB = 16
    F_LO, F_HI = 100.0, 8000.0
    GAIN = 30.0
    PARAMS = [("Pegel", 0.0, 4.0, 'level'), ("Glättung", 0.0, 1.0, 'smooth'),
              ("Zischlaute", 0.0, 1.0, 'hiss'), ("Mix", 0.0, 1.0, 'mix')]

    def __init__(self):
        super().__init__()
        self.level, self.smooth, self.hiss, self.mix = 1.0, 0.3, 0.3, 1.0
        N = self.N
        self.win = np.sin(np.pi * (np.arange(N) + 0.5) / N)
        hz = SR / N
        b = np.round(np.geomspace(self.F_LO, self.F_HI, self.NB + 1) / hz).astype(int)
        for i in range(1, len(b)):
            b[i] = max(b[i], b[i - 1] + 1)
        self.bins = b
        self.h0 = int(4000.0 / hz)
        self.h1 = int(12000.0 / hz)
        self.rng = np.random.default_rng(1)
        self.mod = np.zeros(0, np.float32)
        self.mpos = 0
        self.dirty = False
        self.reset()

    def reset(self):
        N = self.N
        self.cbuf = np.zeros(N)
        self.mbuf = np.zeros(N)
        self.obuf = np.zeros(N)
        self.inq = np.zeros(0)
        self.mq = np.zeros(0)
        self.outq = np.zeros(0)
        self.env = np.zeros(self.NB)

    def set_modulator(self, data, sr):
        d = np.asarray(data, dtype=np.float64)
        if sr != SR:
            n = max(2, int(len(d) * SR / sr))
            d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d)
        rms = float(np.sqrt(np.mean(d * d)))
        if rms > 1e-6:
            d = d * (0.1 / rms)
        self.mpos = 0
        self.mod = d.astype(np.float32)

    def _modchunk(self, n):
        m = self.mod
        idx = (self.mpos + np.arange(n)) % len(m)
        self.mpos = int((self.mpos + n) % len(m))
        return m[idx]

    def _frame(self):
        N, H, NB = self.N, self.HOP, self.NB
        b = self.bins
        C = np.fft.rfft(self.cbuf * self.win)
        M = np.fft.rfft(self.mbuf * self.win)
        p = np.abs(M[:b[-1]]) ** 2
        e = 2.0 * np.sqrt(np.add.reduceat(p, b[:-1])) / N
        tau = 0.005 + 0.2 * max(0.0, min(1.0, self.smooth))
        a = math.exp(-H / (SR * tau))
        self.env = np.where(e > self.env, e, a * self.env + (1.0 - a) * e)
        g = self.GAIN * self.level * self.env
        gb = np.zeros(len(C))
        for i in range(NB):
            gb[b[i]:b[i + 1]] = g[i]
        Y = C * gb
        if self.hiss > 0.0:
            ref = float(np.mean(np.abs(C[b[0]:b[-1]])))
            hf = float(np.mean(g[-3:])) * self.hiss * ref
            if hf > 0.0:
                ph = np.exp(2j * np.pi * self.rng.random(self.h1 - self.h0))
                Y[self.h0:self.h1] += hf * ph
        y = np.fft.irfft(Y, N) * self.win * 0.5
        self.obuf += y
        out = self.obuf[:H].copy()
        self.obuf = np.concatenate((self.obuf[H:], np.zeros(H)))
        return out

    def process(self, x):
        if self.bypassed() or len(self.mod) < 2:
            if self.dirty:
                self.reset()
                self.dirty = False
            return x
        self.dirty = True
        n = len(x)
        H = self.HOP
        a = self.amt_ramp(n)
        car = np.concatenate((self.inq, x.astype(np.float64)))
        mq = np.concatenate((self.mq, self._modchunk(n)))
        pos = 0
        outs = []
        while len(car) - pos >= H:
            self.cbuf = np.concatenate((self.cbuf[H:], car[pos:pos + H]))
            self.mbuf = np.concatenate((self.mbuf[H:], mq[pos:pos + H]))
            outs.append(self._frame())
            pos += H
        self.inq = car[pos:]
        self.mq = mq[pos:]
        if outs:
            self.outq = np.concatenate([self.outq] + outs)
        if len(self.outq) >= n:
            wet = self.outq[:n]
            self.outq = self.outq[n:]
        else:
            wet = np.concatenate((np.zeros(n - len(self.outq)), self.outq))
            self.outq = np.zeros(0)
        w = self.mix * a
        return (x * (1.0 - w) + wet * w).astype(np.float32)

def make_chain():
    return [Vocoder(), Filter(), OffsetLFO(), Overdrive(), Fuzz(), Distortion(), Bitcrusher(),
            Flanger(), Chorus(), Phaser(), Tremolo(), Echo(), Reverb()]
