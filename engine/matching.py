"""Matching pairs computed over the tracks that are actually on a device.

The library-wide pair set is built once over everything, so filtering it down
to a device throws away almost all of it: a stick holding a fraction of the
library keeps only the pairs where BOTH tracks happen to be on it, which for
a small selection is close to none. A partial sync then ships a device with
no Matching at all.

This computes the pairs from scratch over just the tracks present, so every
device gets a Matching list drawn from what it actually holds.

The rules are the library builder's, unchanged, so a device's pairs are the
ones the same algorithm would pick:

  - cosine >= 0.90 on the full embedding. 0.85 is not selective in this
    space; random pairs sit around 0.87.
  - BPM within 8% at 1:1, 2:1 or 1:2, on the curated (halftime-corrected) BPM
  - Camelot compatibility is a RANKING BONUS, never a filter
  - near-identical vectors and repeat pressings of the same track are skipped,
    and one partner may occupy only one slot
  - adaptive slot count: sparse tracks keep the base allowance, dense ones
    earn a slot per `dense_div` candidates up to `top_max`
"""

import re
import sqlite3
from pathlib import Path

CAMELOT = {"Abm": (1, "A"), "B": (1, "B"), "Ebm": (2, "A"), "F#": (2, "B"),
           "Bbm": (3, "A"), "Db": (3, "B"), "Fm": (4, "A"), "Ab": (4, "B"),
           "Cm": (5, "A"), "Eb": (5, "B"), "Gm": (6, "A"), "Bb": (6, "B"),
           "Dm": (7, "A"), "F": (7, "B"), "Am": (8, "A"), "C": (8, "B"),
           "Em": (9, "A"), "G": (9, "B"), "Bm": (10, "A"), "D": (10, "B"),
           "F#m": (11, "A"), "A": (11, "B"), "Dbm": (12, "A"), "E": (12, "B")}


def key_ok(k1, k2) -> bool:
    a, b = CAMELOT.get(k1), CAMELOT.get(k2)
    if not a or not b:
        return False
    (n1, l1), (n2, l2) = a, b
    if n1 == n2:                       # same key or its relative
        return True
    return l1 == l2 and min((n1 - n2) % 12, (n2 - n1) % 12) == 1


def bpm_ok(b1, b2) -> bool:
    if not b1 or not b2:
        return False
    return any(abs(b1 - b2 * r) / (b2 * r) <= 0.08 for r in (1.0, 2.0, 0.5))


def stick_pairs(library_db: Path, track_ids, *, sim: float = 0.90, top: int = 6,
                top_max: int = 20, dense_div: int = 12, log=None):
    """Pairs among `track_ids` only. Returns [(id_a, id_b, similarity)]."""
    import numpy as np

    say = log or (lambda _m: None)
    wanted = list(dict.fromkeys(str(t) for t in track_ids))
    if len(wanted) < 2:
        return []

    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    meta = {r["id"]: (r["bpm"], r["key"],
                      re.sub(r"[^a-z0-9]+", "",
                             f"{r['artist'] or ''} - {r['title'] or ''}".lower()))
            for r in con.execute("SELECT id, bpm, key, artist, title FROM tracks")}
    ids, vecs = [], []
    for tid in wanted:
        row = con.execute("SELECT vec FROM embeddings WHERE track_id = ?",
                          (tid,)).fetchone()
        if row is None or tid not in meta:
            continue
        v = np.frombuffer(row["vec"], dtype=np.float32)
        ids.append(tid)
        vecs.append(v / (np.linalg.norm(v) + 1e-9))
    con.close()
    if len(ids) < 2:
        say(f"matching: only {len(ids)} of {len(wanted)} tracks have embeddings")
        return []

    X = np.stack(vecs)
    n = len(ids)
    bpm = np.array([meta[i][0] or 0.0 for i in ids])
    keys = [meta[i][1] for i in ids]
    label = [meta[i][2] for i in ids]

    pairs: dict[tuple[int, int], float] = {}
    CH = 512
    for s in range(0, n, CH):
        S = X[s:s + CH] @ X.T
        for r in range(S.shape[0]):
            i = s + r
            row = S[r]
            row[i] = -1
            cand = np.where(row >= sim)[0]
            if not len(cand):
                continue
            ok = [j for j in cand
                  if bpm_ok(bpm[i], bpm[j]) and row[j] < 0.997
                  and label[i] != label[j]]
            if not ok:
                continue
            kcap = min(max(top, len(ok) // dense_div), top_max)
            scored = sorted(((row[j] + (0.02 if key_ok(keys[i], keys[j]) else 0.0), j)
                             for j in ok), reverse=True)
            seen, picked = set(), 0
            for _, j in scored:
                if picked >= kcap:
                    break
                if label[j] in seen:
                    continue
                seen.add(label[j])
                picked += 1
                a, b = (i, j) if i < j else (j, i)
                pairs[(a, b)] = max(pairs.get((a, b), 0.0), float(row[j]))

    out = [(ids[a], ids[b], sim_v) for (a, b), sim_v in pairs.items()]
    covered = len({x for p in out for x in p[:2]})
    say(f"matching: {len(out)} pairs among {n} tracks on this device "
        f"({covered} tracks have at least one match)")
    return out
