import { useEffect, useRef, useState } from "react";
import { useSyncJobs, SyncJob as Job } from "./useSyncJobs";

/** Global right-side sync panel: every in-progress (and just-finished) sync,
 *  from any tab, streamed live over SSE. Peeks open when a job starts. */
export default function SyncDrawer() {
  const { jobs } = useSyncJobs();
  const [open, setOpen] = useState(false);
  const prevRunning = useRef(0);

  useEffect(() => {
    const running = jobs.filter(j => j.state === "running").length;
    if (running > prevRunning.current) setOpen(true);       // new sync → peek
    prevRunning.current = running;
  }, [jobs]);

  const running = jobs.filter(j => j.state === "running");
  if (jobs.length === 0) return null;

  if (!open) {
    return (
      <button className="syncfab" onClick={() => setOpen(true)}
        title="show syncs">
        <span className="bkdot" style={{
          background: running.length ? "var(--amber)" : "var(--green)" }} />
        {running.length
          ? `${running.length} sync${running.length > 1 ? "s" : ""} running`
          : "syncs done"}
      </button>
    );
  }

  return (
    <aside className="syncdrawer">
      <div className="sdhead">
        <span className="lbl">Syncs</span>
        <button className="bkbtn" style={{ marginLeft: "auto" }}
          onClick={() => setOpen(false)}>Hide</button>
      </div>
      {jobs.map(j => {
        const hbAge = j.heartbeat ? Math.round(Date.now() / 1000 - j.heartbeat) : 0;
        const pct = j.bytes_total ? (j.bytes_done / j.bytes_total) * 100 : 0;
        return (
          <div key={j.volume + j.started} className="sdjob">
            <div className="sdrow">
              <span className="bkdot" style={{
                background: j.state === "running" ? "var(--amber)"
                  : j.state === "done" ? "var(--green)" : "#e5484d" }} />
              <span className="sdvol">{j.volume}</span>
              <span className="ta" style={{ marginLeft: "auto", fontSize: 10.5 }}>
                {j.state === "running" ? j.step : j.state}</span>
              {j.state !== "running" && (
                <button className="bkbtn" title="dismiss"
                  style={{ padding: "0 6px", lineHeight: "16px" }}
                  onClick={() => fetch("/api/usb/wizard/dismiss", {
                    method: "POST", headers: { "content-type": "application/json" },
                    body: JSON.stringify({ volume: j.volume }) })}>✕</button>
              )}
            </div>
            {j.state === "running" && (<>
              <div className="pbar" style={{ margin: "6px 0" }}>
                <span style={{ width: `${pct}%` }} />
              </div>
              <div className="sdrow" style={{ fontSize: 10.5, color: "var(--muted)" }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {j.current || " "}</span>
                <span style={{ marginLeft: "auto", flexShrink: 0 }}>
                  {(j.bytes_done / 1e9).toFixed(2)}/{(j.bytes_total / 1e9).toFixed(2)} GB</span>
              </div>
              <div className="sdrow" style={{ gap: 6, marginTop: 6 }}>
                <button className="bkbtn" onClick={() =>
                  fetch("/api/usb/wizard/cancel", {
                    method: "POST", headers: { "content-type": "application/json" },
                    body: JSON.stringify({ volume: j.volume }) })}>Cancel</button>
                {hbAge > 20 && (
                  <button className="bkbtn" style={{ color: "#e5484d" }} onClick={() =>
                    fetch("/api/usb/wizard/kill", {
                      method: "POST", headers: { "content-type": "application/json" },
                      body: JSON.stringify({ volume: j.volume }) })}>
                    Kill (stale {hbAge}s)</button>
                )}
              </div>
            </>)}
            {j.state === "done" && j.summary && (
              <div className="ta" style={{ fontSize: 10.5, marginTop: 4 }}>{j.summary}</div>
            )}
            {j.error && (
              <div style={{ color: "#e5484d", fontSize: 10.5, marginTop: 4 }}>{j.error}</div>
            )}
          </div>
        );
      })}
    </aside>
  );
}
