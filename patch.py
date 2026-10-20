import json
import os

from config import PATCH_VERSION


def sample_reference(sample_path, patch_path):
    ap = os.path.abspath(sample_path)
    try:
        rel = os.path.relpath(ap, os.path.dirname(os.path.abspath(patch_path)))
    except ValueError:
        rel = None
    return {"file": rel, "file_abs": ap}


def resolve_sample(sample, patch_path):
    base = os.path.dirname(os.path.abspath(patch_path))
    cands = []
    if sample.get("file"):
        cands.append(os.path.join(base, sample["file"]))
    if sample.get("file_abs"):
        cands.append(sample["file_abs"])
    return next((c for c in cands if os.path.isfile(c)), None)


def validate_patch(d):
    if d.get("format") != "synth-patch":
        raise ValueError("Das ist keine Patch-Datei.")
    if int(d.get("version", 0)) > PATCH_VERSION:
        raise ValueError("Patch stammt aus einer neueren Programmversion.")


def read_patch(path):
    with open(path, 'r', encoding='utf-8') as f:
        d = json.load(f)
    validate_patch(d)
    return d


def write_patch(path, d):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)