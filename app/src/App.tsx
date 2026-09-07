import { useEffect, useMemo, useRef, useState } from "react";
import Constellation from "./Constellation";
import { BackupsView, EnergyView, SyncView, UsbView } from "./Views";
import SyncDrawer from "./SyncDrawer";

type Tag = { bank: string; name: string };
type Track = {
  id: string; title: string | null; artist: string | null; album: string | null;
  label: string | null; year: number | null; bpm: number | null; key: string | null;
  rating: number; energy: number | null; length: number | null; comment: string | null;
  path: string | null; volume: string | null; tags: Tag[];
};
type Playlist = { id: string; name: string; parent_id: string | null; kind: string; seq: number; n: number };
type Stats = { tracks: number; playlists: number; energy_tagged: number; imported_at: string };

const fmtLen = (s: number | null) =>
  s == null ? "—" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`;

/** Track title falls back to the filename for files with empty ID3 titles. */
const displayTitle = (t: Track) =>
  t.title || (t.path ? t.path.split("/").pop()!.replace(/\.[a-z0-9]+$/i, "") : "—");

type Wave = { heights: number[]; colors: number[] };

/** rekordbox-style preview waveform (ANLZ PWAV): blue bars, whiter = brighter
 *  high end; played portion full-strength, unplayed dimmed, red playhead. */
function Waveform({ wave, progress, onSeek }: {
  wave: Wave | null; progress: number; onSeek: (frac: number) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    if (!wave) {
      g.fillStyle = "rgba(255,255,255,.08)";
      g.fillRect(0, h - 2, w, 2);
      if (progress > 0) { g.fillStyle = "#e5484d"; g.fillRect(progress * w - 1, 0, 2, h); }
      return;
    }
    const n = wave.heights.length;
    const bw = w / n;
    for (let i = 0; i < n; i++) {
      const hh = Math.max(1, (wave.heights[i] / 31) * (h - 2));
      const c = Math.min(7, wave.colors[i]) / 7;           // 0 = deep blue, 1 = near white
      const played = i / n <= progress;
      const r = Math.round(43 + c * 190), gg = Math.round(114 + c * 125), b = Math.round(224 + c * 31);
      g.fillStyle = `rgba(${r},${gg},${b},${played ? 1 : 0.45})`;
      g.fillRect(i * bw, h - hh, Math.max(1, bw - 0.4), hh);
    }
    g.fillStyle = "#e5484d";
    g.fillRect(progress * w - 1, 0, 2, h);
  }, [wave, progress]);
  return (
    <canvas
      ref={ref}
      className="wavecv"
      onClick={e => {
        const r = (e.target as HTMLCanvasElement).getBoundingClientRect();
        onSeek((e.clientX - r.left) / r.width);
      }}
    />
  );
}

type Ctx = { x: number; y: number; track: Track };

function ContextMenu({ ctx, onClose, onPlay, onJump, onReveal, onCopy, onConstellation }: {
  ctx: Ctx; onClose: () => void; onPlay: () => void; onJump: () => void;
  onReveal: () => void; onCopy: () => void; onConstellation: () => void;
}) {
  useEffect(() => {
    const close = () => onClose();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("click", close);
    window.addEventListener("keydown", esc);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", esc);
      window.removeEventListener("blur", close);
    };
  }, [onClose]);
  const x = Math.min(ctx.x, window.innerWidth - 230);
  const y = Math.min(ctx.y, window.innerHeight - 190);
  return (
    <div className="ctx" style={{ left: x, top: y }} onClick={e => e.stopPropagation()}>
      <div className="ctxhd">{displayTitle(ctx.track)}</div>
      <button className="ctxit" onClick={() => { onConstellation(); onClose(); }}>
        <span>✦ Open in constellation</span>
      </button>
      <div className="ctxsep" />
      <button className="ctxit" onClick={() => { onPlay(); onClose(); }}>Play</button>
      <button className="ctxit" onClick={() => { onJump(); onClose(); }}>Jump to in library</button>
      <div className="ctxsep" />
      <button className="ctxit" onClick={() => { onReveal(); onClose(); }}>Reveal in Finder</button>
      <button className="ctxit" onClick={() => { onCopy(); onClose(); }}>Copy file path</button>
    </div>
  );
}

function Icon({ d, on, title, extra, onClick }: {
  d: string; on?: boolean; title: string; extra?: string; onClick?: () => void;
}) {
  return (
    <button className={`navi${on ? " on" : ""}${extra ? " " + extra : ""}`} title={title} onClick={onClick}>
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor"
        strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d={d} />
      </svg>
    </button>
  );
}

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [playlists, setPlaylists] = useState<Playlist[]>([]);
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [playlist, setPlaylist] = useState<Playlist | null>(null);
  const [tracks, setTracks] = useState<Track[]>([]);
  const [total, setTotal] = useState(0);
  const [sel, setSel] = useState<Track | null>(null);
  const [openFolders, setOpenFolders] = useState<Set<string>>(new Set());
  const [playing, setPlaying] = useState<Track | null>(null);
  const [similar, setSimilar] = useState<(Track & { sim: number })[] | null>(null);

  useEffect(() => {
    setSimilar(null);
    if (!sel) return;
    let stale = false;
    fetch(`/api/similar/${sel.id}?limit=10`).then(r => r.json())
      .then(d => { if (!stale) setSimilar(d.rows ?? []); });
    return () => { stale = true; };
  }, [sel?.id]);

  const openTrack = (id: string) =>
    fetch(`/api/track/${id}`).then(r => r.json()).then(setSel);

  const [view, setView] = useState<"library" | "constellation" | "energy" | "backups" | "sync" | "usb">("library");
  const [tagFilter, setTagFilter] = useState<string | null>(null);
  const browseTag = (name: string) => {
    setTagFilter(name); setQuery(""); setPlaylist(null); setView("library");
  };
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null);
  const openInConstellation = (id: string) => {
    setFocus({ id, n: Date.now() });
    setView("constellation");
  };
  const playById = (id: string) => fetch(`/api/track/${id}`).then(r => r.json()).then(setPlaying);

  const [catalog, setCatalog] = useState<{ id: string; bank: string; name: string; n: number }[]>([]);
  useEffect(() => { fetch("/api/tags").then(r => r.json()).then(d => setCatalog(d.rows)); }, []);
  const [addBank, setAddBank] = useState<string | null>(null);

  const refreshSel = (id: string) => {
    openTrack(id);
    setTracks(ts => ts.map(t => t.id === id ? t : t));  // detail is source; row refresh below
    fetch(`/api/track/${id}`).then(r => r.json()).then(full =>
      setTracks(ts => ts.map(t => (t.id === id ? { ...t, ...full } : t))));
  };
  const addTag = (trackId: string, tagId: string) =>
    fetch(`/api/tracks/${trackId}/tags`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ tag_id: tagId }),
    }).then(() => { setAddBank(null); refreshSel(trackId); });
  const removeTag = (trackId: string, tagId: string) =>
    fetch(`/api/tracks/${trackId}/tags/${tagId}`, { method: "DELETE" })
      .then(() => refreshSel(trackId));
  const setEnergyVal = (trackId: string, v: number) =>
    fetch(`/api/tracks/${trackId}/energy`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ value: v }),
    }).then(() => refreshSel(trackId));

  const [ctx, setCtx] = useState<Ctx | null>(null);
  const onRowCtx = (e: React.MouseEvent, t: Track) => {
    e.preventDefault();
    setSel(t);
    setCtx({ x: e.clientX, y: e.clientY, track: t });
  };

  // neighbor click: reveal the track in the library table and scroll to it
  const [jumpTo, setJumpTo] = useState<string | null>(null);
  const jumpToTrack = (n: Track) => {
    if (view === "constellation") {
      // stay in the galaxy: glide to the neighbor instead of leaving the map
      setFocus({ id: n.id, n: Date.now() });
      return;
    }
    setPlaylist(null);
    setQuery(displayTitle(n));
    setJumpTo(n.id);
    openTrack(n.id);
  };
  useEffect(() => {
    if (!jumpTo) return;
    const el = document.getElementById(`row-${jumpTo}`);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      setJumpTo(null);
    }
  }, [tracks, jumpTo]);
  const [paused, setPaused] = useState(false);
  const [wave, setWave] = useState<Wave | null>(null);
  const [time, setTime] = useState(0);
  const [dur, setDur] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const a = audioRef.current;
    if (!a || !playing) return;
    setWave(null); setTime(0); setDur(playing.length ?? 0); setPaused(false);
    a.src = `/api/audio/${playing.id}`;
    a.play().catch(() => setPaused(true));
    fetch(`/api/waveform/${playing.id}`).then(r => r.json()).then(setWave);
  }, [playing]);

  const togglePlay = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) { a.play(); setPaused(false); } else { a.pause(); setPaused(true); }
  };

  useEffect(() => {
    fetch("/api/stats").then(r => r.json()).then(setStats);
    fetch("/api/playlists").then(r => r.json()).then(d => setPlaylists(d.rows));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const p = new URLSearchParams({ query: debounced, limit: "300" });
    if (playlist) p.set("playlist", playlist.id);
    if (tagFilter) p.set("tag", tagFilter);
    let stale = false;
    fetch(`/api/tracks?${p}`).then(r => r.json()).then(d => {
      if (!stale) { setTracks(d.rows); setTotal(d.total); }
    });
    return () => { stale = true; };
  }, [debounced, playlist, tagFilter]);

  const roots = useMemo(
    () => playlists.filter(p => p.parent_id == null),
    [playlists]);
  const childrenOf = useMemo(() => {
    const m = new Map<string, Playlist[]>();
    for (const p of playlists) {
      if (p.parent_id) {
        if (!m.has(p.parent_id)) m.set(p.parent_id, []);
        m.get(p.parent_id)!.push(p);
      }
    }
    return m;
  }, [playlists]);

  const toggleFolder = (id: string) =>
    setOpenFolders(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const renderNode = (p: Playlist, depth: number): React.ReactNode => {
    const kids = childrenOf.get(p.id) ?? [];
    const isFolder = p.kind === "folder";
    const open = openFolders.has(p.id);
    return (
      <div key={p.id}>
        <button
          className={`titem${playlist?.id === p.id ? " on" : ""}`}
          style={{ paddingLeft: 10 + depth * 14 }}
          onClick={() => (isFolder ? toggleFolder(p.id) : setPlaylist(playlist?.id === p.id ? null : p))}
        >
          <span>{isFolder ? (open ? "▾ " : "▸ ") : ""}{p.name}{p.kind === "smart" ? " ✦" : ""}</span>
          {!isFolder && <span className="n">{p.n}</span>}
        </button>
        {isFolder && open && kids.map(k => renderNode(k, depth + 1))}
      </div>
    );
  };

  return (
    <div className="shell">
      <nav className="rail">
        <svg className="logo" width="26" height="26" viewBox="0 0 26 26" fill="none">
          <line x1="6" y1="18" x2="13" y2="6" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
          <line x1="13" y1="6" x2="20" y2="15" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
          <line x1="6" y1="18" x2="20" y2="15" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
          <circle cx="6" cy="18" r="2.6" fill="#fab219" />
          <circle cx="13" cy="6" r="2.6" fill="#3987e5" />
          <circle cx="20" cy="15" r="2.6" fill="#d55181" />
        </svg>
        <Icon on={view === "library"} title="Library" d="M3 5h14M3 10h14M3 15h8"
          onClick={() => setView("library")} />
        <Icon title="Ingest (soon)" d="M10 3v8m0 0-3-3m3 3 3-3M3 13v3h14v-3" />
        <Icon on={view === "constellation"} title="Constellation"
          d="M6.4 12.8 8.9 6.6M11.6 6.3l2.5 4.3M6.8 14.3l6.4-1.9M5 12.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6M10 3.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6M15 10.2a1.8 1.8 0 1 0 0 3.6 1.8 1.8 0 0 0 0-3.6"
          onClick={() => setView("constellation")} />
        <Icon on={view === "sync"} title="Sync" d="M4 9a6 6 0 0 1 10.4-3.4L16 7M16 11a6 6 0 0 1-10.4 3.4L4 13M16 3v4h-4M4 17v-4h4"
          onClick={() => setView("sync")} />
        <Icon on={view === "usb"} title="USB stick" d="M10 2v9m0-9 2 2m-2-2L8 4m2 7a3 3 0 1 0 0 6 3 3 0 0 0 0-6m-4-3H4v3.2A5 5 0 0 0 7 14m7-7h2v3.2A5 5 0 0 1 13 14"
          onClick={() => setView("usb")} />
        <Icon on={view === "energy"} title="Energy" d="M11 2 4 11h5l-1 7 7-9h-5l1-7z"
          onClick={() => setView("energy")} />
        <Icon on={view === "backups"} title="Backups" d="M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14M10 6.5V10l2.5 2"
          onClick={() => setView("backups")} />
        <Icon extra="last" title="Settings (soon)" d="M3 5h14M3 10h14M3 15h14" />
      </nav>

      {view === "library" && <aside className="tree">
        <h1>Library</h1>
        <button className={`titem${playlist == null ? " on" : ""}`} onClick={() => setPlaylist(null)}>
          <span>All tracks</span><span className="n">{stats?.tracks?.toLocaleString() ?? "…"}</span>
        </button>
        <div className="thead">Playlists</div>
        {roots.map(p => renderNode(p, 0))}
      </aside>}

      <main className="main">
        {view === "usb" ? (
          <UsbView onOpen={openTrack} />
        ) : view === "sync" ? (
          <SyncView />
        ) : view === "energy" ? (
          <EnergyView catalog={catalog} onTagClick={browseTag} />
        ) : view === "backups" ? (
          <BackupsView />
        ) : view === "constellation" ? (
          <Constellation
            selId={sel?.id ?? null}
            similarIds={(similar ?? []).map(n => n.id)}
            focus={focus}
            onSelect={openTrack}
            onPlay={playById}
          />
        ) : (<>
        <div className="toolbar">
          <div className="search">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="#898781"
              strokeWidth="1.5" strokeLinecap="round">
              <circle cx="6" cy="6" r="4" /><path d="m12.5 12.5-3.6-3.6" />
            </svg>
            <input placeholder="Search title, artist, label, #hashtag…"
              value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          {playlist && (
            <span className="chip acc x" onClick={() => setPlaylist(null)}>
              {playlist.name} ✕
            </span>
          )}
          {tagFilter && (
            <span className="chip acc x" onClick={() => setTagFilter(null)}>
              tag: {tagFilter} ✕
            </span>
          )}
          <span className="lbl" style={{ marginLeft: "auto" }}>
            {tracks.length < total ? `${tracks.length} of ` : ""}{total.toLocaleString()} tracks
          </span>
        </div>

        <div className="thdr">
          <span className="lbl">Track</span><span className="lbl">BPM</span>
          <span className="lbl">Key</span><span className="lbl">Energy</span>
          <span className="lbl">My Tags</span><span className="lbl">Label</span>
        </div>
        <div className="rows">
          {tracks.map(t => (
            <div key={t.id} id={`row-${t.id}`} className={`trow${sel?.id === t.id ? " sel" : ""}${playing?.id === t.id ? " playing" : ""}`}
              onClick={() => setSel(t)} onDoubleClick={() => setPlaying(t)}
              onContextMenu={e => onRowCtx(e, t)}>
              <div style={{ minWidth: 0 }}>
                <div className="tt">{displayTitle(t)}</div>
                <div className="ta">{t.artist || "—"}</div>
              </div>
              <span className="num">{t.bpm?.toFixed(1) ?? "—"}</span>
              <span><span className="keyb">{t.key ?? "—"}</span></span>
              <span className="ebar">
                {t.energy != null
                  ? <><span className="v">E{t.energy}</span>
                    <span className="b"><span className="f" style={{ width: `${t.energy * 10}%` }} /></span></>
                  : <span className="v" style={{ color: "var(--muted)" }}>—</span>}
              </span>
              <span className="tagcell">
                {t.tags.filter(g => g.bank !== "Energy" || !/^E\d+$/.test(g.name)).slice(0, 3)
                  .map(g => <span key={g.bank + g.name} className="chip">{g.name}</span>)}
              </span>
              <span className="ta" style={{ fontSize: 11.5 }}>
                {t.tags.find(g => g.bank === "Label")?.name ?? t.label ?? "—"}
              </span>
            </div>
          ))}
          {tracks.length === 0 && <div className="empty">No tracks match.</div>}
        </div>
        </>)}
        {playing && (
          <div className="player">
            <button className="playbtn" onClick={togglePlay} title={paused ? "Play" : "Pause"}>
              <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
                {paused ? <path d="M4 2.5 13.5 8 4 13.5z" />
                  : <path d="M4 2.5h3v11H4zM9 2.5h3v11H9z" />}
              </svg>
            </button>
            <div className="pmeta">
              <div className="pt">{displayTitle(playing)}</div>
              <div className="pa">{playing.artist || "—"}{playing.bpm ? ` · ${playing.bpm.toFixed(1)}` : ""}{playing.key ? ` · ${playing.key}` : ""}</div>
            </div>
            <Waveform
              wave={wave}
              progress={dur > 0 ? time / dur : 0}
              onSeek={f => { const a = audioRef.current; if (a && dur > 0) a.currentTime = f * dur; }}
            />
            <span className="ptime">{fmtLen(time)} / {fmtLen(dur)}</span>
          </div>
        )}
        <audio
          ref={audioRef}
          onTimeUpdate={e => setTime((e.target as HTMLAudioElement).currentTime)}
          onDurationChange={e => setDur((e.target as HTMLAudioElement).duration || 0)}
          onEnded={() => setPaused(true)}
          style={{ display: "none" }}
        />
        <div className="footer">
          <span>library.db · imported {stats?.imported_at ?? "…"} UTC · double-click a row to play</span>
          <span style={{ marginLeft: "auto" }}>{stats?.energy_tagged?.toLocaleString()} energy-tagged</span>
        </div>
      </main>

      <aside className="detail">
        {sel ? (
          <>
            <div className="card">
              <h3>{displayTitle(sel)}</h3>
              <div className="sub">{[sel.artist, sel.tags.find(g => g.bank === "Label")?.name ?? sel.label, sel.year].filter(Boolean).join(" · ") || "—"}</div>
              <div style={{ marginTop: 12 }}>
                <div className="kv"><span>BPM</span><b>{sel.bpm?.toFixed(2) ?? "—"}</b></div>
                <div className="kv"><span>Key</span><b>{sel.key ?? "—"}</b></div>
                <div className="kv"><span>Energy</span>
                  <span className="epick">
                    {Array.from({ length: 10 }, (_, i) => i + 1).map(v => (
                      <button key={v} className={`ep${sel.energy === v ? " on" : ""}`}
                        onClick={() => setEnergyVal(sel.id, v)} title={`set E${v}`}>{v}</button>
                    ))}
                  </span>
                </div>
                <div className="kv"><span>Length</span><b>{fmtLen(sel.length)}</b></div>
                <div className="kv"><span>Volume</span><b>{sel.volume ?? "—"}</b></div>
              </div>
            </div>
            <div className="card">
              <div className="lbl" style={{ marginBottom: 8 }}>My Tags</div>
              {(["Genre", "Energy", "Vibe", "Label"] as const).map(bank => {
                const bt = sel.tags.filter(g => g.bank === bank);
                const byName = new Map(catalog.filter(c => c.bank === bank).map(c => [c.name, c]));
                const applied = new Set(bt.map(g => g.name));
                const addable = catalog.filter(c => c.bank === bank && !applied.has(c.name)
                  && !/^E(10|[1-9])$/.test(c.name));
                return (
                  <div key={bank} style={{ marginBottom: 6 }}>
                    <span className="lbl" style={{ fontSize: 10 }}>{bank}</span>
                    <div className="chips">
                      {bt.map(g => (
                        <span key={g.name} className={`chip tg${bank === "Label" ? " acc" : ""}`}>
                          {g.name}
                          <button className="tgx" title="remove tag"
                            onClick={() => { const c = byName.get(g.name); if (c) removeTag(sel.id, c.id); }}>✕</button>
                        </span>
                      ))}
                      <span className="chip addt" onClick={() => setAddBank(addBank === bank ? null : bank)}>+</span>
                    </div>
                    {addBank === bank && (
                      <div className="addpop">
                        {addable.map(c => (
                          <button key={c.id} className="addopt" onClick={() => addTag(sel.id, c.id)}>
                            {c.name} <span className="n">{c.n}</span>
                          </button>
                        ))}
                        {addable.length === 0 && <span className="ta">all applied</span>}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="card">
              <div className="lbl" style={{ marginBottom: 8 }}>Sounds like</div>
              {similar == null && <div className="ta">computing…</div>}
              {similar?.length === 0 && <div className="ta">not analyzed yet — no embedding for this track</div>}
              {similar?.map(n => (
                <div key={n.id} className="nb" onClick={() => jumpToTrack(n)}
                  onDoubleClick={() => setPlaying(n)} onContextMenu={e => onRowCtx(e, n)}
                  title="click: jump to track · double-click: play">
                  <span className="sim">{n.sim.toFixed(2).replace(/^0/, "")}</span>
                  <div className="nlbl">
                    <div className="t">{displayTitle(n)}</div>
                    <div className="m">{[n.artist, n.bpm?.toFixed(0), n.key, n.energy != null ? `E${n.energy}` : null].filter(Boolean).join(" · ")}</div>
                  </div>
                </div>
              ))}
            </div>
            {sel.comment && (
              <div className="card">
                <div className="lbl">Comment mirror</div>
                <div className="comment">{sel.comment}</div>
              </div>
            )}
            {sel.path && (
              <div className="card">
                <div className="lbl">File</div>
                <div className="comment">{sel.path}</div>
              </div>
            )}
          </>
        ) : (
          <div className="empty">Select a track to inspect it.<br />Search and playlist filters are live.</div>
        )}
      </aside>
      <SyncDrawer />
      {ctx && (
        <ContextMenu
          ctx={ctx}
          onClose={() => setCtx(null)}
          onPlay={() => setPlaying(ctx.track)}
          onJump={() => jumpToTrack(ctx.track)}
          onConstellation={() => openInConstellation(ctx.track.id)}
          onReveal={() => fetch(`/api/reveal/${ctx.track.id}`, { method: "POST" })}
          onCopy={() => { if (ctx.track.path) navigator.clipboard.writeText(ctx.track.path); }}
        />
      )}
    </div>
  );
}
