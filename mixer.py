"""mixer.py - Audioausgabe: ein Strom (Stereo), mehrere Klangquellen (Engines) werden addiert.

Jede Klangquelle ("Part") ist eine Engine mit render(frames) -> float32-Block (mono).
Der Mixer verteilt jede Engine per Pan (konstante Leistung) auf links/rechts und besitzt
den sounddevice-Strom, die Aufnahme (Stereo, Form (N, 2)) und die Vorschau.
"""
import math

import numpy as np
import sounddevice as sd

from config import SR

SQRT2 = math.sqrt(2.0)


def pan_gains(pan):
    """pan -1.0 (links) .. +1.0 (rechts) -> (gl, gr); Mitte = (1, 1), konstante Leistung."""
    p = max(-1.0, min(1.0, float(pan)))
    th = (p + 1.0) * math.pi / 4.0
    return (SQRT2 * math.cos(th), SQRT2 * math.sin(th))


CENTER = pan_gains(0.0)
RAMP_MS = 10.0
RAMP_STEP = SQRT2 / (SR * RAMP_MS / 1000.0)   # max. Aenderung des Faktors pro Sample


class Mixer:
    def __init__(self):
        self.parts = []
        self.gains = {}            # {Engine: (gl, gr)}, wird immer neu zugewiesen
        self._cur = {}      # aktuelle Faktoren je Part (nur im Audio-Thread benutzt)
        self.player = None         # Sequencer-Wiedergabe (player.Player) oder None
        self.stream = None
        self.out_channels = 2
        self.device_note = ""
        self.recording = False
        self.rec_blocks = []
        self.rec_frames = 0
        self.pv_data = None
        self.pv_pos = 0

    # Listen und dicts werden immer neu zugewiesen (nie veraendert), damit der
    # Audio-Thread nie einen halb geaenderten Zustand sieht.
    def add_part(self, eng, pan=None):
        if pan is not None:
            self.gains = {**self.gains, eng: pan_gains(pan)}
        if eng not in self.parts:
            self.parts = self.parts + [eng]

    def remove_part(self, eng):
        self.parts = [p for p in self.parts if p is not eng]
        if eng in self.gains:
            self.gains = {k: v for k, v in self.gains.items() if k is not eng}

    def set_pan(self, eng, pan):
        self.gains = {**self.gains, eng: pan_gains(pan)}

    def start_player(self, pl):
        pl.mixer = self
        self.player = pl

    def _open(self, device, channels):
        st = sd.OutputStream(samplerate=SR, device=device, channels=channels,
                             dtype='float32', blocksize=1024, latency='high',
                             callback=self._callback)
        st.start()
        self.out_channels = channels
        return st

    def start(self):
        try:
            self.stream = self._open(None, 2)
            return
        except sd.PortAudioError as first:
            err = first
        try:
            info = sd.query_devices(kind="output")
            host = info["hostapi"]
            if info["max_output_channels"] == 1:
                self.stream = self._open(None, 1)
                self.device_note = f"{info['name']}: nur mono"
                print("Mixer:", self.device_note, flush=True)
                return
            for i, d in enumerate(sd.query_devices()):
                if d["hostapi"] == host and d["max_output_channels"] >= 2:
                    try:
                        self.stream = self._open(i, 2)
                    except sd.PortAudioError:
                        continue
                    self.device_note = f"Ausweichgerät: {d['name']}"
                    print("Mixer:", self.device_note, flush=True)
                    return
        except Exception:
            pass
        raise err

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def start_recording(self):
        self.rec_blocks = []
        self.rec_frames = 0
        self.recording = True

    def stop_recording(self):
        self.recording = False
        blocks, self.rec_blocks = self.rec_blocks, []
        if not blocks:
            return None
        return np.concatenate(blocks).astype(np.float32)      # Form (N, 2)

    def play_preview(self, data):
        d = np.asarray(data, np.float32)
        if d.ndim == 1:                                        # Mono -> beide Seiten
            d = np.column_stack([d, d])
        self.pv_pos = 0
        self.pv_data = d

    def stop_preview(self):
        self.pv_data = None

    def latency(self):
        st = self.stream
        if st is None:
            return 0.1
        try:
            lat = st.latency
            if isinstance(lat, (tuple, list)):
                lat = lat[-1]                  # (Eingang, Ausgang): Ausgang nehmen
            return float(lat)
        except Exception:
            return 0.1

    def _callback(self, outdata, frames, time_info, status):
        parts, gains = self.parts, self.gains
        out = np.zeros((frames, 2), np.float32)
        clk = None
        pos = 0
        while pos < frames:
            n = frames - pos
            pl = self.player
            if pl is not None:
                try:
                    n = pl.prepare(n)
                except Exception as e:
                    pl.fail(e)
                    self.player = None
                    pl = None
                    n = frames - pos
            gains = self.gains      # Pan-Befehle des Players sofort uebernehmen

            for part in parts:
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
                c = pl.click_samples(n)
                if c is not None:
                    if clk is None:
                        clk = np.zeros(frames, np.float32)
                    clk[pos:pos + n] += c
                pl.advance(n)
                if pl.done:
                    self.player = None
            pos += n
        np.clip(out, -1.0, 1.0, out=out)
        if self.recording:
            self.rec_blocks.append(out.copy())
            self.rec_frames += frames
        if clk is not None:
            out += clk[:, None]
        pv = self.pv_data
        if pv is not None:
            p = self.pv_pos
            chunk = pv[p:p + frames]
            out[:len(chunk)] += chunk
            self.pv_pos = p + frames
            if self.pv_pos >= len(pv):
                self.pv_data = None
        res = np.clip(out, -1.0, 1.0)
        outdata[:] = res if self.out_channels == 2 else res.mean(axis=1, keepdims=True)
