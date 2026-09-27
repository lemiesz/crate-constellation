import { useEffect, useRef, useState } from "react";
import { useSyncJobs } from "./useSyncJobs";

const RAMP = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#5598e7",
  "#86b6ef", "#5598e7", "#3987e5", "#256abf", "#184f95"];

export function EnergyView({ catalog, onTagClick }: {
  catalog: { id: string; bank: string; name: string; n: number }[];
  onTagClick: (name: string) => void;
}) {
  const [hist, setHist] = useState<{ e: number; n: number }[] | null>(null);
  const [meta, setMeta] = useState<{ total: number; tagged: number } | null>(null);
  useEffect(() => {
    fetch("/api/energy").then(r => r.json()).then(d => {
      setHist(d.hist); setMeta({ total: d.total, tagged: d.tagged });
    });
  }, []);
  const max = Math.max(1, ...(hist ?? []).map(b => b.n));
  const banks = ["Genre", "Energy", "Vibe", "Label"];
  return (
    <div className="viewwrap">
      <div className="vhead">
        <span className="vtitle">Energy &amp; Tags</span>
        <span className="ta" style={{ fontSize: 12 }}>
          {meta ? `${meta.tagged.toLocaleString()} of ${meta.total.toLocaleString()} tracks energy-tagged` : "…"}
        </span>
      </div>
      <div className="vbody">
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="lbl" style={{ marginBottom: 12 }}>Distribution — E1 … E10</div>
          <div className="hist">
            {(hist ?? []).map((b, i) => (
              <div key={b.e} className="hcol" onClick={() => onTagClick(`E${b.e}`)} title={`browse E${b.e}`}>
                <span className="hn">{b.n.toLocaleString()}</span>
                <span className="hbar" style={{ height: Math.max(5, (b.n / max) * 110), background: RAMP[i] }} />
                <span className="he">E{b.e}</span>
              </div>
            ))}
          </div>
          <div className="ta" style={{ marginTop: 12, fontSize: 11, lineHeight: 1.5 }}>
            Relative 1–10 scale across the whole library. Click a bar to browse that band.
          </div>
        </div>
        <div className="card">
          <div className="lbl" style={{ marginBottom: 4 }}>My Tag banks</div>
          <div className="ta" style={{ fontSize: 10.5, color: "var(--amber)", marginBottom: 10 }}>
            4 banks max — the XDJ-XZ shows only these
          </div>
          <div className="banks">
            {banks.map(bank => {
              const bt = catalog.filter(c => c.bank === bank && c.n > 0)
                .sort((a, b) => b.n - a.n);
              return (
                <div key={bank}>
                  <div className="bankhd">{bank}</div>
                  <div className="bankchips">
                    {bt.map(c => (
                      <button key={c.id} className="bankchip" onClick={() => onTagClick(c.name)}>
                        {c.name} <span className="n">{c.n.toLocaleString()}</span>
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

type Backup = { key: string; group: string; name: string; size: number; mtime: number; live: boolean };
const GROUPS: [string, string][] = [
  ["rekordbox", "rekordbox · master.db"],
  ["traktor", "Traktor · collection.nml"],
  ["usb-stick", "USB stick · PDB"],
];
const fmtSize = (b: number) =>
  b > 1 << 30 ? `${(b / (1 << 30)).toFixed(1)} GB`
    : b > 1 << 20 ? `${Math.round(b / (1 << 20))} MB` : `${Math.round(b / 1024)} KB`;
const fmtDate = (t: number) => new Date(t * 1000).toLocaleString(undefined,
  { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function BackupsView() {
  const [rows, setRows] = useState<Backup[] | null>(null);
  useEffect(() => {
    fetch("/api/backups").then(r => r.json()).then(d => setRows(d.rows));
  }, []);
  return (
    <div className="viewwrap">
      <div className="vhead">
        <span className="vtitle">Backups</span>
        <span className="ta" style={{ fontSize: 12 }}>
          every snapshot found on disk — every write is backup-first
        </span>
        <span className="ta" style={{ marginLeft: "auto", fontSize: 11 }}>
          {rows ? `${rows.length} files` : "scanning…"}
        </span>
      </div>
      <div className="vbody" style={{ flexDirection: "column" }}>
        {GROUPS.map(([g, label]) => {
          const gr = (rows ?? []).filter(r => r.group === g);
          return (
            <div key={g} className="card">
              <div className="lbl" style={{ marginBottom: 8 }}>{label}</div>
              {gr.length === 0 && <div className="ta">none found</div>}
              {gr.map(r => (
                <div key={r.key} className="bkrow">
                  <span className="bkdot" style={{ background: r.live ? "var(--green)" : "var(--blue)" }} />
                  <span className="bkname">{r.name}{r.live ? "  · live" : ""}</span>
                  <span className="bkmeta">{fmtDate(r.mtime)}</span>
                  <span className="bkmeta num">{fmtSize(r.size)}</span>
                  <button className="bkbtn" onClick={() =>
                    fetch("/api/backups/reveal", {
                      method: "POST", headers: { "content-type": "application/json" },
                      body: JSON.stringify({ key: r.key }),
                    })}>Reveal</button>
                </div>
              ))}
            </div>
          );
        })}
        <div className="ta" style={{ fontSize: 11 }}>
          Snapshot state-browsing, diff and restore land with the sync engine.
        </div>
      </div>
    </div>
  );
}

type SyncStatus = {
  imported_at: string | null; tracks: number; pending_edits: number;
  rekordbox: { size: number; mtime: number } | null;
  traktor: { size: number; mtime: number } | null;
  usb_volumes: string[];
};
type Journal = { id: number; ts: string; field: string; entity_id: string;
  old_value: string | null; new_value: string | null };
type NetChange = { kind: "energy"; from: string | null; to: string | null }
  | { kind: "tag"; tag: string; change: "added" | "removed" };
type PendingTrack = { id: string; title: string | null; artist: string | null;
  edits: number; first_ts: string; last_ts: string; changes: NetChange[] };
type PendingEdits = { imported_at: string | null; raw_edits: number;
  net_changes: number; tracks: PendingTrack[] };

// What a re-import would overwrite, as net changes per track. The raw
// journal count alone reads as alarming (a test flipping one tag 55 times
// is 110 "edits") when the real loss may be one energy value, so the modal
// leads with the net figure and folds the cancelled-out tracks away.
function ImportEditsModal({ busy, onImport, onClose }: {
  busy: boolean; onImport: () => void; onClose: () => void;
}) {
  const [d, setD] = useState<PendingEdits | null>(null);
  const [showNoop, setShowNoop] = useState(false);
  useEffect(() => {
    fetch("/api/sync/pending-edits").then(r => r.json()).then(setD);
  }, []);
  const real = d?.tracks.filter(t => t.changes.length) ?? [];
  const noop = d?.tracks.filter(t => !t.changes.length) ?? [];
  const name = (t: PendingTrack) =>
    `${t.artist ?? "?"} — ${t.title ?? t.id}`;
  const row = (t: PendingTrack) => (
    <div key={t.id} className="pe-row">
      <div className="pe-name" title={name(t)}>{name(t)}</div>
      <div className="pe-changes">
        {t.changes.length ? t.changes.map((c, i) => c.kind === "energy" ? (
          <span key={i} className="pe-chip">energy {c.from ?? "—"} → <b>{c.to ?? "—"}</b></span>
        ) : (
          <span key={i} className={`pe-chip ${c.change}`}>
            {c.change === "added" ? "+" : "−"} {c.tag}</span>
        )) : <span className="ta">no net change</span>}
      </div>
      <div className="pe-meta">{t.edits} edit{t.edits === 1 ? "" : "s"} · last {t.last_ts}</div>
    </div>
  );
  return (
    <div className="wizov" data-testid="import-edits-modal"
      onClick={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div className="wiz" style={{ width: 640 }}>
        <div className="wizhd">
          <span className="vtitle">Local edits since import</span>
          <span className="ta" style={{ fontSize: 11.5 }}>
            {d?.imported_at ? `last import ${d.imported_at} UTC` : ""}</span>
        </div>
        {!d ? <div className="ta">loading…</div> : (
          <>
            <div className="md" style={{ fontSize: 12.5, lineHeight: 1.55 }}>
              Re-importing rebuilds tracks, tags and cues from rekordbox.
              {" "}<b data-testid="pe-summary">
                {d.net_changes} change{d.net_changes === 1 ? "" : "s"} on {real.length} track
                {real.length === 1 ? "" : "s"}</b> will be lost
              {d.raw_edits !== d.net_changes &&
                <> ({d.raw_edits} journal edits; the rest cancel each other out)</>}.
              The edit journal itself is kept.
            </div>
            <div className="wizlist" style={{ minHeight: 0 }}>
              {real.map(row)}
              {real.length === 0 && <div className="ta" style={{ padding: 6 }}>
                Nothing would actually change — every edit was undone.</div>}
              {noop.length > 0 && (
                <>
                  <button className="wizcaret pe-fold" onClick={() => setShowNoop(!showNoop)}>
                    {showNoop ? "▾" : "▸"} {noop.length} track{noop.length === 1 ? "" : "s"} with
                    edits that cancel out</button>
                  {showNoop && noop.map(row)}
                </>
              )}
            </div>
          </>
        )}
        <div className="mbtns" style={{ justifyContent: "flex-end" }}>
          <button className="mcancel" onClick={onClose} disabled={busy}>Keep my edits</button>
          <button className="syncbtn" onClick={onImport} disabled={busy || !d}>
            {busy ? "importing…" : d && d.net_changes === 0 ? "Re-import" : "Overwrite and re-import"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function SyncView() {
  const [st, setSt] = useState<SyncStatus | null>(null);
  const [journal, setJournal] = useState<Journal[]>([]);
  const [busy, setBusy] = useState(false);
  const [usbLog, setUsbLog] = useState<any[]>([]);
  const [healReport, setHealReport] = useState<any[] | null>(null);
  const [healBusy, setHealBusy] = useState(false);
  const [job, setJob] = useState<any[]>([]);
  const load = () => {
    fetch("/api/sync/status").then(r => r.json()).then(setSt);
    fetch("/api/journal?limit=25").then(r => r.json()).then(d => setJournal(d.rows));
    fetch("/api/usb/wizard/log?limit=20").then(r => r.json()).then(d => setUsbLog(d.rows));
  };
  useEffect(load, []);
  const stream = useSyncJobs();
  useEffect(() => {
    setJob(stream.jobs.filter((j: any) => j.state === "running"));
  }, [stream]);
  // Confirmed in a modal that lists what would be lost, per track, rather
  // than a bare count next to the button.
  const [confirmImport, setConfirmImport] = useState(false);
  const doImport = () => {
    setBusy(true);
    fetch("/api/sync/import", { method: "POST" }).then(r => r.json())
      .finally(() => { setBusy(false); setConfirmImport(false); load(); });
  };
  const reimport = () => {
    if (st?.pending_edits) setConfirmImport(true);
    else doImport();
  };
  return (
    <div className="viewwrap">
      <div className="vhead">
        <span className="vtitle">Sync</span>
        <span className="ta" style={{ fontSize: 12 }}>
          app library is the source of truth — pushing to targets lands with the sync engine
        </span>
        <button className="syncbtn" style={{ width: "auto", marginTop: 0, marginLeft: "auto", padding: "6px 14px" }}
          disabled={healBusy}
          onClick={() => {
            setHealBusy(true);
            fetch("/api/heal", { method: "POST" }).then(r => r.json())
              .then(d => setHealReport(d.steps)).finally(() => setHealBusy(false));
          }}>{healBusy ? "healing…" : "Heal library"}</button>
      </div>
      <div className="vbody" style={{ flexDirection: "column" }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 14, width: "100%" }}>
          <div className="card">
            <div className="synchd">rekordbox <span className="ta">master.db</span></div>
            <div className="kv"><span>tracks imported</span><b>{st?.tracks?.toLocaleString() ?? "…"}</b></div>
            <div className="kv"><span>last import</span><b>{st?.imported_at ?? "—"} UTC</b></div>
            <div className="kv"><span>master.db size</span><b>{st?.rekordbox ? `${Math.round(st.rekordbox.size / 1048576)} MB` : "—"}</b></div>
            <div className="kv"><span>local edits since import</span>
              {st?.pending_edits ? (
                <button className="pe-link" data-testid="view-edits"
                  onClick={() => setConfirmImport(true)}>{st.pending_edits} · review</button>
              ) : <b style={{ color: "var(--green)" }}>{st?.pending_edits ?? "…"}</b>}</div>
            <button className="syncbtn" onClick={reimport} disabled={busy}>
              {busy ? "importing…" : "Re-import from rekordbox"}
            </button>
            {confirmImport && (
              <ImportEditsModal busy={busy} onImport={doImport}
                onClose={() => setConfirmImport(false)} />
            )}
            <div className="ta" style={{ fontSize: 10.5, marginTop: 6 }}>
              read-only against master.db — safe while rekordbox is open
            </div>
          </div>
          <div className="card">
            <div className="synchd">Traktor <span className="ta">collection.nml</span></div>
            <div className="kv"><span>collection size</span><b>{st?.traktor ? `${(st.traktor.size / 1048576).toFixed(1)} MB` : "—"}</b></div>
            <div className="kv"><span>last modified</span><b>{st?.traktor ? new Date(st.traktor.mtime * 1000).toLocaleString() : "—"}</b></div>
            <div className="ta" style={{ fontSize: 11, marginTop: 10, lineHeight: 1.5 }}>
              Direct diff-based writer (grid-lock, cues preserved) is the next engine milestone.
            </div>
          </div>
          <div className="card">
            <div className="synchd">USB · XDJ-XZ</div>
            <div className="kv"><span>mounted sticks</span>
              <b>{st ? (st.usb_volumes.length ? st.usb_volumes.join(", ") : "none") : "…"}</b></div>
            <div className="ta" style={{ fontSize: 11, marginTop: 10, lineHeight: 1.5 }}>
              Phase A wraps the deck-verified matching patch with its three safety gates — lands with the sync engine.
            </div>
          </div>
        </div>
        {healReport && (
          <div className="card" style={{ width: "100%" }}>
            <div className="lbl" style={{ marginBottom: 8 }}>Heal report</div>
            {healReport.map((st: any, i: number) => (
              <div key={i} className="bkrow">
                <span className="bkdot" style={{ background:
                  st.fixed ? "var(--amber)" : "var(--green)" }} />
                <span className="bkname">{st.step}: {String(st.found)}
                  {st.fixed ? ` · fixed ${st.fixed}` : ""}{st.note ? ` — ${st.note}` : ""}</span>
              </div>
            ))}
          </div>
        )}
        <div className="card" style={{ width: "100%" }}>
          <div className="lbl" style={{ marginBottom: 8 }}>USB sync log</div>
          {job.map((j: any) => {
            const hbAge = j.heartbeat ? Math.round(Date.now() / 1000 - j.heartbeat) : 0;
            return (
              <div key={j.volume} className="bkrow" style={{ background: "rgba(201,133,0,.08)", borderRadius: 6, padding: "6px 8px" }}>
                <span className="bkdot" style={{ background: "var(--amber)" }} />
                <span className="bkname">RUNNING → {j.volume} · {j.step} ·
                  {" "}{((j.bytes_done ?? 0) / 1e6).toFixed(0)} MB · heartbeat {hbAge}s ago</span>
                <button className="bkbtn" onClick={() => fetch("/api/usb/wizard/cancel", {
                  method: "POST", headers: { "content-type": "application/json" },
                  body: JSON.stringify({ volume: j.volume }) }).then(load)}>Cancel</button>
                {hbAge > 20 && (
                  <button className="bkbtn" style={{ color: "#e5484d" }}
                    onClick={() => fetch("/api/usb/wizard/kill", {
                      method: "POST", headers: { "content-type": "application/json" },
                      body: JSON.stringify({ volume: j.volume }) }).then(load)}>Kill</button>
                )}
              </div>
            );
          })}
          {usbLog.length === 0 && job.length === 0 && <div className="ta">no syncs recorded yet</div>}
          {usbLog.map(l => (
            <div key={l.id} className="bkrow">
              <span className="bkdot" style={{ background: l.state === "done" ? "var(--green)" : l.state === "cancelled" ? "var(--muted)" : "#e5484d" }} />
              <span className="bkname">{l.volume} · {l.n_tracks ?? "?"} tracks
                {l.error ? ` · ${String(l.error).slice(0, 60)}` : ""}</span>
              <span className="bkmeta">{l.started_at} UTC</span>
              <span className="bkmeta num">{((l.bytes ?? 0) / 1e9).toFixed(2)} GB</span>
              <span className="bkmeta num">{Math.round(l.duration_s ?? 0)}s</span>
              <span className="bkmeta num" style={{ width: 76, color: "var(--ink2)" }}>{l.mb_s} MB/s</span>
            </div>
          ))}
        </div>
        <div className="card" style={{ width: "100%" }}>
          <div className="lbl" style={{ marginBottom: 8 }}>Edit journal — most recent</div>
          {journal.length === 0 && <div className="ta">no edits yet</div>}
          {journal.map(j => (
            <div key={j.id} className="bkrow">
              <span className="bkmeta" style={{ textAlign: "left", width: 130 }}>{j.ts} UTC</span>
              <span className="bkname">{j.field}
                {j.old_value ? ` ${j.old_value}` : ""}{j.new_value ? ` → ${j.new_value}` : ""}</span>
              <span className="bkmeta num">track {j.entity_id}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

type UsbStatus = {
  mounted: boolean; volume?: string; free_bytes?: number; total_bytes?: number;
  on_stick?: number; synced?: number; missing?: number; stick_only?: number;
  missing_bytes?: number; matching_pairs?: number;
};
type UsbTrack = { id: string | null; title: string | null; artist: string | null;
  bpm: number | null; energy: number | null; size: number | null; status: string };
type UsbPlaylist = { id: string; name: string; parent_id: string | null;
  kind: string; seq: number; n: number };

const gb = (b?: number | null) => b == null ? "—" : `${(b / 1e9).toFixed(1)} GB`;

export function UsbView({ onOpen }: { onOpen: (id: string) => void }) {
  const [st, setSt] = useState<UsbStatus | null>(null);
  const [filter, setFilter] = useState<"synced" | "missing" | "stick_only">("missing");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<UsbTrack[]>([]);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string | null>(null);
  const [playlists, setPlaylists] = useState<UsbPlaylist[]>([]);
  const [pl, setPl] = useState<UsbPlaylist | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch("/api/usb/status").then(r => r.json()).then(d => {
      setSt(d); setPlaylists(d.playlists ?? []);
    });
  }, []);
  useEffect(() => {
    if (!st?.mounted) return;
    const p = new URLSearchParams({ query: q });
    if (pl) p.set("playlist", pl.id); else p.set("filter", filter);
    const t = setTimeout(() =>
      fetch(`/api/usb/tracks?${p}`)
        .then(r => r.json()).then(d => { setRows(d.rows); setTotal(d.total); }), 200);
    return () => clearTimeout(t);
  }, [st?.mounted, filter, q, pl]);

  const kids = (id: string | null) =>
    playlists.filter(p => p.parent_id === id).sort((a, b) => a.seq - b.seq);
  const renderPl = (p: UsbPlaylist, depth: number): React.ReactNode => {
    const isFolder = p.kind === "folder";
    const isOpen = open.has(p.id);
    return (
      <div key={p.id}>
        <button className={`titem${pl?.id === p.id ? " on" : ""}`}
          style={{ paddingLeft: 10 + depth * 14 }}
          onClick={() => isFolder
            ? setOpen(s2 => { const n = new Set(s2); n.has(p.id) ? n.delete(p.id) : n.add(p.id); return n; })
            : setPl(pl?.id === p.id ? null : p)}>
          <span>{isFolder ? (isOpen ? "▾ " : "▸ ") : ""}{p.name}</span>
          {!isFolder && <span className="n">{p.n}</span>}
        </button>
        {isFolder && isOpen && kids(p.id).map(k => renderPl(k, depth + 1))}
      </div>
    );
  };

  const [match, setMatch] = useState<any>(null);

  // The dry run leaves its result on screen with its own Write button, rather
  // than throwing a browser confirm over the top of it. The numbers you are
  // agreeing to stay visible while you decide.
  const patch = async () => {
    setBusy(true); setLog(null); setMatch(null);
    try {
      const dry = await (await fetch("/api/usb/patch-matching", {
        method: "POST", headers: { "content-type": "application/json" }, body: "{}",
      })).json();
      if (dry.detail) setLog(dry.detail); else setMatch(dry);
    } catch (e) { setLog(String(e)); }
    setBusy(false);
  };

  const applyMatch = async () => {
    setBusy(true);
    try {
      const res = await (await fetch("/api/usb/patch-matching", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ apply: true }),
      })).json();
      if (res.detail) setLog(res.detail); else setMatch(res);
      fetch("/api/usb/status").then(r => r.json()).then(setSt);
    } catch (e) { setLog(String(e)); }
    setBusy(false);
  };

  const matchPanel = match && (
    <div className="mpanel" data-testid="match-result">
      <div className="mrow">
        <span className="mk">{match.applied ? "Written to" : "Ready to write to"}</span>
        <b>{match.volume}</b>
      </div>
      <div className="mstats">
        <div><b>{(match.tracks_on_stick ?? 0).toLocaleString()}</b><span>tracks on the drive</span></div>
        <div><b>{(match.pairs ?? 0).toLocaleString()}</b><span>matching pairs</span></div>
        <div><b>{(match.tracks_matched ?? 0).toLocaleString()}</b><span>tracks with a match</span></div>
      </div>
      {(match.steps ?? []).map((s: any, i: number) => (
        <div key={i} className={"mstep " + (s.ok ? "ok" : "bad")}>
          <span>{s.ok ? "✓" : "✕"}</span>
          <b>{s.name}</b>
          <span className="md">{s.detail}</span>
        </div>
      ))}
      {(match.notes ?? []).map((n: string, i: number) => (
        <div key={"n" + i} className="md">{n}</div>
      ))}
      {match.backups && <div className="md">Originals backed up to {match.backups}</div>}
      {!match.applied && match.pairs > 0 && (
        <div className="mconfirm">
          <div className="md">
            Nothing has been written yet. Both tables are replaced and the
            originals are backed up off the drive. Don't let rekordbox sync
            this drive while it runs.
          </div>
          <div className="mbtns">
            <button className="syncbtn" disabled={busy} onClick={applyMatch}
                    data-testid="match-apply">
              {busy ? "Writing…" : `Write ${match.pairs.toLocaleString()} pairs`}
            </button>
            <button className="mcancel" disabled={busy} onClick={() => setMatch(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {match.applied && match.ok && (
        <div className="md">Done. Eject cleanly, then test on the deck.</div>
      )}
    </div>
  );

  const [wizard, setWizard] = useState(false);
  const [settings, setSettings] = useState(false);
  if (st && !st.mounted) {
    return (
      <div className="viewwrap">
        <div className="vhead"><span className="vtitle">USB</span>
          <button className="syncbtn" style={{ width: "auto", marginTop: 0, marginLeft: "auto", padding: "6px 14px" }}
            onClick={() => setWizard(true)}>Sync wizard…</button>
        </div>
        <div className="empty" style={{ flex: 1 }}>
          No rekordbox-exported stick mounted.<br />
          Plug one in to explore it — or use the sync wizard to build a stick from a cleared USB.
        </div>
        {wizard && <SyncWizard onClose={() => setWizard(false)} />}
      </div>
    );
  }
  const used = st ? (st.total_bytes! - st.free_bytes!) / st.total_bytes! : 0;
  return (
    <div className="viewwrap">
      <div className="vhead">
        <span className="vtitle">USB · {st?.volume ?? "…"}</span>
        <span className="capbar"><span style={{ width: `${used * 100}%` }} /></span>
        <span className="ta" style={{ fontSize: 11.5 }}>
          {st ? `${gb(st.total_bytes! - st.free_bytes!)} used · ${gb(st.free_bytes)} free` : ""}
        </span>
        <span className="ta" style={{ marginLeft: "auto", fontSize: 11.5 }}>
          exportLibrary.db read off-stick — explorer never writes
        </span>
        <button className="bkbtn" style={{ padding: "6px 12px" }}
          onClick={() => setSettings(true)}>Drive settings…</button>
        <button className="syncbtn" style={{ width: "auto", marginTop: 0, padding: "6px 14px" }}
          onClick={() => setWizard(true)}>Sync wizard…</button>
      </div>
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      <aside className="tree" style={{ width: 240 }}>
        <button className={`titem${pl == null ? " on" : ""}`} onClick={() => setPl(null)}>
          <span>All tracks on stick</span><span className="n">{st?.on_stick?.toLocaleString()}</span>
        </button>
        <div className="thead">Playlists on stick</div>
        {kids(null).map(p => renderPl(p, 0))}
      </aside>
      <div className="vbody" style={{ flexDirection: "column", minWidth: 0 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 14, width: "100%" }}>
          <div className="card"><div className="lbl">On stick</div>
            <div className="bignum">{st?.on_stick?.toLocaleString() ?? "…"}</div>
            <div className="ta" style={{ fontSize: 11 }}>{st?.synced?.toLocaleString()} matched to library</div></div>
          <div className="card"><div className="lbl">Missing from stick</div>
            <div className="bignum" style={{ color: "var(--amber)" }}>{st?.missing?.toLocaleString() ?? "…"}</div>
            <div className="ta" style={{ fontSize: 11, color: (st?.missing_bytes ?? 0) > (st?.free_bytes ?? 1) ? "#e5484d" : "var(--muted)" }}>
              {gb(st?.missing_bytes)} needed · {gb(st?.free_bytes)} free</div></div>
          <div className="card"><div className="lbl">Stick only</div>
            <div className="bignum">{st?.stick_only?.toLocaleString() ?? "…"}</div>
            <div className="ta" style={{ fontSize: 11 }}>on stick, not in master library</div></div>
          <div className="card"><div className="lbl">Matching pairs</div>
            <div className="bignum" style={{ color: st?.matching_pairs === 0 ? "#e5484d" : "var(--ink)" }}>
              {st?.matching_pairs?.toLocaleString() ?? "…"}</div>
            <button className="syncbtn" onClick={patch} disabled={busy}>
              {busy ? "patching…" : "Patch matching (deck-verified)"}</button></div>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center", width: "100%" }}>
          {pl ? (
            <span className="chip acc x" onClick={() => setPl(null)}
              style={{ fontSize: 12 }}>{pl.name} · {pl.n} · in playlist order ✕</span>
          ) : (
          <div className="seg2">
            {(["missing", "synced", "stick_only"] as const).map(f => (
              <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                {f === "stick_only" ? "stick only" : f}</button>
            ))}
          </div>
          )}
          <div className="search" style={{ width: 260 }}>
            <input placeholder="Filter…" value={q} onChange={e => setQ(e.target.value)} />
          </div>
          <span className="lbl" style={{ marginLeft: "auto" }}>
            {rows.length < total ? `${rows.length} of ` : ""}{total.toLocaleString()}</span>
          <button className="bkbtn" disabled
            title="Adding tracks to the stick without rekordbox needs the PDB writer — gated behind the sacrificial-stick protocol. For now: export missing tracks via rekordbox device sync.">
            Add missing to stick — gated</button>
        </div>
        <div className="card" style={{ width: "100%", padding: 0, overflowY: "auto", flex: "1 1 0", minHeight: 120 }}>
          {rows.map((t, i) => (
            <div key={i} className="bkrow" style={{ padding: "6px 14px", cursor: t.id ? "pointer" : "default" }}
              onClick={() => t.id && onOpen(t.id)}>
              <span className="bkdot" style={{ background: t.status === "synced" ? "var(--green)" : t.status === "missing" ? "var(--amber)" : "var(--muted)" }} />
              <span className="bkname">{t.title || (t as any).path || "—"}
                <span style={{ color: "var(--muted)" }}>{t.artist ? `  ·  ${t.artist}` : ""}</span></span>
              <span className="bkmeta num" style={{ width: 56 }}>{t.bpm?.toFixed(0) ?? "—"}</span>
              <span className="bkmeta num" style={{ width: 40 }}>{t.energy != null ? `E${t.energy}` : "—"}</span>
              <span className="bkmeta num" style={{ width: 70 }}>{t.size ? `${Math.round(t.size / 1048576)} MB` : "—"}</span>
            </div>
          ))}
          {rows.length === 0 && <div className="empty">nothing here</div>}
        </div>
        {matchPanel}
        {log && <pre className="patchlog">{log}</pre>}
      </div>
      </div>
      {wizard && <SyncWizard onClose={() => setWizard(false)} />}
      {settings && st?.volume &&
        <DriveSettings volume={st.volume} onClose={() => setSettings(false)} />}
    </div>
  );
}

/** Per-drive maintenance: repair/re-sync from the stick's own manifest
 *  (fixes artwork, ANLZ, databases — audio already present is skipped)
 *  plus library health, without re-picking playlists in the wizard. */
export function DriveSettings({ volume, onClose }: { volume: string; onClose: () => void }) {
  const [info, setInfo] = useState<any | null>(null);
  const [artStatus, setArtStatus] = useState<any | null>(null);
  const [healReport, setHealReport] = useState<any[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/usb/settings?volume=${encodeURIComponent(volume)}`)
      .then(r => r.json()).then(setInfo);
    fetch("/api/heal/art").then(r => r.json()).then(setArtStatus);
  }, [volume]);

  const resync = async () => {
    setBusy("resync"); setErr(null);
    try {
      const r = await fetch("/api/usb/resync", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ volume }),
      });
      if (!r.ok) { setErr((await r.json()).detail ?? `HTTP ${r.status}`); }
      else onClose();                      // job appears in the sync drawer
    } catch (e) { setErr(String(e)); }
    setBusy(null);
  };
  const heal = async () => {
    setBusy("heal"); setErr(null);
    try {
      const d = await (await fetch("/api/heal", { method: "POST" })).json();
      setHealReport(d.steps);
      fetch("/api/heal/art").then(r => r.json()).then(setArtStatus);
    } catch (e) { setErr(String(e)); }
    setBusy(null);
  };

  const m = info?.manifest;
  return (
    <div className="wizov" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="wiz">
        <div className="wizhd">
          <span className="vtitle">Drive settings · {volume}</span>
          <button className="bkbtn" style={{ marginLeft: "auto" }} onClick={onClose}>Close</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, overflowY: "auto" }}>
          <div className="card">
            <div className="lbl">Last sync (from this tool)</div>
            {m ? (
              <div style={{ fontSize: 12.5, lineHeight: 1.7 }}>
                {new Date(m.synced_at).toLocaleString()} — {m.n_tracks?.toLocaleString()} tracks
                · {m.n_playlists} playlists · {info.art_files} covers on stick
              </div>
            ) : info && !info.mounted ? (
              <div className="ta" style={{ fontSize: 12 }}>drive not mounted</div>
            ) : (
              <div className="ta" style={{ fontSize: 12 }}>
                No sync manifest on this stick — run a sync from the wizard once;
                after that it can be repaired from here.
              </div>
            )}
          </div>
          <div className="card">
            <div className="lbl">Repair / re-sync</div>
            <div className="ta" style={{ fontSize: 11.5, margin: "4px 0 8px" }}>
              Re-runs the last sync against this stick: audio already on the stick is
              skipped; album artwork, analysis files and all databases are rebuilt.
              Use after an artwork fix or a suspect eject.
            </div>
            <button className="syncbtn" disabled={!m || busy === "resync"} onClick={resync}>
              {busy === "resync" ? "starting…" : "Re-sync artwork & databases"}
            </button>
          </div>
          <div className="card">
            <div className="lbl">Library health</div>
            <div className="ta" style={{ fontSize: 11.5, margin: "4px 0 8px" }}>
              {artStatus?.coverage
                ? `art cache: ${artStatus.coverage.with_art?.toLocaleString()} of ${artStatus.coverage.total?.toLocaleString()} tracks with covers`
                : " "}
              {artStatus?.state === "running"
                ? ` · precompute ${artStatus.phase === "normalizing" ? "normalizing" : "extracting"} ${artStatus.done}/${artStatus.total}` : ""}
            </div>
            <button className="syncbtn" disabled={busy === "heal"} onClick={heal}>
              {busy === "heal" ? "checking…" : "Check & heal library"}
            </button>
            {healReport && (
              <div style={{ marginTop: 10 }}>
                {healReport.map((st: any, i: number) => (
                  <div key={i} className="sdrow" style={{ fontSize: 11.5, padding: "3px 0" }}>
                    <span className="bkdot" style={{
                      background: st.fixed ? "var(--amber)" : "var(--green)" }} />
                    <span>{st.step}</span>
                    <span className="ta" style={{ marginLeft: "auto" }}>
                      {String(st.found)}{st.fixed ? ` · fixed ${st.fixed}` : ""}{st.note ? ` · ${st.note}` : ""}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {err && <div style={{ color: "#e5484d", fontSize: 12 }}>{err}</div>}
        </div>
      </div>
    </div>
  );
}

const gb2 = (b: number) => `${(b / 1e9).toFixed(2)} GB`;

export function SyncWizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState<1 | 2>(1);
  const [targets, setTargets] = useState<any[]>([]);
  const [vol, setVol] = useState<string>("");
  const [libPls, setLibPls] = useState<any[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [plan, setPlan] = useState<any | null>(null);
  const [syncState, setSyncState] = useState<Record<string, { total: number; on_stick: number }>>({});

  const loadTargets = (keepSelection = false) =>
    fetch("/api/usb/wizard/targets").then(r => r.json()).then(d => {
      setTargets(d.rows);
      if (keepSelection) return;
      const stick = d.rows.find((t: any) => t.has_export) ?? d.rows[0];
      if (stick) setVol(stick.volume);
    });

  // Guard against the engine serving code older than what is on disk. The
  // server runs with --reload so this should never fire, but a sync from a
  // stale process is invisible on the resulting stick and has cost real
  // hardware test cycles, so it is worth saying out loud.
  const [build, setBuild] = useState<any>(null);
  useEffect(() => {
    loadTargets();
    fetch("/api/playlists").then(r => r.json()).then(d => setLibPls(d.rows));
    fetch("/api/version").then(r => r.json()).then(setBuild).catch(() => {});
  }, []);

  // A drive rekordbox has never prepared has no player settings and no
  // library. Syncing onto it would produce a device unlike anything the
  // players or rekordbox would have made, so we offer to initialize first.
  // A drive rekordbox already initialized reports "ready" and is left alone.
  const [initBusy, setInitBusy] = useState(false);
  const [initErr, setInitErr] = useState<string | null>(null);
  const initialize = () => {
    setInitBusy(true); setInitErr(null);
    fetch("/api/usb/initialize", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ volume: vol }),
    }).then(async r => {
      if (!r.ok) setInitErr((await r.json()).detail ?? "could not initialize");
      else await loadTargets(true);
    }).finally(() => setInitBusy(false));
  };

  // sync state follows the SELECTED target volume; fully-synced playlists on
  // that volume start checked (selection = desired stick state). Switching
  // targets swaps the auto-checked set without touching manual picks.
  const autoRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!vol) return;
    let stale = false;
    fetch(`/api/usb/wizard/state?volume=${encodeURIComponent(vol)}`)
      .then(r => r.json()).then(d => {
        if (stale) return;
        setSyncState(d.mounted ? (d.playlists ?? {}) : {});
        const full = new Set<string>(d.mounted
          ? Object.entries(d.playlists as Record<string, any>)
              .filter(([, v]) => v.total > 0 && v.on_stick === v.total)
              .map(([k]) => k)
          : []);
        setPicked(prev => {
          const n = new Set(prev);
          autoRef.current.forEach(k => { if (!full.has(k)) n.delete(k); });
          full.forEach(k => n.add(k));
          return n;
        });
        autoRef.current = full;
      });
    return () => { stale = true; };
  }, [vol]);

  useEffect(() => {
    if (picked.size === 0) { setPlan(null); return; }
    const t = setTimeout(() =>
      fetch("/api/usb/wizard/plan", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ playlist_ids: [...picked] }),
      }).then(r => r.json()).then(setPlan), 300);
    return () => clearTimeout(t);
  }, [picked]);



  const target = targets.find(t => t.volume === vol);
  const fits = plan && target && plan.total_bytes < target.free_bytes;
  const device = target?.device;
  const deviceReady = !device || device.can_sync;
  const toggle = (id: string) =>
    setPicked(s2 => { const n = new Set(s2); n.has(id) ? n.delete(id) : n.add(id); return n; });

  // Folders start collapsed. The library has hundreds of playlists nested
  // several deep, and opening the wizard onto all of them at once buries the
  // handful you came to pick.
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const seededClosed = useRef(false);
  useEffect(() => {
    if (seededClosed.current || libPls.length === 0) return;
    seededClosed.current = true;
    setClosed(new Set(libPls.filter(x => x.kind === "folder").map(x => x.id)));
  }, [libPls]);
  const kidsOf = (parent: string | null) =>
    libPls.filter(x => x.parent_id === parent).sort((a, b) => a.seq - b.seq);
  const descendants = (id: string): string[] => {
    const out: string[] = [];
    for (const k of kidsOf(id)) {
      if (k.kind === "folder") out.push(...descendants(k.id));
      else out.push(k.id);
    }
    return out;
  };
  const toggleFolderPick = (id: string) => {
    const ds = descendants(id);
    setPicked(s2 => {
      const n = new Set(s2);
      const allIn = ds.length > 0 && ds.every(d => n.has(d));
      ds.forEach(d => (allIn ? n.delete(d) : n.add(d)));
      return n;
    });
  };

  const renderNode = (p: any, depth: number): React.ReactNode => {
    if (p.kind !== "folder") {
      const st = syncState[p.id];
      const badge = !st || st.on_stick === 0 ? null
        : st.on_stick === st.total
          ? <span className="wizsync full" title="fully on stick">✓</span>
          : <span className="wizsync part" title="partially on stick">{st.on_stick}/{st.total}</span>;
      return (
        <label key={p.id} className="wizpl" style={{ paddingLeft: 8 + depth * 16 }}>
          <input type="checkbox" checked={picked.has(p.id)} onChange={() => toggle(p.id)} />
          <span className="nm">{p.name}</span>{badge}<span className="n">{p.n}</span>
        </label>
      );
    }
    const ds = descendants(p.id);
    const nPicked = ds.filter(d => picked.has(d)).length;
    const isOpen = !closed.has(p.id);
    return (
      <div key={p.id}>
        <div className="wizpl wizfolder" style={{ paddingLeft: 8 + depth * 16 }}>
          <input type="checkbox" checked={ds.length > 0 && nPicked === ds.length}
            ref={el => { if (el) el.indeterminate = nPicked > 0 && nPicked < ds.length; }}
            onChange={() => toggleFolderPick(p.id)} />
          <button className="wizcaret" onClick={() =>
            setClosed(s2 => { const n = new Set(s2); n.has(p.id) ? n.delete(p.id) : n.add(p.id); return n; })}>
            {isOpen ? "▾" : "▸"} {p.name}
          </button>
          <span className="n">{nPicked > 0 ? `${nPicked}/${ds.length}` : ds.length}</span>
        </div>
        {isOpen && kidsOf(p.id).map(k => renderNode(k, depth + 1))}
      </div>
    );
  };

  const [startErr, setStartErr] = useState<string | null>(null);
  const start = () =>
    fetch("/api/usb/wizard/start", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ playlist_ids: [...picked], volume: vol }),
    }).then(async r => {
      if (r.ok) { setStartErr(null); onClose(); }   // sync panel takes over
      else setStartErr((await r.json()).detail ?? "could not start");
    });

  return (
    <div className="wizov" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="wiz">
        <div className="wizhd">
          <span className="vtitle">USB sync wizard</span>
          <span className="ta" style={{ fontSize: 11.5 }}>step {step} of 2</span>
          <button className="bkbtn" style={{ marginLeft: "auto" }} onClick={onClose}>Close</button>
        </div>

        {step === 1 && (<>
          <div className="wizrow">
            <span className="lbl">Target</span>
            <select className="wizsel" value={vol} onChange={e => setVol(e.target.value)}>
              {targets.map(t => (
                <option key={t.volume} value={t.volume}>
                  {t.volume} — {gb2(t.free_bytes)} free{t.has_export ? " · has export" : ""}
                </option>
              ))}
            </select>
          </div>
          {target?.link_mode && (
            <div className="wizinit wizlink" data-testid="link-mode">
              <div className="wizinit-h">Experimental: audio will be linked, not copied</div>
              <div className="wizinit-n">
                This drive already holds a mirror of your library under{" "}
                <code>{target.link_mode.mirror}/</code>. Tracks are hard-linked
                into <code>/Contents</code> instead of copied, so the sync
                takes seconds and adds no audio to the drive.
              </div>
              <div className="wizinit-n">
                A hard link is a second name for the same file, not a shortcut,
                so players read it as an ordinary track. It does mean the two
                names share one copy of the bytes. Nothing here writes to audio,
                but deleting the mirror would take the linked track with it.
              </div>
            </div>
          )}
          {build?.stale && (
            <div className="wizinit" style={{ borderColor: "#e5484d" }} data-testid="stale-engine">
              <div className="wizinit-h">The engine is running older code than what is on disk</div>
              <div className="wizinit-n">
                Anything you sync now comes from the older build. Restart it
                with <code>./run.sh restart</code> before syncing.
              </div>
            </div>
          )}
          {device && device.state !== "ready" && (
            <div className="wizinit" data-testid="device-init">
              <div className="wizinit-h">
                {device.state === "empty" && "This drive has not been prepared for Pioneer players"}
                {device.state === "incomplete" && "This drive is missing files a prepared device has"}
                {device.state === "unsupported" && "This drive cannot be read by Pioneer players"}
              </div>
              {device.notes?.map((n: string, i: number) => (
                <div key={i} className="wizinit-n">{n}</div>
              ))}
              {device.missing?.length > 0 && (
                <div className="wizinit-n">
                  missing: {device.missing.slice(0, 6).join(", ")}
                  {device.missing.length > 6 ? ` and ${device.missing.length - 6} more` : ""}
                </div>
              )}
              {device.can_initialize && (
                <button className="syncbtn" disabled={initBusy} onClick={initialize}
                        data-testid="device-init-go">
                  {initBusy ? "Initializing…" : "Initialize this drive"}
                </button>
              )}
              {initErr && <div className="wizinit-n" style={{ color: "#e5484d" }}>{initErr}</div>}
            </div>
          )}
          <div className="lbl" style={{ margin: "10px 0 6px" }}>Playlists to sync</div>
          <div className="wizlist">
            {kidsOf(null).map(p => renderNode(p, 0))}
          </div>
          <div className="ta" style={{ fontSize: 10.5, margin: "6px 0 0" }}>
            checked = what the stick will contain after sync · ✓ already on stick · n/m partial
          </div>
          <div className="wizfoot">
            {plan ? (
              <span style={{ fontSize: 12.5, color: fits ? "var(--ink2)" : "#e5484d" }}>
                {plan.n_tracks.toLocaleString()} tracks · {gb2(plan.total_bytes)}
                {target ? ` of ${gb2(target.free_bytes)} free` : ""}
                {plan.missing_files ? ` · ${plan.missing_files} files missing on disk` : ""}
                {!fits && " — doesn't fit, trim the selection"}
              </span>
            ) : <span className="ta">pick playlists to size the sync</span>}
            <button className="wizgo" disabled={!plan || !fits || picked.size === 0 || !deviceReady}
              onClick={() => setStep(2)}>Review →</button>
          </div>
        </>)}

        {step === 2 && plan && (<>
          <div className="wizreview">
            <div className="kv"><span>target</span><b>/Volumes/{vol}</b></div>
            <div className="kv"><span>tracks</span><b>{plan.n_tracks.toLocaleString()} · {gb2(plan.total_bytes)}</b></div>
            <div className="kv"><span>playlists</span><b>{plan.playlists.map((p: any) => p.name).join(", ")}</b></div>
            <div className="wizwrites">
              <div>✓ audio files → /Contents/&lt;artist&gt;/</div>
              <div>✓ waveforms, beatgrids &amp; cues → /PIONEER/USBANLZ (from rekordbox's own analysis)</div>
              <div>✓ exportLibrary.db — playlists, My Tags, keys, comments (CDJ-3000 / AZ / OPUS / rekordbox)</div>
              <div>✓ player settings from your current profile</div>

              <div>✓ export.pdb + exportExt.pdb — the XDJ-XZ database, written by our
                corpus-validated writer (12,171-row round-trip verified)</div>
              <div style={{ color: "var(--amber)" }}>▲ first load on the XZ is the real-world test —
                if the deck rejects it, nothing is lost: re-run rekordbox export as before</div>
            </div>
          </div>
          {startErr && <div style={{ color: "#e5484d", fontSize: 12 }}>{startErr}</div>}
          <div className="wizfoot">
            <button className="bkbtn" onClick={() => setStep(1)}>← Back</button>
            <button className="wizgo" onClick={start}>Start sync</button>
          </div>
        </>)}


      </div>
    </div>
  );
}
