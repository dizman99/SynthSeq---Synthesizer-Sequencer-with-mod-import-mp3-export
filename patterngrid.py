"""patterngrid.py - Pattern-Raster (Tracker-Ansicht) als Canvas.

Pro Spur vier Zonen: Note | Lautstaerke | Code | Wert (Code+Wert = Effektbefehl).
Das Widget liest alles vom Modell (SequencerTab): model.pat, model.song, model.sel,
model.crow, model.field, model.root, model.on_grid_click(row, col),
model.sel_range(), model._eff_mute(i), model.solo.
"""
import os
import tkinter as tk
from tkinter import ttk

from config import note_label, NUM_TRACKS

ROW_H, LBL_W, HDR_H = 20, 44, 50
NOTE_W, VEL_W, CODE_W, VAL_W = 55, 22, 18, 18
COL_W = NOTE_W + VEL_W + CODE_W + VAL_W
W = LBL_W + NUM_TRACKS * COL_W
FIELD_X = (0, NOTE_W, NOTE_W + VEL_W, NOTE_W + VEL_W + CODE_W)
FIELD_W = (NOTE_W, VEL_W, CODE_W, VAL_W)
EMPTY_TXT = "--"          # leere Lautstaerke / Effekt (auf "00" aendern, wenn gewuenscht)
BG, BG_BEAT, BG_MUTE = "#1e1e1e", "#2a2a2a", "#141414"
FG, FG_DIM = "#e8e8e8", "#7a7a7a"
CURSOR, PLAY, SELECT = "#ffe14d", "#24527a", "#4a78b0"
HOLD, END = "#6a6a6a", "#9a9a9a"
VEL_FG, CODE_FG, VAL_FG, EMPTY_FG = "#8fb4ff", "#d9a066", "#f0d0a0", "#555555"
FONT, FONT_S = ("Consolas", 10), ("Segoe UI", 7)
TRACK_COLORS = ["#6cc0ff", "#ff9f6c", "#8be28b", "#e08be0",
                "#ffe66c", "#6cffe0", "#ff7c7c", "#b0b0ff"]


class PatternGrid(ttk.Frame):
    def __init__(self, parent, model):
        super().__init__(parent)
        self.model = model
        self.play_row = None
        self._play_id = None
        self._mode = None
        self._ev = None
        self._auto_id = None
        self.hdr = tk.Canvas(self, height=HDR_H, bg="#303030",
                             highlightthickness=0, takefocus=0)
        self.hdr.pack(side="top", fill="x")
        self.sbx = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.sbx.pack(side="bottom", fill="x")
        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self.cv = tk.Canvas(body, width=600, height=300, bg=BG, highlightthickness=0,
                            takefocus=0, yscrollincrement=ROW_H)
        self.sb = ttk.Scrollbar(body, orient="vertical", command=self.cv.yview)
        self.cv.configure(yscrollcommand=self.sb.set, xscrollcommand=self._xscroll)
        self.sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<B1-Motion>", self._motion)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.hdr.bind("<Button-1>", self._click_hdr)
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    # ---- horizontales Scrollen (Kopf und Raster laufen synchron)
    def _xview(self, *args):
        self.cv.xview(*args)

    def _xscroll(self, first, last):
        self.sbx.set(first, last)
        self.hdr.xview_moveto(first)

    def ensure_visible_x(self, col):
        w = self.cv.winfo_width()
        if w < 100:
            return
        left = self.cv.canvasx(0)
        x0 = LBL_W + col * COL_W
        x1 = x0 + COL_W
        if col == 0:
            if left > 0:
                self.cv.xview_moveto(0)
        elif x0 < left:
            self.cv.xview_moveto(x0 / W)
        elif x1 > left + w:
            self.cv.xview_moveto((x1 - w) / W)

    # ---- Zeichnen
    def draw_header(self):
        m = self.model
        h = self.hdr
        h.delete("all")
        h.configure(scrollregion=(0, 0, W, HDR_H))
        zones = (("Note", FIELD_X[0], NOTE_W), ("Vol", FIELD_X[1], VEL_W),
                 ("Fx", FIELD_X[2], CODE_W + VAL_W))
        for i, t in enumerate(m.song["tracks"]):
            x = LBL_W + i * COL_W
            sel = (i == m.sel)
            h.create_rectangle(x + 1, 1, x + COL_W - 1, HDR_H - 1,
                               fill="#3d5f8a" if sel else "#3a3a3a", outline="#555")
            mark = " S" if m.solo == i else (" M" if t["mute"] else "")
            h.create_text(x + COL_W / 2, 11, text=f"{i + 1}{mark}", fill=FG, font=FONT)
            name = os.path.splitext(os.path.basename(t["patch"] or "-"))[0][:16]
            h.create_text(x + COL_W / 2, 27, text=name, fill="#bbbbbb", font=FONT_S)
            for lbl, fx_, fw in zones:
                h.create_text(x + fx_ + fw / 2, 41, text=lbl, fill="#8a8a8a", font=FONT_S)

    def redraw(self):
        m = self.model
        pat, tracks = m.pat, m.song["tracks"]
        rpb = max(1, m.song["rows_per_beat"])
        rows = pat["rows"]
        cv = self.cv
        cv.delete("all")
        cv.configure(scrollregion=(0, 0, W, rows * ROW_H))
        for r in range(rows):
            y = r * ROW_H
            beat = (r % rpb == 0)
            cv.create_rectangle(0, y, W, y + ROW_H, fill=BG_BEAT if beat else BG, outline="")
            cv.create_text(LBL_W - 6, y + ROW_H / 2, text=f"{r:02d}", anchor="e",
                           fill=FG if beat else FG_DIM, font=FONT)
        for i in range(len(tracks)):
            x = LBL_W + i * COL_W
            if m._eff_mute(i):
                cv.create_rectangle(x, 0, x + COL_W, rows * ROW_H, fill=BG_MUTE, outline="")
            cv.create_line(x, 0, x, rows * ROW_H, fill="#333333")
            cv.create_line(x + FIELD_X[1], 0, x + FIELD_X[1], rows * ROW_H, fill="#2c2c2c")
            cv.create_line(x + FIELD_X[2], 0, x + FIELD_X[2], rows * ROW_H, fill="#2c2c2c")
            cv.create_line(x + FIELD_X[3], 0, x + FIELD_X[3], rows * ROW_H, fill="#242424")
        cv.create_line(W, 0, W, rows * ROW_H, fill="#333333")
        self._play_id = cv.create_rectangle(0, 0, W, ROW_H, fill=PLAY, outline="",
                                            state="hidden")
        cmds = {(c[0], c[1]): c for c in pat.get("cmds", [])}
        onsets = {(n[0], n[1]) for n in pat["notes"]}
        for r, tr, semi, ln, *rest in pat["notes"]:
            if not (0 <= r < rows and 0 <= tr < NUM_TRACKS):
                continue
            x0 = LBL_W + tr * COL_W
            xc = x0 + NOTE_W / 2
            yc = r * ROW_H + ROW_H / 2
            end = min(r + ln, rows)
            if end - r > 1:
                cv.create_line(xc, r * ROW_H + ROW_H - 2, xc, end * ROW_H - 2,
                               fill=HOLD, width=2)
            if r + ln < rows and (r + ln, tr) not in onsets:
                cv.create_line(xc - 9, (r + ln) * ROW_H + 3, xc + 9, (r + ln) * ROW_H + 3,
                               fill=END, width=2)
            cv.create_text(xc, yc, text=note_label(semi),
                           fill=TRACK_COLORS[tr % 8], font=FONT)
            v = rest[0] if rest else None
            cv.create_text(x0 + FIELD_X[1] + VEL_W / 2, yc,
                           text=EMPTY_TXT if v is None else f"{v:02d}",
                           fill=EMPTY_FG if v is None else VEL_FG, font=FONT)
            if (r, tr) not in cmds:
                cv.create_text(x0 + FIELD_X[2] + CODE_W / 2, yc, text=EMPTY_TXT,
                               fill=EMPTY_FG, font=FONT)
                cv.create_text(x0 + FIELD_X[3] + VAL_W / 2, yc, text=EMPTY_TXT,
                               fill=EMPTY_FG, font=FONT)
        for r, tr, code, val in pat.get("cmds", []):
            if not (0 <= r < rows and 0 <= tr < NUM_TRACKS):
                continue
            x0 = LBL_W + tr * COL_W
            yc = r * ROW_H + ROW_H / 2
            cv.create_text(x0 + FIELD_X[2] + CODE_W / 2, yc, text=f"{code:02d}",
                           fill=CODE_FG, font=FONT)
            cv.create_text(x0 + FIELD_X[3] + VAL_W / 2, yc, text=f"{val:02d}",
                           fill=VAL_FG, font=FONT)
        self.draw_cursor()
        self.set_play_row(self.play_row)

    def draw_cursor(self):
        m = self.model
        self.cv.delete("cursor")
        rows = m.pat["rows"]
        row = max(0, min(rows - 1, m.crow))
        colx = LBL_W + m.sel * COL_W
        y = row * ROW_H
        rg = m.sel_range()
        if rg:
            self.cv.create_rectangle(colx + 1, rg[0] * ROW_H, colx + COL_W - 1,
                                     (rg[1] + 1) * ROW_H, fill=SELECT, stipple="gray50",
                                     outline="", tags="cursor")
        f = max(0, min(3, getattr(m, "field", 0)))
        x = colx + FIELD_X[f]
        self.cv.create_rectangle(x + 1, y + 1, x + FIELD_W[f] - 1, y + ROW_H - 1,
                                 outline=CURSOR, width=2, tags="cursor")
        self.draw_header()
        self.ensure_visible(row)
        self.ensure_visible_x(m.sel)

    def set_play_row(self, r):
        self.play_row = r
        if self._play_id is None:
            return
        if r is None or r >= self.model.pat["rows"]:
            self.cv.itemconfigure(self._play_id, state="hidden")
            return
        self.cv.coords(self._play_id, 0, r * ROW_H, W, r * ROW_H + ROW_H)
        self.cv.itemconfigure(self._play_id, state="normal")
        self.follow(r)

    def draw_ghost(self, col, top, n, ok, color):
        self.cv.delete("ghost")
        if not ok:
            return
        x = LBL_W + col * COL_W
        self.cv.create_rectangle(x + 1, top * ROW_H, x + COL_W - 1, (top + n) * ROW_H,
                                 outline=color, width=2, dash=(4, 3), tags="ghost")

    # ---- Scrollen (vertikal)
    def _view(self):
        total = max(1, self.model.pat["rows"] * ROW_H)
        return total, self.cv.canvasy(0), self.cv.winfo_height()

    def ensure_visible(self, row):
        total, top, h = self._view()
        if h < 40:
            return
        y0, y1 = row * ROW_H, row * ROW_H + ROW_H
        if y0 < top:
            self.cv.yview_moveto(y0 / total)
        elif y1 > top + h:
            self.cv.yview_moveto(max(0, y1 - h) / total)

    def follow(self, row):
        total, top, h = self._view()
        if h < 40:
            return
        if row * ROW_H < top or (row + 2) * ROW_H > top + h:
            self.cv.yview_moveto(max(0, row - 2) * ROW_H / total)

    def _wheel(self, e):
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except Exception:
            return
        while w is not None:
            if w is self.cv:
                if e.state & 0x1:
                    self.cv.xview_scroll(int(-e.delta / 120) * 3, "units")
                else:
                    self.cv.yview_scroll(int(-e.delta / 120) * 3, "units")
                return
            w = w.master

    # ---- Maus
    def _xy(self, e):
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        return int(y // ROW_H), int((x - LBL_W) // COL_W)

    def _where(self, e):
        rows = self.model.pat["rows"]
        row, col = self._xy(e)
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        over = max(-e.y, e.y - h, 0)
        inside = 0 <= e.x < w and over <= 40
        return (max(0, min(rows - 1, row)), max(0, min(NUM_TRACKS - 1, col)), inside)

    def _click(self, e):
        row, col = self._xy(e)
        self._mode = None
        self._ev = e
        if 0 <= row < self.model.pat["rows"] and 0 <= col < NUM_TRACKS:
            off = self.cv.canvasx(e.x) - LBL_W - col * COL_W
            self.model.field = max(k for k in range(4) if off >= FIELD_X[k])
            self._mode = self.model.mouse_press(row, col, e.state)
        self.model.root.after_idle(self.model.root.focus_set)

    def _apply(self):
        row, col, inside = self._where(self._ev)
        if self._mode == "select":
            self.model.mouse_select(row)
        elif self._mode == "drag":
            self.model.mouse_ghost(col, row, inside)

    def _motion(self, e):
        if self._mode is None:
            return
        self._ev = e
        self._apply()
        if (e.y < 0 or e.y > self.cv.winfo_height()) and self._auto_id is None:
            self._auto_id = self.cv.after(60, self._autoscroll)

    def _autoscroll(self):
        self._auto_id = None
        e = self._ev
        if self._mode is None or e is None:
            return
        if e.y < 0:
            self.cv.yview_scroll(-1, "units")
        elif e.y > self.cv.winfo_height():
            self.cv.yview_scroll(1, "units")
        else:
            return
        self._apply()
        self._auto_id = self.cv.after(60, self._autoscroll)

    def _release(self, e):
        mode, self._mode = self._mode, None
        if self._auto_id is not None:
            self.cv.after_cancel(self._auto_id)
            self._auto_id = None
        self.cv.delete("ghost")
        if mode == "drag":
            row, col, inside = self._where(e)
            self.model.mouse_drop(col, row, inside)
        self.model.root.after_idle(self.model.root.focus_set)

    def _click_hdr(self, e):
        col = int((self.hdr.canvasx(e.x) - LBL_W) // COL_W)
        if 0 <= col < NUM_TRACKS:
            self.model.on_grid_click(self.model.crow, col)
        self.model.root.after_idle(self.model.root.focus_set)

