"""fxcmd.py - Befehlscodes der Effekt-Spalte, abgeleitet aus der Effektkette.

01        Pan
11..      Effekt Nr. 1.. ein/aus (Reihenfolge von make_chain)
30..      Effektparameter, fortlaufend in Kettenfolge
Neue Effekte immer HINTEN an make_chain() haengen, sonst verschieben sich die Codes.
"""
from effects import make_chain

CODE_PAN = 1
CODE_FX_ON = 11
CODE_PARAM = 30
DISCRETE = {"mode", "stages", "bits", "down"}   # ganzzahlige Parameter: kein Uebergang

def _build():
    chain = make_chain()
    toggles = {CODE_FX_ON + i: i for i in range(len(chain))}
    params = {}
    code = CODE_PARAM
    for i, fx in enumerate(chain):
        for label, lo, hi, attr in fx.PARAMS:
            params[code] = (i, attr, float(lo), float(hi), label)
            code += 1
    return chain, toggles, params

_CHAIN, TOGGLES, PARAMS = _build()

def command_lines():
    lines = ["Code  Wirkung",
             "01    Pan: 00 = links, 50 = Mitte, 99 = rechts", ""]
    for code, i in TOGGLES.items():
        lines.append(f"{code:02d}    {_CHAIN[i].name} ein/aus (00 = aus, sonst ein)")
    lines.append("")
    for code, (i, _attr, lo, hi, label) in PARAMS.items():
        lines.append(f"{code:02d}    {_CHAIN[i].name}: {label}  ({lo:g} .. {hi:g})")
    lines += ["", "Wert 00-99 wird linear auf den Bereich min .. max abgebildet.",
              "Parameter gleiten in 20 ms zum neuen Wert, Schalter nutzen die weiche",
              "Rampe des Effekts. Stopp setzt alles auf die Patch-Werte zurück."]
    return lines