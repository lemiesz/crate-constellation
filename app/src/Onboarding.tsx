import { useEffect, useState } from "react";

/** First-run walkthrough: what the app is, import the library, set up stick
 *  exports (capture the rekordbox template the writer clones), and go.
 *  Opens by itself only when the library is empty and onboarding was never
 *  finished; the rail's "Getting started" button reopens it any time. */

type Stick = { volume: string; usable: boolean; problem: string | null; tracks: number | null };
type Status = {
  completed: boolean; show: boolean;
  rekordbox: { master_db: boolean };
  library: { exists: boolean; tracks: number; playlists: number; imported_at: string | null; embeddings: number };
  extras: { embeddings: boolean; constellation: boolean };
  template: { ready: boolean; missing: string[]; source: string | null; captured_at: string | null;
              tracks: number | null; deck_confirmed: boolean };
  sticks: Stick[];
};

const STEPS = ["Welcome", "Your library", "Stick exports", "Ready"];

const post = (url: string, body: object = {}) =>
  fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    .then(async r => ({ ok: r.ok, data: await r.json().catch(() => ({})) }));

function Check({ ok, optional }: { ok: boolean; optional?: boolean }) {
  return <span className={`obcheck${ok ? " ok" : optional ? " opt" : ""}`}>{ok ? "✓" : optional ? "–" : "○"}</span>;
}

export default function Onboarding({ onClose, onGo, onImported }: {
  onClose: () => void;
  onImported: () => void;
  onGo: (view: "library" | "usb") => void;
}) {
  const [step, setStep] = useState(0);
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [replacing, setReplacing] = useState(false);

  const refresh = () => fetch("/api/onboarding/status").then(r => r.json()).then(setSt);
  useEffect(() => { refresh(); }, []);
  // on the stick step, watch for a drive being plugged in
  useEffect(() => {
    if (step !== 2) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [step]);
  useEffect(() => setMsg(null), [step]);

  const finish = (go?: "library" | "usb") => {
    post("/api/onboarding/complete", { completed: true }).finally(() => {
      onClose();
      if (go) onGo(go);
    });
  };

  const runImport = async () => {
    setBusy("import"); setMsg(null);
    const r = await post("/api/sync/import");
    setBusy(null);
    if (r.ok) onImported();
    setMsg(r.ok ? { ok: true, text: "Library imported." }
                : { ok: false, text: r.data.detail ?? "Import failed." });
    refresh();
  };

  const capture = async (volume: string) => {
    setBusy(volume); setMsg(null);
    const r = await post("/api/onboarding/capture-template", { volume, replace: replacing });
    setBusy(null);
    setMsg(r.ok ? { ok: true, text: (r.data.log ?? []).join("\n") }
                : { ok: false, text: r.data.detail ?? "Capture failed." });
    if (r.ok) setReplacing(false);
    refresh();
  };

  const confirmDeck = (confirmed: boolean) => {
    // flip the box now; the server write just records it
    setSt(s => s && { ...s, template: { ...s.template, deck_confirmed: confirmed } });
    post("/api/onboarding/deck-confirmed", { confirmed }).then(refresh);
  };

  const lib = st?.library;
  const tpl = st?.template;

  return (
    <div className="wizov obov">
      <div className="wiz ob">
        <div className="obsteps">
          {STEPS.map((s, i) => (
            <button key={s} className={`obstep${i === step ? " on" : i < step ? " done" : ""}`}
              onClick={() => setStep(i)}>
              <span className="obn">{i < step ? "✓" : i + 1}</span>{s}
            </button>
          ))}
          <button className="obskip" onClick={() => finish()} title="You can reopen this from the rail">
            Skip for now
          </button>
        </div>

        {step === 0 && (
          <div className="obbody">
            <svg width="44" height="44" viewBox="0 0 26 26" fill="none" className="oblogo">
              <line x1="6" y1="18" x2="13" y2="6" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
              <line x1="13" y1="6" x2="20" y2="15" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
              <line x1="6" y1="18" x2="20" y2="15" stroke="rgba(255,255,255,.3)" strokeWidth="1.2" />
              <circle cx="6" cy="18" r="2.6" fill="#fab219" />
              <circle cx="13" cy="6" r="2.6" fill="#3987e5" />
              <circle cx="20" cy="15" r="2.6" fill="#d55181" />
            </svg>
            <h2 className="obh">Welcome to Crate Constellation</h2>
            <p className="oblead">
              One place for your whole DJ library: find tracks fast, see how they relate, tag them,
              and build USB sticks your Pioneer decks can play, without opening rekordbox.
            </p>
            <div className="obtiles">
              <div className="obtile"><b>Library</b>Fuzzy search, your rekordbox playlists, rekordbox's own waveforms, double-click to play.</div>
              <div className="obtile"><b>Constellation</b>Every track on a sound map. Similar-sounding tracks sit together.</div>
              <div className="obtile"><b>Energy &amp; tags</b>A 1–10 energy scale and four My Tag banks, editable per track.</div>
              <div className="obtile"><b>USB sticks</b>Pick playlists, get a deck-ready stick with cues, artwork and Matching. Several at once.</div>
            </div>
            <div className="obsafe">
              <b>What it never does:</b> it opens rekordbox's database read-only, so your rekordbox library
              is never changed. It only writes to a drive when you start a sync to it.
            </div>
            <p className="obnote">Setup takes about five minutes: import your library, then optionally prepare stick exports.</p>
          </div>
        )}

        {step === 1 && (
          <div className="obbody">
            <h2 className="obh">Bring in your library</h2>
            <p className="oblead">
              The app keeps its own copy of your rekordbox library, so you can browse and edit freely.
              Importing reads rekordbox's database and never writes to it. It's safe with rekordbox open.
            </p>
            <div className="obrows">
              <div className="obrow"><Check ok={!!st?.rekordbox.master_db} />
                <div><b>rekordbox library found</b>
                  <span>{st?.rekordbox.master_db ? "~/Library/Pioneer/rekordbox/master.db"
                    : "Install rekordbox 6 or 7 and import your music into it first."}</span></div></div>
              <div className="obrow"><Check ok={(lib?.tracks ?? 0) > 0} />
                <div><b>Library imported</b>
                  <span>{lib && lib.tracks > 0
                    ? `${lib.tracks.toLocaleString()} tracks · ${lib.playlists.toLocaleString()} playlists · ${lib.imported_at} UTC`
                    : "Not yet. Takes a few seconds."}</span></div>
                {lib && lib.tracks === 0 && (
                  <button className="wizgo" disabled={!st?.rekordbox.master_db || !!busy} onClick={runImport}>
                    {busy === "import" ? "Importing…" : "Import from rekordbox"}
                  </button>
                )}
              </div>
              <div className="obrow"><Check ok={!!st?.extras.embeddings} optional />
                <div><div><b>Sounds-like neighbours</b> <em>optional</em></div>
                  <span>{st?.extras.embeddings ? `${lib?.embeddings.toLocaleString()} tracks analysed`
                    : "Needs audio embeddings from the library-management analysis pipeline. Everything else works without them."}</span></div></div>
              <div className="obrow"><Check ok={!!st?.extras.constellation} optional />
                <div><div><b>Constellation map</b> <em>optional</em></div>
                  <span>{st?.extras.constellation ? "Map data found"
                    : "Built from the same analysis pipeline. The map stays empty until it has run."}</span></div></div>
            </div>
            {lib && lib.tracks > 0 && (
              <p className="obnote">To pick up later changes from rekordbox, use <b>Sync → Re-import</b>. It shows
                which of your edits would be overwritten first.</p>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="obbody">
            <h2 className="obh">Set up stick exports</h2>
            <p className="oblead">
              Decks are strict about the files on a stick, so the app copies the structure of a real
              rekordbox export and fills in your tracks. You give it one small export to learn from, once.
            </p>

            {tpl?.ready && !replacing ? (
              <div className="obrows">
                <div className="obrow"><Check ok />
                  <div><b>Stick exports are ready</b>
                    <span>{tpl.source ? `Template from ${tpl.source}, captured ${tpl.captured_at}` : "Template installed"}</span></div>
                  <button className="syncbtn" onClick={() => setReplacing(true)}>Replace…</button>
                </div>
                <div className="obrow"><Check ok={tpl.deck_confirmed} optional />
                  <div><div><b>Confirmed on a deck</b> <em>recommended</em></div>
                    <span>Put the stick you exported from rekordbox into your deck and play a track. If it plays,
                      your deck accepts this format.</span></div>
                  <label className="obtick">
                    <input type="checkbox" checked={tpl.deck_confirmed}
                      onChange={e => confirmDeck(e.target.checked)} /> It played
                  </label>
                </div>
              </div>
            ) : (
              <ol className="obscript">
                <li><b>Plug in a USB stick you can erase.</b> Any size; a small one is fine. This is only
                  for the template. Your real sticks come later.</li>
                <li><b>Export one small playlist from rekordbox.</b> Open rekordbox → <i>Export</i> mode →
                  select the stick under <i>Devices</i> → drag a playlist with 3–5 tracks onto it.
                  Wait for the export to finish.</li>
                <li><b>Leave it plugged in, then pick it below.</b> The app only reads from the stick.
                  {tpl?.ready && " Your current template is kept as a backup."}
                  <div className="obsticks">
                    {(st?.sticks ?? []).length === 0 && (
                      <div className="obwait"><span className="obpulse" /> Waiting for a stick with a rekordbox export…</div>
                    )}
                    {st?.sticks.map(s => (
                      <div key={s.volume} className="obstick">
                        <div><b>{s.volume}</b>
                          <span>{s.usable ? `${s.tracks ?? "?"} track(s) exported by rekordbox` : s.problem}</span></div>
                        <button className="wizgo" disabled={!s.usable || !!busy} onClick={() => capture(s.volume)}>
                          {busy === s.volume ? "Copying…" : "Use this stick"}
                        </button>
                      </div>
                    ))}
                  </div>
                </li>
                <li><b>Optional: check it on your deck.</b> Play a track from that same stick. Once it plays,
                  sticks the app writes will use the same format.</li>
              </ol>
            )}
            {replacing && <button className="obskip" onClick={() => setReplacing(false)}>Keep the current template</button>}
            {!tpl?.ready && (
              <p className="obnote">You can skip this. Everything except writing sticks works without it, and the
                sync wizard will point you back here.</p>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="obbody">
            <h2 className="obh">You're set</h2>
            <div className="obrows">
              <div className="obrow"><Check ok={(lib?.tracks ?? 0) > 0} />
                <div><b>Library</b><span>{(lib?.tracks ?? 0) > 0 ? `${lib!.tracks.toLocaleString()} tracks`
                  : "Not imported yet. Go back a step, or use Sync → Re-import later."}</span></div></div>
              <div className="obrow"><Check ok={!!tpl?.ready} />
                <div><b>Stick exports</b><span>{tpl?.ready ? "Ready" : "Not set up. Reopen Getting started from the rail any time."}</span></div></div>
            </div>
            <div className="obtiles">
              <div className="obtile"><b>Search</b>Type loosely: <code>jayz</code> finds Jay-Z.</div>
              <div className="obtile"><b>Play</b>Double-click any track. Click the waveform to seek.</div>
              <div className="obtile"><b>Tag</b>Select a track, then set energy and tags in the right-hand pane.</div>
              <div className="obtile"><b>Build a stick</b>USB → Sync wizard → tick playlists → Start.</div>
            </div>
          </div>
        )}

        {msg && <pre className={`oblog${msg.ok ? "" : " err"}`}>{msg.text}</pre>}

        <div className="wizfoot">
          <button className="obskip" disabled={step === 0} onClick={() => setStep(s => s - 1)}>← Back</button>
          {step < 3 ? (
            <button className="wizgo" onClick={() => setStep(s => s + 1)}>
              {step === 0 ? "Get started" : step === 2 && !tpl?.ready ? "Skip this step" : "Next →"}
            </button>
          ) : (
            <div className="wizrow">
              {tpl?.ready && <button className="syncbtn obalt" onClick={() => finish("usb")}>Build a stick</button>}
              <button className="wizgo" onClick={() => finish("library")}>Open my library</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
