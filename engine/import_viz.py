#!/usr/bin/env python
"""Copy the precomputed UMAP layout (viz_arrays.npz + viz_meta.csv) into
library.db viz_cache. Refresh of the layout itself stays with the analysis
pipeline until the analyzer registry lands."""
import csv
import sqlite3
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "library.db"
LM = Path.home() / "Music/library-management"

a = np.load(LM / "viz_arrays.npz")
xy = a["xy"]
meta = list(csv.DictReader(open(LM / "viz_meta.csv")))
assert len(meta) == len(xy), (len(meta), len(xy))

con = sqlite3.connect(DB)
con.executescript((HERE / "schema.sql").read_text())
con.execute("DELETE FROM viz_cache")
known = {r[0] for r in con.execute("SELECT id FROM tracks")}
rows = [(m["rb_id"], float(x), float(y), m["cluster"], m["genre"])
        for m, (x, y) in zip(meta, xy) if m["rb_id"] in known]
con.executemany("INSERT INTO viz_cache VALUES (?,?,?,?,?)", rows)
con.commit()
print(f"viz_cache: {len(rows)} points")
