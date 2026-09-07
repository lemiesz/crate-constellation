#!/usr/bin/env python
"""Import the rekordbox library into library.db (read-only against master.db).

One-time / re-runnable seeding step: wipes and rebuilds library.db content
tables from the current master.db state. Reads only — master.db is never
touched, so it is safe to run while rekordbox is open.

Usage:  python import_rekordbox.py [--db PATH]   (default: ../library.db)
"""

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

from pyrekordbox import Rekordbox6Database
from sqlalchemy import text as sa_text
from pyrekordbox.db6 import tables

HERE = Path(__file__).resolve().parent
ENERGY_RE = re.compile(r"^E(10|[1-9])$")

import unicodedata

def norm(s: str | None) -> str:
    """Fold to lowercase alphanumerics: 'Jay-Z' -> 'jayz', 'Sébastien' -> 'sebastien'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())

BANK_NAMES = {"Genre": "Genre", "Energy": "Energy", "Vibe": "Vibe", "Label": "Label"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(HERE.parent / "library.db"))
    args = ap.parse_args()

    t0 = time.time()
    rb = Rekordbox6Database()

    out = sqlite3.connect(args.db)
    out.executescript((HERE / "schema.sql").read_text())
    for t in ("track_tags", "playlist_tracks", "files", "tracks", "tags",
              "playlists", "cues"):
        out.execute(f"DELETE FROM {t}")

    # --- reference maps -------------------------------------------------
    labels = {l.ID: l.Name for l in rb.query(tables.DjmdLabel).all()}
    keys = {k.ID: k.ScaleName for k in rb.query(tables.DjmdKey).all()}

    # My Tag banks: root rows are the banks, children are the tags
    banks, tag_rows = {}, []
    for t in rb.query(tables.DjmdMyTag).all():
        if t.ParentID == "root":
            banks[t.ID] = t.Name
    for t in rb.query(tables.DjmdMyTag).all():
        bank = banks.get(t.ParentID)
        if bank:
            tag_rows.append((t.ID, bank, t.Name))
    out.executemany("INSERT INTO tags VALUES (?,?,?)", tag_rows)
    tag_name = {tid: name for tid, _, name in tag_rows}

    # --- tracks + files -------------------------------------------------
    n_tracks = 0
    track_ids = set()
    for c in rb.get_content():
        bpm = (c.BPM or 0) / 100.0 or None
        path = c.FolderPath or ""
        volume = path.split("/")[2] if path.startswith("/Volumes/") else "local"
        basename = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] if path else ""
        blob = "|".join(norm(x) for x in
                        (c.Title, c.ArtistName, c.AlbumName, labels.get(c.LabelID),
                         c.Commnt, basename) if x)
        out.execute(
            "INSERT INTO tracks (id,title,artist,album,label,year,bpm,key,rating,"
            "comment,length,added_at,search_blob) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (c.ID, c.Title, c.ArtistName, c.AlbumName, labels.get(c.LabelID),
             c.ReleaseYear or None, bpm, keys.get(c.KeyID), c.Rating,
             c.Commnt, c.Length, str(c.created_at or ""), blob),
        )
        out.execute("INSERT INTO files VALUES (?,?,?,?)",
                    (c.ID, path, volume, c.AnalysisDataPath))
        track_ids.add(c.ID)
        n_tracks += 1

    # --- track_tags + energy from E-tags --------------------------------
    tt = []
    energy = {}
    for r in rb.query(tables.DjmdSongMyTag).all():
        if r.ContentID in track_ids and r.MyTagID in tag_name:
            tt.append((r.ContentID, r.MyTagID))
            m = ENERGY_RE.match(tag_name[r.MyTagID])
            if m:
                energy[r.ContentID] = int(m.group(1))
    out.executemany("INSERT OR IGNORE INTO track_tags VALUES (?,?)", tt)
    out.executemany("UPDATE tracks SET energy=? WHERE id=?",
                    [(e, tid) for tid, e in energy.items()])

    # --- playlists ------------------------------------------------------
    kind_of = {0: "playlist", 1: "folder", 4: "smart"}
    n_pl = 0
    for p in rb.query(tables.DjmdPlaylist).all():
        if getattr(p, "rb_local_deleted", 0):
            continue
        out.execute(
            "INSERT INTO playlists VALUES (?,?,?,?,?,?)",
            (p.ID, p.Name, None if p.ParentID == "root" else p.ParentID,
             kind_of.get(p.Attribute, "playlist"), p.Seq, p.SmartList),
        )
        n_pl += 1
    pt = [(r.PlaylistID, r.ContentID, r.TrackNo)
          for r in rb.query(tables.DjmdSongPlaylist).all()
          if r.ContentID in track_ids]
    out.executemany("INSERT OR IGNORE INTO playlist_tracks VALUES (?,?,?)", pt)

    # --- embeddings (discogs-effnet 1280-dim, from the analysis pipeline) ---
    feat_db = Path.home() / "Music/library-management/features_v2.sqlite"
    n_emb = 0
    if feat_db.exists():
        out.execute("DELETE FROM embeddings")
        src = sqlite3.connect(f"file:{feat_db}?mode=ro", uri=True)
        for rb_id, model, dim, vec in src.execute(
                "SELECT rb_id, model, dim, vec FROM embedding"):
            if rb_id in track_ids:
                out.execute("INSERT INTO embeddings VALUES (?,?,?,?)",
                            (rb_id, model, dim, vec))
                n_emb += 1
        src.close()

    out.execute("INSERT OR REPLACE INTO meta VALUES ('imported_at', datetime('now'))")
    out.execute("INSERT OR REPLACE INTO meta VALUES ('source', 'rekordbox master.db')")
    # --- cues -----------------------------------------------------------
    # One bulk read: ~200k rows in about a second. Per-track queries would be
    # 19,000 round trips for the same data.
    cue_rows = [(str(c[0]), c[1], c[2], c[3], c[4], c[5], c[6], c[7])
                for c in rb.session.execute(sa_text(
                    "SELECT ContentID, Kind, InMsec, OutMsec, Color, "
                    "ColorTableIndex, Comment, BeatLoopSize FROM djmdCue "
                    "WHERE rb_local_deleted = 0 AND ContentID IS NOT NULL"))]
    out.executemany("INSERT INTO cues (track_id, kind, in_msec, "
                    "out_msec, color, color_idx, comment, loop_beats) "
                    "VALUES (?,?,?,?,?,?,?,?)", cue_rows)
    print(f"  cues: {len(cue_rows)}")

    out.commit()
    out.close()

    print(f"imported {n_tracks} tracks, {len(tt)} tag links, {n_pl} playlists, "
          f"{len(pt)} playlist entries, {len(tag_rows)} tags, {n_emb} embeddings "
          f"in {time.time() - t0:.1f}s -> {args.db}")


if __name__ == "__main__":
    sys.exit(main())
