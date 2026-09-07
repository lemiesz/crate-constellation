-- Crate Constellation · library.db — the app's canonical store.
-- Source of truth once populated; rekordbox/Traktor/USB are sync targets.
-- Phase 0/1 subset: identity, files, tags (4 banks), playlists. Features,
-- embeddings (sqlite-vec), sync_state and edit_journal land in later phases.

PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
  id       TEXT PRIMARY KEY,        -- rekordbox ContentID for now (stable, unique)
  title    TEXT,
  artist   TEXT,
  album    TEXT,
  label    TEXT,
  year     INTEGER,
  bpm      REAL,                    -- curated BPM (halftime-halved values included)
  key      TEXT,
  rating   INTEGER,
  comment  TEXT,                    -- carries the #hashtag mirror
  length   REAL,                    -- seconds
  energy   INTEGER,                 -- 1-10, parsed from the E-tag
  added_at TEXT,
  search_blob TEXT                  -- normalized (lower, no diacritics/punct) haystack for fuzzy search
);

CREATE TABLE IF NOT EXISTS files (
  track_id  TEXT PRIMARY KEY REFERENCES tracks(id),
  path      TEXT,
  volume    TEXT,
  anlz_path TEXT                    -- rekordbox ANLZ .DAT (waveform/beatgrid), relative to share/
);

CREATE TABLE IF NOT EXISTS tags (
  id   TEXT PRIMARY KEY,            -- rekordbox MyTag ID
  bank TEXT NOT NULL,               -- Genre | Energy | Vibe | Label (hard 4-bank limit)
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS track_tags (
  track_id TEXT NOT NULL REFERENCES tracks(id),
  tag_id   TEXT NOT NULL REFERENCES tags(id),
  PRIMARY KEY (track_id, tag_id)
);

CREATE TABLE IF NOT EXISTS playlists (
  id        TEXT PRIMARY KEY,       -- rekordbox PlaylistID
  name      TEXT NOT NULL,
  parent_id TEXT,                   -- NULL = root
  kind      TEXT NOT NULL,          -- folder | playlist | smart
  seq       INTEGER,
  smart_xml TEXT                    -- rekordbox SmartList XML, verbatim
);

CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id TEXT NOT NULL REFERENCES playlists(id),
  track_id    TEXT NOT NULL REFERENCES tracks(id),
  seq         INTEGER,
  PRIMARY KEY (playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS embeddings (
  track_id TEXT PRIMARY KEY REFERENCES tracks(id),
  model    TEXT NOT NULL,           -- version-stamped: never mix models in one similarity space
  dim      INTEGER NOT NULL,
  vec      BLOB NOT NULL            -- float32[dim]
);

CREATE TABLE IF NOT EXISTS viz_cache (
  track_id TEXT PRIMARY KEY REFERENCES tracks(id),
  x        REAL NOT NULL,           -- UMAP 2D layout
  y        REAL NOT NULL,
  cluster  TEXT,
  genre    TEXT                     -- effnet-derived genre family for coloring
);

CREATE TABLE IF NOT EXISTS edit_journal (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL DEFAULT (datetime('now')),
  entity    TEXT NOT NULL,          -- track | tag-link
  entity_id TEXT NOT NULL,
  field     TEXT NOT NULL,          -- energy | tag:add | tag:remove
  old_value TEXT,
  new_value TEXT,
  actor     TEXT NOT NULL DEFAULT 'user'
);

CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS idx_tracks_bpm    ON tracks(bpm);
CREATE INDEX IF NOT EXISTS idx_tt_tag        ON track_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_pt_playlist   ON playlist_tracks(playlist_id);

-- Hot cues and memory cues, mirrored from rekordbox so a sync never has to
-- open its database. rekordbox keeps these in its own tables and only
-- materializes them into the analysis files at export time, which is why a
-- stick built by copying those files carries none.
CREATE TABLE IF NOT EXISTS cues (
  track_id   TEXT NOT NULL REFERENCES tracks(id),
  kind       INTEGER NOT NULL,     -- 0 = memory cue, 1..8 = hot cue slots A..H
  in_msec    INTEGER,              -- position, milliseconds
  out_msec   INTEGER,              -- loop end, -1 or NULL when not a loop
  color      INTEGER,
  color_idx  INTEGER,
  comment    TEXT,
  loop_beats INTEGER
  -- deliberately no primary key: (track_id, kind, in_msec) is NOT unique in
  -- rekordbox and using it as one silently discarded 20,000 of 202,000 cues
);
CREATE INDEX IF NOT EXISTS idx_cues_track ON cues(track_id);
