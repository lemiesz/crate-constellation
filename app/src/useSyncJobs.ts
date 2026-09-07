import { useEffect, useState } from "react";

export type SyncJob = {
  volume: string; state: string; step: string; done: number; total: number;
  bytes_done: number; bytes_total: number; current: string; heartbeat: number;
  started: number; summary?: string; error?: string;
};

// One shared EventSource per page; components subscribe to the latest state.
// Falls back to polling if the stream can't connect.
type State = { jobs: SyncJob[]; art?: any };
let latest: State = { jobs: [] };
const subs = new Set<(s: State) => void>();
let started = false;
let pollTimer: ReturnType<typeof setInterval> | null = null;

function broadcast(s: State) {
  latest = s;
  subs.forEach(fn => fn(s));
}

function startStream() {
  if (started) return;
  started = true;
  try {
    const es = new EventSource("/api/events");
    es.onmessage = e => {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      try { broadcast(JSON.parse(e.data)); } catch {}
    };
    es.onerror = () => {
      // EventSource auto-reconnects; poll meanwhile so state never goes stale
      if (!pollTimer) {
        pollTimer = setInterval(() =>
          fetch("/api/usb/wizard/progress").then(r => r.json())
            .then(d => broadcast({ jobs: d.jobs ?? [] })).catch(() => {}), 2000);
      }
    };
  } catch {
    pollTimer = setInterval(() =>
      fetch("/api/usb/wizard/progress").then(r => r.json())
        .then(d => broadcast({ jobs: d.jobs ?? [] })).catch(() => {}), 2000);
  }
}

export function useSyncJobs(): State {
  const [state, setState] = useState<State>(latest);
  useEffect(() => {
    startStream();
    subs.add(setState);
    setState(latest);
    return () => { subs.delete(setState); };
  }, []);
  return state;
}
