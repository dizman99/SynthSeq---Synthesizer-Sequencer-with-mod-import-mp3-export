import math

import numpy as np

from config import SR, BASE_C4
from audio_utils import make_table, make_tables, normalize_gain
from effects import make_chain

ATT, DEC, SUS, REL, DONE = range(5)


class Voice:
    def __init__(self, semitone, vel=1.0):
        self.semi = semitone
        self.vel = vel
        self.freq = BASE_C4 * 2 ** (semitone / 12)
        self.phase = 0.0
        self.stage = ATT
        self.level = 0.0
        self.rel_step = 0.0
        self.phases = np.random.rand(9) * 2048   # N = TABLE_N
        self.phases[0] = 0.0                      # Stimme 0 startet wie bisher

    def release(self, R):
        if self.stage in (REL, DONE):
            return
        self.stage = REL
        self.rel_step = max(self.level, 1e-4) / (max(R, 0.002) * SR)

    def envelope(self, n, A, D, S, R):
        env = np.empty(n, np.float32)
        pos = 0
        while pos < n:
            left = n - pos
            if self.stage == ATT:
                step = 1.0 / (max(A, 0.002) * SR)
                k = min(left, max(1, math.ceil((1.0 - self.level) / step)))
                seg = np.minimum(self.level + step * np.arange(1, k + 1), 1.0)
                env[pos:pos + k] = seg
                self.level = float(seg[-1])
                pos += k
                if self.level >= 1.0:
                    self.stage = DEC
            elif self.stage == DEC:
                if self.level <= S:
                    self.stage = SUS
                    continue
                step = (1.0 - S) / (max(D, 0.002) * SR)
                k = min(left, max(1, math.ceil((self.level - S) / step)))
                seg = np.maximum(self.level - step * np.arange(1, k + 1), S)
                env[pos:pos + k] = seg
                self.level = float(seg[-1])
                pos += k
                if self.level <= S:
                    self.stage = SUS
            elif self.stage == SUS:
                self.level = S
                env[pos:] = S
                pos = n
            elif self.stage == REL:
                k = min(left, max(1, math.ceil(self.level / self.rel_step)))
                seg = np.maximum(self.level - self.rel_step * np.arange(1, k + 1), 0.0)
                env[pos:pos + k] = seg
                self.level = float(seg[-1])
                pos += k
                if self.level <= 0.0:
                    self.stage = DONE
            else:
                env[pos:] = 0.0
                pos = n
        return env


class Engine:
    def __init__(self):
        self.kind = 'sine'
        self.table = make_table('sine')
        self.tfs = [(None, self.table.astype(np.float32) / 127.0)]
        self.A, self.D, self.S, self.R = 0.01, 0.1, 0.7, 0.3
        self.volume = 0.3
        self.voices = {}
        self.dying = []
        self.chain = make_chain()
        self.source = 'generator'
        self.sample = np.zeros(0, np.float32)
        self.sample_sr = SR
        self.root_semi = 0
        self.sample_loop = False
        self.loop_start = 0.0
        self.loop_end = 1.0
        self.loop_xfade = 0.0
        self.loop_snap = False
        self._snap_key = None
        self._snap_val = (0, 0)
        self.normalize = False
        self.sample_gain = 1.0
        self.unison = 1
        self.detune = 0.0

    def set_wave(self, kind):
        self.kind = kind
        self.table = make_table(kind)
        self.tfs = [(nh, t.astype(np.float32) / 127.0) for nh, t in make_tables(kind)]

    def _pick(self, f):
        for nh, tfk in self.tfs:
            if nh is None or nh * f <= 20000.0:
                return tfk
        return self.tfs[-1][1]

    def note_on(self, key, semi, vel=1.0):
        if key not in self.voices:
            self.voices[key] = Voice(semi, vel)

    def note_off(self, key):
        v = self.voices.pop(key, None)
        if v:
            v.release(self.R)
            self.dying.append(v)

    def set_sample(self, data, sr):
        self.sample_sr = sr
        self.sample = data
        self.update_gain()
        self._snap_key = None

    def update_gain(self):
        self.sample_gain = normalize_gain(self.sample) if self.normalize else 1.0

    def loop_range(self, Ns):
        ls = int(round(min(max(self.loop_start, 0.0), 1.0) * Ns))
        le = int(round(min(max(self.loop_end, 0.0), 1.0) * Ns))
        if le - ls < 16:
            le = min(Ns, ls + 16)
            ls = max(0, le - 16)
        if self.loop_snap and len(self.sample) == Ns:
            key = (ls, le, Ns)
            if key != self._snap_key:
                self._snap_val = (self._snap(ls), self._snap(le))
                self._snap_key = key
            ls2, le2 = self._snap_val
            if le2 - ls2 >= 16:
                return ls2, le2
        return ls, le

    def _snap(self, i):
        smp = self.sample
        Ns = len(smp)
        if i <= 0 or i >= Ns:
            return i
        a = max(0, i - 2048)
        b = min(Ns - 1, i + 2048)
        seg = smp[a:b + 1]
        zc = np.nonzero((seg[:-1] <= 0) & (seg[1:] > 0))[0]
        if len(zc) == 0:
            return i
        zc = zc + a + 1
        return int(zc[np.argmin(np.abs(zc - i))])

    def render(self, frames):
        mix = np.zeros(frames, np.float32)
        idx = np.arange(frames, dtype=np.float64)
        smp = self.sample
        use_sample = self.source == 'sample' and len(smp) > 1
        gain = self.sample_gain if use_sample else 1.0
        ls = le = xf = 0

        if use_sample:
            Ns = len(smp)
            loop = self.sample_loop
            ls, le = self.loop_range(Ns)
            xf = min(int(self.loop_xfade * (le - ls)), ls)
            root = self.root_semi
            sr_ratio = self.sample_sr / SR
        else:
            table = self.table
            N = len(table)
            tf = table.astype(np.float32) / 127.0

        for v in list(self.voices.values()) + list(self.dying):
            if v.stage == DONE:
                continue
            ended = False
            if use_sample:
                ratio = 2 ** ((v.semi - root) / 12) * sr_ratio
                pos = v.phase + ratio * idx

                if loop:
                    L = le - ls
                    pos = np.where(pos >= le, ls + (pos - ls) % L, pos)
                    i0 = pos.astype(np.int64)
                    i1 = np.where(i0 + 1 >= le, ls, i0 + 1)
                    frac = (pos - i0).astype(np.float32)
                    s = smp[i0] * (1.0 - frac) + smp[i1] * frac
                    if xf > 0:
                        m = pos >= le - xf
                        if m.any():
                            p2 = np.where(m, pos - L, 0.0)
                            j0 = p2.astype(np.int64)
                            j1 = np.minimum(j0 + 1, Ns - 1)
                            fr2 = (p2 - j0).astype(np.float32)
                            s2 = smp[j0] * (1.0 - fr2) + smp[j1] * fr2
                            w = np.clip((pos - (le - xf)) / xf, 0.0, 1.0).astype(np.float32)
                            s = np.where(m, s * (1.0 - w) + s2 * w, s)
                    p = v.phase + ratio * frames
                    if p >= le:
                        p = ls + (p - ls) % L
                    v.phase = float(p)

                else:
                    i0 = pos.astype(np.int64)
                    alive = i0 < Ns - 1
                    i0c = np.minimum(i0, Ns - 1)
                    i1 = np.minimum(i0c + 1, Ns - 1)
                    frac = (pos - i0).astype(np.float32)
                    s = smp[i0c] * (1.0 - frac) + smp[i1] * frac
                    s[~alive] = 0.0
                    v.phase = float(v.phase + ratio * frames)
                    if v.phase >= Ns - 1:
                        ended = True
            else:

                tf = self._pick(v.freq * (2.0 ** (self.detune / 1200.0) if self.unison > 1 else 1.0))
                N = len(tf)

                n = int(self.unison)
                if n > 1 and self.kind != 'noise':
                    offs = np.linspace(-1, 1, n)
                    incs = (v.freq * N / SR) * 2.0 ** (self.detune * offs / 1200.0)
                    ph = (v.phases[:n, None] + incs[:, None] * idx[None, :]) % N
                    v.phases[:n] = (v.phases[:n] + incs * frames) % N
                    i0 = ph.astype(np.int64)
                    frac = (ph - i0).astype(np.float32)
                    s = (tf[i0] * (1.0 - frac) + tf[(i0 + 1) % N] * frac).sum(axis=0) / math.sqrt(n)
                else:
                    # bisheriger Code unverändert
                    inc = v.freq / BASE_C4 if self.kind == 'noise' else v.freq * N / SR
                    ph = (v.phase + inc * idx) % N
                    v.phase = float((v.phase + inc * frames) % N)
                    i0 = ph.astype(np.int64)
                    frac = (ph - i0).astype(np.float32)
                    s = tf[i0] * (1.0 - frac) + tf[(i0 + 1) % N] * frac
            mix += s * (gain * v.vel) * v.envelope(frames, self.A, self.D, self.S, self.R)
            if ended:
                v.stage = DONE

        self.dying = [v for v in self.dying if v.stage != DONE]
        out = (mix * self.volume).astype(np.float32)
        for fx in self.chain:
            out = fx.process(out)
        out = np.clip(out, -1.0, 1.0)
        return out

