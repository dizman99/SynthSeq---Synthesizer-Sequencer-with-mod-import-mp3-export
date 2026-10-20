"""patternedit.py - Bearbeitungsregeln fuer Pattern-Noten (GUI-frei, testbar).

Eine Note ist [row, track, semi, length_rows]. Pro Spur gilt die Amiga-Regel:
eine neue Note beendet die vorherige (die Engine laesst sie ueber ihre Release-Zeit
ausklingen, es gibt also keinen harten Abbruch).
"""


def covering(notes, row, tr):
    """Note der Spur tr, die in Zeile row klingt (Beginn <= row < Beginn+Laenge) oder None."""
    best = None
    for n in notes:
        if n[1] == tr and n[0] <= row < n[0] + n[3]:
            if best is None or n[0] > best[0]:
                best = n
    return best

def set_note(notes, rows, row, tr, semi):
    """Note setzen. Laenge: bis zur naechsten Note der Spur oder zum Patternende.
    Eine an derselben Stelle ersetzte Note behaelt ihre Lautstaerke."""
    old = next((n for n in notes if n[0] == row and n[1] == tr), None)
    vel = old[4] if old is not None and len(old) > 4 else None
    notes[:] = [n for n in notes if not (n[0] == row and n[1] == tr)]
    for n in notes:
        if n[1] == tr and n[0] < row < n[0] + n[3]:
            n[3] = row - n[0]
    nxt = min((n[0] for n in notes if n[1] == tr and n[0] > row), default=rows)
    notes.append([row, tr, semi, max(1, nxt - row), vel])
    notes.sort()

def clear_note(notes, row, tr):
    """Die Note entfernen, die in (row, tr) klingt. Gibt sie zurueck oder None."""
    n = covering(notes, row, tr)
    if n is not None:
        notes.remove(n)
    return n


def set_end(notes, row, tr):
    """Ende-Kennzeichen: die klingende Note endet vor Zeile row. True bei Aenderung."""
    n = covering(notes, row, tr)
    if n is None or n[0] >= row:
        return False
    n[3] = row - n[0]
    return True

def clear_track(notes, tr):
    """Alle Noten der Spur tr entfernen. Gibt die Anzahl zurueck."""
    n0 = len(notes)
    notes[:] = [n for n in notes if n[1] != tr]
    return n0 - len(notes)

def clear_all(notes):
    n0 = len(notes)
    notes.clear()
    return n0

def transpose_note(notes, row, tr, d):
    """Die in (row, tr) klingende Note um d Halbtoene verschieben. Gibt sie zurueck oder None."""
    n = covering(notes, row, tr)
    if n is None:
        return None
    n[2] = max(-36, min(48, n[2] + d))
    return n

def clear_range(notes, tr, r0, r1):
    """Zeilen r0..r1 (einschl.) der Spur leeren; eine davor beginnende Note wird vor r0 gekuerzt."""
    notes[:] = [n for n in notes if not (n[1] == tr and r0 <= n[0] <= r1)]
    for n in notes:
        if n[1] == tr and n[0] < r0 < n[0] + n[3]:
            n[3] = r0 - n[0]

def copy_range(notes, tr, r0, r1):
    """Noten der Spur mit Beginn in r0..r1 als [rel_zeile, semi, laenge]; Laenge bis r1 gekuerzt."""
    out = []
    for n in sorted(notes):
        if n[1] == tr and r0 <= n[0] <= r1:
            out.append([n[0] - r0, n[2], min(n[3], r1 + 1 - n[0]), n[4] if len(n) > 4 else None])
    return out

def paste(notes, rows, tr, row, n_rows, clip):
    """Clip ab row in Spur tr einfuegen; der Zielbereich wird dabei ueberschrieben."""
    end = min(rows, row + n_rows)
    if row >= end:
        return 0
    clear_range(notes, tr, row, end - 1)
    cnt = 0
    for rr, semi, ln, *rest in clip:
        r = row + rr
        if r < end:
            notes.append([r, tr, semi, max(1, min(ln, end - r)), rest[0] if rest else None])
            cnt += 1
    notes.sort()
    return cnt

def set_vel(notes, row, tr, vel):
    """Lautstaerke (0..99 oder None) der in (row, tr) klingenden Note setzen."""
    n = covering(notes, row, tr)
    if n is None:
        return None
    while len(n) < 5:
        n.append(None)
    n[4] = vel
    return n

def cmd_at(cmds, row, tr):
    for c in cmds:
        if c[0] == row and c[1] == tr:
            return c
    return None

def set_cmd(cmds, row, tr, code=None, value=None):
    """Befehl anlegen (Code 0, Wert 0) und Code und/oder Wert setzen."""
    c = cmd_at(cmds, row, tr)
    if c is None:
        c = [row, tr, 0, 0]
        cmds.append(c)
        cmds.sort()
    if code is not None:
        c[2] = code
    if value is not None:
        c[3] = value
    return c

def clear_cmd(cmds, row, tr):
    c = cmd_at(cmds, row, tr)
    if c is None:
        return False
    cmds.remove(c)
    return True

def clear_cmd_range(cmds, tr, r0, r1):
    cmds[:] = [c for c in cmds if not (c[1] == tr and r0 <= c[0] <= r1)]

def copy_cmd_range(cmds, tr, r0, r1):
    return [[c[0] - r0, c[2], c[3]] for c in sorted(cmds)
            if c[1] == tr and r0 <= c[0] <= r1]

def paste_cmds(cmds, rows, tr, row, n_rows, clip):
    end = min(rows, row + n_rows)
    if row >= end:
        return 0
    clear_cmd_range(cmds, tr, row, end - 1)
    cnt = 0
    for rr, code, val in clip:
        r = row + rr
        if r < end:
            cmds.append([r, tr, code, val])
            cnt += 1
    cmds.sort()
    return cnt

