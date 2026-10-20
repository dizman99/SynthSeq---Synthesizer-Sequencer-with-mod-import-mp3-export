import tkinter as tk
from tkinter import ttk


def _guess_bg(parent):
    try:
        return parent.cget('background')
    except tk.TclError:
        return ttk.Style().lookup('TFrame', 'background') or '#f0f0f0'


class Knob(tk.Canvas):
    active = None            # zuletzt überfahrener/angeklickter Regler
    W, H = 80, 90
    CX, CY, R = 40, 44, 20

    def __init__(self, parent, label, lo, hi, value, command=None, default=None, step=None, fmt=None):
        super().__init__(parent, width=self.W, height=self.H, highlightthickness=0,
                         bg=_guess_bg(parent), takefocus=0)
        self.label = label
        self.lo, self.hi = float(lo), float(hi)
        self.command = command
        self.step = step
        self.fmt = fmt
        self.default = float(value if default is None else default)
        self.value = self._clamp(float(value))
        self._y0 = 0
        self._v0 = 0.0
        self.bind('<Enter>', lambda e: self._activate())
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<Double-Button-1>', lambda e: self.set(self.default))
        self.bind('<MouseWheel>', self._on_wheel)
        self._draw()

    # ---- Wert
    def _clamp(self, v):
        if self.step:
            v = self.lo + round((v - self.lo) / self.step) * self.step
        return max(self.lo, min(self.hi, v))

    def get(self):
        return self.value

    def set(self, v, notify=True):
        v = self._clamp(float(v))
        if v == self.value:
            return
        self.value = v
        self._draw()
        if notify and self.command:
            self.command(v)

    def nudge(self, direction, factor):
        if self.step:
            self.set(self.value + direction * self.step)
        else:
            self.set(self.value + direction * (self.hi - self.lo) * factor)

    # ---- Maus
    def _activate(self):
        old = Knob.active
        Knob.active = self
        if old is not None and old is not self and old.winfo_exists():
            old._draw()
        self._draw()

    def _on_press(self, e):
        self._activate()
        self._y0 = e.y
        self._v0 = self.value

    def _on_drag(self, e):
        fine = 0.1 if (e.state & 0x1) else 1.0
        dy = self._y0 - e.y
        self.set(self._v0 + dy / 150.0 * (self.hi - self.lo) * fine)

    def _on_wheel(self, e):
        self._activate()
        steps = e.delta / 120.0
        fac = 0.002 if (e.state & 0x1) else 0.02
        if self.step:
            self.set(self.value + (1 if steps > 0 else -1) * self.step)
        else:
            self.set(self.value + steps * (self.hi - self.lo) * fac)
        return 'break'

    # ---- Anzeige
    def _fmt(self):
        if self.fmt:
            return self.fmt(self.value)
        rng = self.hi - self.lo
        if self.step:
            return f"{self.value:.0f}"
        dec = 0 if rng >= 100 else 1 if rng >= 10 else 2 if rng >= 1 else 3
        return f"{self.value:.{dec}f}"

    def _draw(self):
        import math
        self.delete('all')
        cx, cy, r = self.CX, self.CY, self.R
        is_active = Knob.active is self
        self.create_text(cx, 9, text=self.label, font=('Segoe UI', 8), fill='#222')
        track = '#1565c0' if is_active else '#bdbdbd'
        box = (cx - r, cy - r, cx + r, cy + r)
        self.create_arc(*box, start=225, extent=-270, style='arc', width=5, outline=track)
        frac = (self.value - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0.0
        if frac > 0.002:
            self.create_arc(*box, start=225, extent=-270 * frac, style='arc',
                            width=5, outline='#3a9d3a')
        ang = math.radians(225 - 270 * frac)
        self.create_line(cx, cy, cx + (r - 4) * math.cos(ang), cy - (r - 4) * math.sin(ang),
                         width=3, fill='#222', capstyle='round')
        self.create_text(cx, 77, text=self._fmt(), font=('Segoe UI', 9, 'bold'), fill='#222')


def install_knob_keys(root, enabled=None):
    """Pfeiltasten wirken auf den aktiven Regler (1%, Umschalt 0,1%, Strg 10%)."""
    text_widgets = (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, tk.Spinbox)

    def handler(e, direction):
        if enabled is not None and not enabled():
            return
        k = Knob.active
        if k is None or not k.winfo_exists():
            return
        try:
            fw = root.focus_get()
        except KeyError:
            fw = None
        if isinstance(fw, text_widgets):
            return
        factor = 0.001 if (e.state & 0x1) else (0.1 if (e.state & 0x4) else 0.01)
        k.nudge(direction, factor)
        return 'break'

    root.bind('<Up>', lambda e: handler(e, +1))
    root.bind('<Right>', lambda e: handler(e, +1))
    root.bind('<Down>', lambda e: handler(e, -1))
    root.bind('<Left>', lambda e: handler(e, -1))